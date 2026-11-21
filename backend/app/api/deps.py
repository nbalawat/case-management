"""Shared request dependencies and response shapes."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..cases.service import definition, stage_due
from ..domain.models import Case, now
from ..domain.personas import QUEUES, Persona, get_persona


def persona(x_persona: str | None = Header(default=None)) -> Persona:
    return get_persona(x_persona)


def actor(p: Persona = Depends(persona)) -> Persona:
    """A persona allowed to change something. The auditor is read-only everywhere."""
    if p.read_only:
        raise HTTPException(403, f"{p.name} has read-only access")
    return p


def require(permission: str):
    def dep(p: Persona = Depends(actor)) -> Persona:
        if not p.can(permission):
            raise HTTPException(403, f"{p.title} cannot do this. It needs the '{permission.replace('_', ' ')}' permission.")
        return p
    return dep


def get_case_or_404(s: Session, case_id: str) -> Case:
    c = s.get(Case, case_id)
    if c is None:
        raise HTTPException(404, "Case not found")
    return c


def case_row(c: Case) -> dict:
    ct = definition(c)
    due = stage_due(c, ct)
    return {"id": c.id, "case_type": c.case_type, "type_title": ct["title"], "domain": ct.get("domain", ""), "state": c.state,
            "state_label": ct["states"][c.state]["label"], "state_kind": ct["states"][c.state].get("kind", "open"),
            "open": c.closed is None, "priority": c.priority, "owner": c.owner,
            "queue": c.queue, "queue_label": QUEUES.get(c.queue or "", ""),
            "party_id": c.party_id, "party_name": c.party.name, "created": c.created.isoformat(),
            "sla_due": c.sla_due.isoformat() if c.sla_due else None, "sla_clock": ct.get("sla", {}).get("clock"),
            "stage_entered": c.state_entered.isoformat(), "stage_due": due.isoformat() if due else None,
            "stage_breached": bool(due and due < now()), "parent_id": c.parent_id,
            "disposition": c.disposition, "agent_recommendation": c.agent_recommendation}
