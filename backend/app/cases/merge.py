"""Two open cases about the same customer and the same kind of concern become one; and the reverse two ways.

Merge folds one case into another. Undo puts a merge back exactly as it was, as long as the surviving case is still
open. Split moves some of a case's alerts into a new case of the same kind, when one case turns out to be two matters.
Every step is recorded on both cases, so either trail tells the whole story.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..alerts.hub import PRIORITY_ORDER
from ..domain.models import Alert, Case, CaseEvent, InfoRequest, now
from ..domain.personas import Persona
from ..visibility import system_work
from .service import MERGED, TransitionError, record


@system_work
def merge(s: Session, source: Case, target: Case, persona: Persona, note: str = "", ts: datetime | None = None) -> Case:
    """Fold `source` into `target`: its alerts and open requests move across, and it closes pointing at the survivor."""
    ts = ts or now()
    if persona.read_only or not persona.can("work"):
        raise TransitionError("Merging cases needs someone who works cases")
    if source.id == target.id:
        raise TransitionError("A case cannot be merged into itself")
    if source.closed is not None or target.closed is not None:
        raise TransitionError("Both cases must be open")
    if source.party_id != target.party_id or source.case_type != target.case_type:
        raise TransitionError("Only cases of the same type about the same customer can be merged")
    if not (persona.can("reassign") or target.owner == persona.id):
        raise TransitionError("Merging needs the owner of the surviving case or a team lead")
    if not note.strip():
        raise TransitionError("Say why these are one matter")
    moved = [a.id for a in s.scalars(select(Alert).where(Alert.case_id == source.id))]
    requests = [r.id for r in s.scalars(select(InfoRequest).where(InfoRequest.case_id == source.id))]
    before = {"owner": source.owner, "priority": target.priority, "sla_due": target.sla_due.isoformat() if target.sla_due else None}
    s.execute(update(Alert).where(Alert.case_id == source.id).values(case_id=target.id))
    s.execute(update(InfoRequest).where(InfoRequest.case_id == source.id).values(case_id=target.id))
    if PRIORITY_ORDER.index(source.priority) > PRIORITY_ORDER.index(target.priority):
        target.priority = source.priority
    if source.sla_due and (target.sla_due is None or source.sla_due < target.sla_due):
        target.sla_due = source.sla_due  # the earlier deadline still binds
    source.closed, source.disposition, source.owner = ts, MERGED, None
    source.data = dict(source.data or {}) | {"merged_into": target.id}
    target.data = dict(target.data or {}) | {"merged_from": [*(target.data or {}).get("merged_from", []), source.id]}
    after = {"priority": target.priority, "sla_due": target.sla_due.isoformat() if target.sla_due else None}
    record(s, source.id, persona.id, "human", "case.merged_into", {"into": target.id, "note": note, "alerts": moved, "requests": requests,
                                                                  "before": before, "after": after}, ts=ts)
    record(s, target.id, persona.id, "human", "case.merged_from", {"from": source.id, "note": note, "alerts": moved}, ts=ts)
    s.flush()
    return target


def _may_rework(persona: Persona, owner: str | None, what: str) -> None:
    if persona.read_only or not persona.can("work"):
        raise TransitionError(f"{what} needs someone who works cases")
    if not (persona.can("reassign") or owner == persona.id):
        raise TransitionError(f"{what} needs the owner of the case or a team lead")


@system_work
def unmerge(s: Session, source: Case, persona: Persona, note: str, ts: datetime | None = None) -> Case:
    """Undo a merge: `source` reopens with the alerts and requests it brought, and the survivor gets back what it had.

    Anything added to the survivor since the merge stays with the survivor. A priority or deadline someone changed
    after the merge is left as they set it.
    """
    ts = ts or now()
    into = (source.data or {}).get("merged_into")
    if source.disposition != MERGED or not into:
        raise TransitionError("This case was not merged into another")
    target = s.scalars(select(Case).where(Case.id == into)).first()
    if target is None or target.closed is not None:
        raise TransitionError("The case it was merged into has closed; open a new case instead")
    _may_rework(persona, target.owner, "Undoing a merge")
    if not note.strip():
        raise TransitionError("Say why these are separate matters after all")
    ev = s.scalars(select(CaseEvent).where(CaseEvent.case_id == source.id, CaseEvent.kind == "case.merged_into")
                   .order_by(CaseEvent.ts.desc(), CaseEvent.id.desc())).first()
    done = (ev.payload if ev else {}) or {}
    alerts = [a for a in done.get("alerts", []) if a]
    requests = [r for r in done.get("requests", []) if r]
    if alerts:
        s.execute(update(Alert).where(Alert.id.in_(alerts), Alert.case_id == target.id).values(case_id=source.id))
    if requests:
        s.execute(update(InfoRequest).where(InfoRequest.id.in_(requests), InfoRequest.case_id == target.id).values(case_id=source.id))
    before, after = done.get("before") or {}, done.get("after") or {}
    if before and target.priority == after.get("priority"):
        target.priority = before["priority"]
    if before and (target.sla_due.isoformat() if target.sla_due else None) == after.get("sla_due"):
        target.sla_due = datetime.fromisoformat(before["sla_due"]) if before.get("sla_due") else None
    source.closed, source.disposition, source.owner = None, None, before.get("owner")
    source.data = {k: v for k, v in (source.data or {}).items() if k != "merged_into"}
    target.data = dict(target.data or {}) | {"merged_from": [x for x in (target.data or {}).get("merged_from", []) if x != source.id]}
    record(s, source.id, persona.id, "human", "case.unmerged", {"from": target.id, "note": note, "alerts": alerts}, ts=ts)
    record(s, target.id, persona.id, "human", "case.unmerged_from", {"case": source.id, "note": note, "alerts": alerts}, ts=ts)
    s.flush()
    return source


@system_work
def split(s: Session, case: Case, alert_ids: list[str], persona: Persona, note: str, ts: datetime | None = None) -> Case:
    """Move some of a case's alerts into a new case of the same type about the same customer, and return the new case."""
    from .case_types import load_case_types
    from .service import create_case

    ts = ts or now()
    if case.closed is not None:
        raise TransitionError("Only an open case can be split")
    _may_rework(persona, case.owner, "Splitting a case")
    if not note.strip():
        raise TransitionError("Say why these alerts are a separate matter")
    mine = {a.id: a for a in s.scalars(select(Alert).where(Alert.case_id == case.id))}
    chosen = sorted(set(alert_ids))
    if not chosen or any(a not in mine for a in chosen):
        raise TransitionError("Choose alerts that belong to this case")
    if len(chosen) == len(mine):
        raise TransitionError("Leave at least one alert on this case; to move them all, reassign the case instead")
    definition = load_case_types(case.tenant_id).get(case.case_type)
    if definition is None:
        raise TransitionError(f"The case type {case.case_type} is no longer published")
    new = create_case(s, definition, case.party_id, case.priority, created=ts, parent_id=case.parent_id, actor=persona.id)
    new.owner = case.owner
    if case.restricted:  # what was restricted stays restricted
        new.restricted, new.restricted_reason = True, case.restricted_reason
    s.flush()
    s.execute(update(Alert).where(Alert.id.in_(chosen)).values(case_id=new.id))
    new.data = dict(new.data or {}) | {"split_from": case.id}
    case.data = dict(case.data or {}) | {"split_into": [*(case.data or {}).get("split_into", []), new.id]}
    record(s, case.id, persona.id, "human", "case.split_into", {"into": new.id, "note": note, "alerts": chosen}, ts=ts)
    record(s, new.id, persona.id, "human", "case.split_from", {"from": case.id, "note": note, "alerts": chosen}, ts=ts)
    s.flush()
    return new
