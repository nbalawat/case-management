"""Case core: creation, guarded state transitions and the append-only audit trail."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..domain.models import Case, CaseEvent, Filing, new_id, now
from ..domain.personas import Persona
from .case_types import load_case_types


class TransitionError(Exception):
    """A requested transition broke a rule in the case type."""


def record(s: Session, case_id: str, actor: str, actor_kind: str, kind: str, payload: dict | None = None,
           ts: datetime | None = None) -> CaseEvent:
    ev = CaseEvent(case_id=case_id, actor=actor, actor_kind=actor_kind, kind=kind, payload=payload or {}, ts=ts or now())
    s.add(ev)
    return ev


def create_case(s: Session, case_type: dict, party_id: str, priority: str, created: datetime | None = None) -> Case:
    created = created or now()
    c = Case(id=new_id("case"), case_type=case_type["case_type"], type_version=case_type["version"],
             state=case_type["initial_state"], party_id=party_id, priority=priority, created=created,
             sla_due=created + timedelta(days=case_type.get("sla", {}).get("due_days", 30)))
    s.add(c)
    record(s, c.id, "alert-hub", "system", "case.created",
           {"case_type": c.case_type, "version": c.type_version, "state": c.state, "priority": priority}, ts=created)
    return c


def allowed_transitions(case: Case, persona: Persona) -> list[dict]:
    ct = load_case_types()[case.case_type]
    out = []
    for t in ct["transitions"]:
        if t["from"] != case.state:
            continue
        blocked = None
        if persona.role not in t["roles"]:
            blocked = f"Needs role: {', '.join(t['roles'])}"
        elif t.get("four_eyes") and case.drafted_by == persona.id:
            blocked = "Four-eyes: the drafter cannot approve"
        elif "narrative" in t.get("requires", []) and not case.narrative.strip():
            blocked = "Narrative required"
        out.append({"to": t["to"], "label": t["label"], "to_label": ct["states"][t["to"]]["label"],
                    "needs_note": "note" in t.get("requires", []), "blocked": blocked})
    return out


def transition(s: Session, case: Case, to: str, persona: Persona, note: str = "", ts: datetime | None = None) -> Case:
    """Move a case to a new state. Only humans call this; agents have no path to it."""
    ct = load_case_types()[case.case_type]
    t = next((x for x in ct["transitions"] if x["from"] == case.state and x["to"] == to), None)
    if t is None:
        raise TransitionError(f"No transition from {case.state} to {to}")
    if persona.role not in t["roles"]:
        raise TransitionError(f"Role {persona.role} cannot perform '{t['label']}'")
    if t.get("four_eyes") and case.drafted_by == persona.id:
        raise TransitionError("Four-eyes control: the drafter cannot approve their own work")
    req = t.get("requires", [])
    if "note" in req and not note.strip():
        raise TransitionError("A rationale note is required")
    if "narrative" in req and not case.narrative.strip():
        raise TransitionError("A narrative is required before this step")
    ts = ts or now()
    prev, case.state = case.state, to
    target = ct["states"][to]
    record(s, case.id, persona.id, "human", "case.state_changed",
           {"from": prev, "to": to, "label": t["label"], "note": note,
            "agent_recommendation": case.agent_recommendation}, ts=ts)
    if target.get("kind") == "closed":
        case.closed = ts
        case.disposition = target.get("disposition")
        if case.disposition == "sar_filed":
            _file_sar(s, case, persona, ts)
    return case


def save_narrative(s: Session, case: Case, text: str, persona: Persona, ts: datetime | None = None) -> None:
    case.narrative = text
    case.drafted_by = persona.id
    record(s, case.id, persona.id, "human", "narrative.saved", {"chars": len(text)}, ts=ts)


def _file_sar(s: Session, case: Case, persona: Persona, ts: datetime) -> Filing:
    """Demo filing: a JSON payload with a content hash. Not FinCEN BSA XML yet."""
    payload = {"case_id": case.id, "subject": {"id": case.party_id, "name": case.party.name},
               "narrative": case.narrative, "filed_at": ts.isoformat()}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    f = Filing(id=new_id("sar"), case_id=case.id, kind="SAR", filed_by=persona.id, filed_at=ts, sha256=digest, payload=payload)
    s.add(f)
    record(s, case.id, persona.id, "human", "sar.filed", {"filing_id": f.id, "sha256": digest}, ts=ts)
    return f
