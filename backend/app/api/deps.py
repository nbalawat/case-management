"""Shared request dependencies and response shapes.

Every request is for one client. In this demo the client and the person come from the `X-Tenant` and `X-Persona`
headers; a real deployment takes both from the sign-in token, never from the caller.
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..cases.service import definition, stage_due
from ..config import settings
from ..db import session_scope
from ..domain.models import Case, now
from ..domain.personas import Persona, policy_of
from ..tenants.registry import TenantRuntime, UnknownTenant, all_runtimes, runtime

# Demo platform staff. A real deployment keeps these in the provider's own identity system, apart from any client.
OPERATORS = {"olivia": {"id": "olivia", "name": "Olivia Grant", "title": "Platform operations"}}


def tenant(x_tenant: str | None = Header(default=None)) -> TenantRuntime:
    try:
        rt = runtime(x_tenant or settings.default_tenant)
    except UnknownTenant:
        if x_tenant or not all_runtimes():
            raise HTTPException(404, "Unknown client") from None
        rt = all_runtimes()[0]
    if rt.status != "live":
        raise HTTPException(403, f"{rt.name} is suspended")
    return rt


def get_session(rt: TenantRuntime = Depends(tenant)) -> Iterator[Session]:
    """A database session that can only see and write this client's rows."""
    with session_scope(rt.id) as s:
        yield s


def operator(x_operator: str | None = Header(default=None)) -> dict:
    if x_operator not in OPERATORS:
        raise HTTPException(403, "The operator console is for platform staff")
    return OPERATORS[x_operator]


def platform_session(_: dict = Depends(operator)) -> Iterator[Session]:
    with session_scope(platform=True) as s:
        yield s


def persona(rt: TenantRuntime = Depends(tenant), x_persona: str | None = Header(default=None)) -> Persona:
    if x_persona and x_persona not in rt.policy.people:
        raise HTTPException(403, f"'{x_persona}' is not a user of {rt.name}")
    return rt.policy.person(x_persona)


def actor(p: Persona = Depends(persona)) -> Persona:
    """A persona allowed to change something. Read-only roles are stopped here."""
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
            "queue": c.queue, "queue_label": policy_of(c.tenant_id).queues.get(c.queue or "", ""),
            "party_id": c.party_id, "party_name": c.party.name, "created": c.created.isoformat(),
            "sla_due": c.sla_due.isoformat() if c.sla_due else None, "sla_clock": ct.get("sla", {}).get("clock"),
            "stage_entered": c.state_entered.isoformat(), "stage_due": due.isoformat() if due else None,
            "stage_breached": bool(due and due < now()), "parent_id": c.parent_id,
            "disposition": c.disposition, "agent_recommendation": c.agent_recommendation}
