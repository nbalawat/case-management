"""The managed-service model over HTTP, from each of its three sides.

  * The provider's operator console: the staff directory, and engagements at every client (propose, end).
  * The client's Access page: what its policy allows, engagements waiting for approval, and everything provider
    staff have done here (approve, turn down, end).
  * A member of the provider's staff: the clients they are engaged at, to choose where to work.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..domain.models import AccessEvent, Engagement, ProviderStaff, Tenant, now
from ..domain.personas import Persona
from ..tenants import provider
from ..tenants.provider import EngagementError
from ..tenants.registry import TenantRuntime, UnknownTenant, all_runtimes, runtime
from .deps import get_session, operator, persona, platform_session, require, staff, tenant

router = APIRouter(prefix="/api")


def _expire_all(s: Session) -> None:
    for rt in all_runtimes():
        provider.expire(s, rt.id)


# --- the provider's side -------------------------------------------------------------------------------------------
@router.get("/platform/managed-service")
def overview(s: Session = Depends(platform_session)):
    """Every client's terms for the managed service, every engagement, and the staff who can be engaged."""
    _expire_all(s)
    members = {m.id: m for m in provider.directory(s)}
    rows = list(s.scalars(select(Engagement).order_by(Engagement.proposed.desc())))
    week = now() - timedelta(days=7)
    activity = dict(s.execute(select(AccessEvent.tenant_id, func.count()).where(AccessEvent.org == "provider", AccessEvent.ts >= week)
                              .group_by(AccessEvent.tenant_id)).all())
    clients = []
    for t in s.scalars(select(Tenant).order_by(Tenant.created, Tenant.id)):
        rt = runtime(t.id)
        mine = [e for e in rows if e.tenant_id == t.id]
        clients.append({"id": t.id, "name": t.name, "status": t.status, "deployment": t.deployment, "allows": provider.allowance(rt.policy),
                        "active": sum(1 for e in mine if provider.out(e, None)["live"]),
                        "waiting": sum(1 for e in mine if e.status == "proposed"), "actions_7d": activity.get(t.id, 0)})
    return {"staff": [provider.staff_out(m) | {"engagements": sum(1 for e in rows if e.staff_id == m.id and provider.out(e, m)["live"])}
                      for m in members.values()],
            "clients": clients,
            "engagements": [provider.out(e, members.get(e.staff_id), _rt(e.tenant_id)) for e in rows]}


def _rt(tenant_id: str) -> TenantRuntime | None:
    try:
        return runtime(tenant_id)
    except UnknownTenant:
        return None


class StaffIn(BaseModel):
    id: str
    name: str
    title: str = ""
    email: str = ""
    console: bool = False


@router.post("/platform/staff", status_code=201)
def add_staff(body: StaffIn, s: Session = Depends(platform_session)):
    try:
        return provider.staff_out(provider.add_staff(s, **body.model_dump()))
    except EngagementError as e:
        raise HTTPException(422, str(e)) from None


class EngagementIn(BaseModel):
    tenant: str
    staff_id: str
    roles: list[str]
    queues: list[str] = []
    days: int = Field(90, ge=1, le=366, description="How long the engagement lasts, from today")
    note: str = ""


@router.post("/platform/engagements", status_code=201)
def propose(body: EngagementIn, s: Session = Depends(platform_session), op: dict = Depends(operator)):
    try:
        rt = runtime(body.tenant)
    except UnknownTenant:
        raise HTTPException(404, "Unknown client") from None
    at = now()
    try:
        e = provider.propose(s, rt.id, body.staff_id, body.roles, body.queues, at + timedelta(days=body.days), op["id"], body.note, at)
    except EngagementError as err:
        raise HTTPException(422, str(err)) from None
    return provider.out(e, provider.staff(s, e.staff_id), rt)


class EndIn(BaseModel):
    reason: str


@router.post("/platform/engagements/{tenant_id}/{engagement_id}/end")
def provider_end(tenant_id: str, engagement_id: str, body: EndIn, s: Session = Depends(platform_session), op: dict = Depends(operator)):
    e = s.get(Engagement, engagement_id)
    if e is None or e.tenant_id != tenant_id:
        raise HTTPException(404, "No such engagement")
    try:
        provider.end(s, engagement_id, op["id"], "provider", body.reason)
    except EngagementError as err:
        raise HTTPException(422, str(err)) from None
    return provider.out(e, provider.staff(s, e.staff_id), _rt(tenant_id))


@router.get("/provider/me")
def my_engagements(member: dict = Depends(staff)):
    """The clients this member of staff may work at now, and any engagement still waiting for the client's approval."""
    with session_scope(platform=True) as s:
        _expire_all(s)
        rows = list(s.scalars(select(Engagement).where(Engagement.staff_id == member["id"], Engagement.status.in_(("active", "proposed")))
                              .order_by(Engagement.ends)))
        m = provider.staff(s, member["id"])
        return {"staff": member, "engagements": [provider.out(e, m, _rt(e.tenant_id)) for e in rows]}


# --- the client's side ---------------------------------------------------------------------------------------------
@router.get("/access/managed-service")
def client_view(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(persona)):
    """What this client allows the provider's staff, who is here, who is waiting, and what they have done."""
    if p.external or not (p.can("manage_access") or p.can("view_team") or p.can("certify_access")):
        raise HTTPException(403, "The managed-service record is for the client's administrators, managers and auditors")
    provider.expire(s, rt.id)
    rows = list(s.scalars(select(Engagement).order_by(Engagement.proposed.desc())))
    members = {m.id: m for m in s.scalars(select(ProviderStaff).where(ProviderStaff.id.in_({e.staff_id for e in rows})))} if rows else {}
    events = list(s.scalars(select(AccessEvent).order_by(AccessEvent.id.desc()).limit(200)))
    staff_names = {m.id: m.name for m in provider.directory(s)}  # operators act from the console under their own id
    names = staff_names | {provider.persona_id(k): v for k, v in staff_names.items()} | {pid: q.name for pid, q in rt.policy.people.items()}
    return {"allows": provider.allowance(rt.policy), "can_decide": p.can("manage_access") and not p.external,
            "engagements": [provider.out(e, members.get(e.staff_id), rt) for e in rows],
            "log": [{"id": ev.id, "ts": ev.ts.isoformat(), "actor": ev.actor, "actor_name": names.get(ev.actor, ev.actor), "org": ev.org,
                     "kind": ev.kind, "subject": ev.subject, "detail": ev.detail} for ev in events]}


class DecisionIn(BaseModel):
    reason: str = ""


def _decide(engagement_id: str, approve: bool, body: DecisionIn, s: Session, p: Persona, rt: TenantRuntime) -> dict:
    try:
        e = provider.decide(s, engagement_id, approve, p, body.reason)
    except EngagementError as err:
        raise HTTPException(422, str(err)) from None
    return provider.out(e, provider.staff(s, e.staff_id), rt)


@router.post("/access/engagements/{engagement_id}/approve")
def approve(engagement_id: str, body: DecisionIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant),
            p: Persona = Depends(require("manage_access"))):
    return _decide(engagement_id, True, body, s, p, rt)


@router.post("/access/engagements/{engagement_id}/reject")
def reject(engagement_id: str, body: DecisionIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant),
           p: Persona = Depends(require("manage_access"))):
    return _decide(engagement_id, False, body, s, p, rt)


@router.post("/access/engagements/{engagement_id}/end")
def client_end(engagement_id: str, body: EndIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant),
               p: Persona = Depends(require("manage_access"))):
    if p.external:
        raise HTTPException(403, "Ending an engagement from the client side is for the client's own people")
    try:
        e = provider.end(s, engagement_id, p.id, "client", body.reason)
    except EngagementError as err:
        raise HTTPException(422, str(err)) from None
    return provider.out(e, provider.staff(s, e.staff_id), rt)
