"""Two open cases about the same customer and the same kind of concern become one."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..alerts.hub import PRIORITY_ORDER
from ..domain.models import Alert, Case, InfoRequest, now
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
    s.execute(update(Alert).where(Alert.case_id == source.id).values(case_id=target.id))
    s.execute(update(InfoRequest).where(InfoRequest.case_id == source.id).values(case_id=target.id))
    if PRIORITY_ORDER.index(source.priority) > PRIORITY_ORDER.index(target.priority):
        target.priority = source.priority
    if source.sla_due and (target.sla_due is None or source.sla_due < target.sla_due):
        target.sla_due = source.sla_due  # the earlier deadline still binds
    source.closed, source.disposition, source.owner = ts, MERGED, None
    source.data = dict(source.data or {}) | {"merged_into": target.id}
    target.data = dict(target.data or {}) | {"merged_from": [*(target.data or {}).get("merged_from", []), source.id]}
    record(s, source.id, persona.id, "human", "case.merged_into", {"into": target.id, "note": note, "alerts": moved}, ts=ts)
    record(s, target.id, persona.id, "human", "case.merged_from", {"from": source.id, "note": note, "alerts": moved}, ts=ts)
    s.flush()
    return target
