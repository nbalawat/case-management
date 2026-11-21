"""Case core: creation, guarded state transitions, stage tasks and the append-only audit trail."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Case, CaseEvent, Filing, InfoRequest, Transaction, new_id, now
from ..domain.personas import Persona, queue_members
from ..tenancy import tenant_of
from . import conditions
from .case_types import get_definition
from ..visibility import system_work


class TransitionError(Exception):
    """A requested action broke a rule in the case type."""


# Called after a case reaches a closed state: (session, case, definition, actor, timestamp).
ON_CLOSED: list[Callable[[Session, Case, dict, Persona, datetime], None]] = []


def definition(case: Case) -> dict:
    return get_definition(case.tenant_id, case.case_type, case.type_version)


def record(s: Session, case_id: str, actor: str, actor_kind: str, kind: str, payload: dict | None = None,
           ts: datetime | None = None) -> CaseEvent:
    ev = CaseEvent(case_id=case_id, actor=actor, actor_kind=actor_kind, kind=kind, payload=payload or {}, ts=ts or now())
    s.add(ev)
    return ev


@system_work
def create_case(s: Session, case_type: dict, party_id: str, priority: str, created: datetime | None = None,
                parent_id: str | None = None, actor: str = "alert-hub") -> Case:
    created = created or now()
    initial = case_type["initial_state"]
    c = Case(id=new_id("case"), tenant_id=tenant_of(s), case_type=case_type["case_type"], type_version=case_type["version"], state=initial,
             party_id=party_id, priority=priority, created=created, state_entered=created, parent_id=parent_id,
             queue=case_type["states"][initial].get("queue"), data={},
             sla_due=created + timedelta(days=case_type.get("sla", {}).get("due_days", 30)))
    parent = s.get(Case, parent_id) if parent_id else None
    if parent is not None and parent.restricted:  # a case opened from a restricted case is restricted too
        c.restricted, c.restricted_reason = True, parent.restricted_reason
    s.add(c)
    record(s, c.id, actor, "system", "case.created",
           {"case_type": c.case_type, "version": c.type_version, "state": c.state, "priority": priority,
            "queue": c.queue, "parent_id": parent_id}, ts=created)
    return c


@system_work
def facts(s: Session, case: Case) -> dict:
    """The case facts that transition conditions may test."""
    alerts = list(s.scalars(select(Alert).where(Alert.case_id == case.id)))
    ids = {i for a in alerts for i in a.txn_ids}
    amount = s.scalar(select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(Transaction.id.in_(ids))) if ids else 0.0
    return {"alerted_amount": round(float(amount or 0), 2), "alerts": len(alerts), "risk_rating": case.party.risk_rating,
            "pep": bool(case.party.pep), "priority": case.priority, "agent_recommendation": case.agent_recommendation or ""}


# --- stage tasks -----------------------------------------------------------------
def stage_tasks(case: Case, defn: dict | None = None) -> list[dict]:
    defn = defn or definition(case)
    done = (case.data or {}).get("tasks", {}).get(case.state, {})
    return [{"id": t["id"], "label": t["label"], "required": bool(t.get("required", True)),
             "done": t["id"] in done, "done_by": done.get(t["id"], {}).get("by"), "done_at": done.get(t["id"], {}).get("at")}
            for t in defn["states"][case.state].get("tasks", [])]


@system_work
def set_task(s: Session, case: Case, task_id: str, persona: Persona, done: bool = True, ts: datetime | None = None) -> None:
    if case.closed is not None:
        raise TransitionError("The case is closed")
    if task_id not in {t["id"] for t in stage_tasks(case)}:
        raise TransitionError(f"No task '{task_id}' in stage {case.state}")
    ts = ts or now()
    data = json.loads(json.dumps(case.data or {}))
    stage = data.setdefault("tasks", {}).setdefault(case.state, {})
    if done:
        stage[task_id] = {"by": persona.id, "at": ts.isoformat()}
    else:
        stage.pop(task_id, None)
    case.data = data
    record(s, case.id, persona.id, "human", "task.completed" if done else "task.reopened", {"task": task_id, "stage": case.state}, ts=ts)


@system_work
def complete_stage_tasks(s: Session, case: Case, persona: Persona, ts: datetime | None = None) -> None:
    for t in stage_tasks(case):
        if not t["done"]:
            set_task(s, case, t["id"], persona, ts=ts)


# --- transitions -----------------------------------------------------------------
def _open_requests(s: Session, case: Case) -> int:
    return s.scalar(select(func.count()).select_from(InfoRequest).where(InfoRequest.case_id == case.id, InfoRequest.status == "open")) or 0


def _blocked(s: Session, case: Case, t: dict, persona: Persona, note: str | None) -> str | None:
    """Why this persona cannot take this transition now. `note=None` skips the note check (it is typed at click time)."""
    if not persona.holds(t["roles"]):
        return f"Needs role: {', '.join(r.replace('_', ' ') for r in t['roles'])}"
    if t.get("four_eyes"):
        if case.drafted_by == persona.id:
            return "Four-eyes: the drafter cannot approve their own work"
        if (case.data or {}).get("last_actor") == persona.id:
            return "Four-eyes: a second person must take this step"
    req = t.get("requires", [])
    if "tasks" in req:
        missing = [x["label"] for x in stage_tasks(case) if x["required"] and not x["done"]]
        if missing:
            return f"Finish required task{'s' if len(missing) > 1 else ''}: {'; '.join(missing)}"
    if "narrative" in req and not (case.narrative.strip() and case.drafted_by):
        return "A narrative saved by a person is required"
    if "open_request" in req and not _open_requests(s, case):
        return "Raise an information request first"
    if "requests_answered" in req and _open_requests(s, case):
        return "An information request is still open"
    if note is not None and "note" in req and not note.strip():
        return "A rationale note is required"
    return None


@system_work
def applicable_targets(s: Session, case: Case) -> set[str]:
    """The stages this case could move to next, whoever takes the step."""
    f = facts(s, case)
    return {t["to"] for t in definition(case)["transitions"] if t["from"] == case.state and conditions.evaluate(t.get("when"), f)}


@system_work
def allowed_transitions(s: Session, case: Case, persona: Persona) -> list[dict]:
    defn = definition(case)
    f = facts(s, case)
    out = []
    for t in defn["transitions"]:
        if t["from"] != case.state or not conditions.evaluate(t.get("when"), f):
            continue
        blocked = "Read-only access" if persona.read_only else _blocked(s, case, t, persona, None)
        out.append({"to": t["to"], "label": t["label"], "to_label": defn["states"][t["to"]]["label"],
                    "needs_note": "note" in t.get("requires", []), "blocked": blocked,
                    "when": conditions.describe(t.get("when")), "four_eyes": bool(t.get("four_eyes"))})
    return out


@system_work
def transition(s: Session, case: Case, to: str, persona: Persona, note: str = "", ts: datetime | None = None) -> Case:
    """Move a case to a new state. Only people call this; agents have no path to it."""
    defn = definition(case)
    f = facts(s, case)
    candidates = [x for x in defn["transitions"] if x["from"] == case.state and x["to"] == to]
    if not candidates:
        raise TransitionError(f"No transition from {case.state} to {to}")
    t = next((x for x in candidates if conditions.evaluate(x.get("when"), f)), None)
    if t is None:
        raise TransitionError(f"'{candidates[0]['label']}' does not apply to this case: {conditions.describe(candidates[0].get('when'))}")
    if persona.read_only:
        raise TransitionError("Read-only access")
    reason = _blocked(s, case, t, persona, note)
    if reason:
        raise TransitionError(reason)
    ts = ts or now()
    prev = case.state
    record(s, case.id, persona.id, "human", "case.state_changed",
           {"from": prev, "to": to, "label": t["label"], "note": note, "agent_recommendation": case.agent_recommendation}, ts=ts)
    enter_state(s, case, defn, to, ts, actor=persona.id)
    target = defn["states"][to]
    if target.get("kind") == "closed":
        case.closed = ts
        case.disposition = target.get("disposition")
        if case.disposition == "sar_filed":
            _file_sar(s, case, persona, ts)
        for hook in ON_CLOSED:
            hook(s, case, defn, persona, ts)
    return case


def enter_state(s: Session, case: Case, defn: dict, state: str, ts: datetime, actor: str) -> None:
    """Apply what a stage declares on entry: its queue, a fresh stage clock and, on a hand-off, an empty owner."""
    target = defn["states"][state]
    case.state, case.state_entered = state, ts
    data = dict(case.data or {})
    data["last_actor"] = actor
    data.pop("stage_breach", None)
    case.data = data
    queue = target.get("queue")
    if queue != case.queue:
        case.queue = queue
        if case.owner and queue and case.owner not in {p.id for p in queue_members(case.tenant_id, queue)}:
            record(s, case.id, "workflow", "system", "case.handed_off", {"from_owner": case.owner, "queue": queue}, ts=ts)
            case.owner = None


@system_work
def save_narrative(s: Session, case: Case, text: str, persona: Persona, ts: datetime | None = None) -> None:
    if persona.read_only:
        raise TransitionError("Read-only access")
    case.narrative = text
    case.drafted_by = persona.id
    record(s, case.id, persona.id, "human", "narrative.saved", {"chars": len(text)}, ts=ts)


def stage_due(case: Case, defn: dict | None = None) -> datetime | None:
    hours = (defn or definition(case))["states"][case.state].get("sla_hours")
    return None if hours is None or case.closed else case.state_entered + timedelta(hours=float(hours))


def _file_sar(s: Session, case: Case, persona: Persona, ts: datetime) -> Filing:
    """Demo filing: a JSON payload with a content hash. Not FinCEN BSA XML yet."""
    payload = {"case_id": case.id, "subject": {"id": case.party_id, "name": case.party.name},
               "narrative": case.narrative, "filed_at": ts.isoformat()}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    f = Filing(id=new_id("sar"), case_id=case.id, kind="SAR", filed_by=persona.id, filed_at=ts, sha256=digest, payload=payload)
    s.add(f)
    record(s, case.id, persona.id, "human", "sar.filed", {"filing_id": f.id, "sha256": digest}, ts=ts)
    return f
