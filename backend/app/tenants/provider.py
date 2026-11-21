"""The managed-service operating model: the platform provider's own staff working a client's cases.

Two directories meet here. The provider keeps its staff (`ProviderStaff`, a platform table); each client keeps its own
access policy. An *engagement* joins them: one member of staff, at one client, holding some of that client's roles and
queues until a date. The client's policy decides whether this is allowed at all (its `managed_service` section), which
roles the provider may be given, whether each engagement waits for the client's approval, and which steps a
provider maker needs a client checker for.

While an engagement is active its holder is an ordinary person in the client's runtime policy, with an id that starts
`ms.`, so queues, routing, four-eyes, visibility and every permission check apply to them unchanged. They are never
in the client's YAML and never hold a permission that controls access. When the engagement ends, by either side or by
the calendar, the person disappears from the policy and any case they owned goes back to its queue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.access import NEVER_DELEGATED, PROVIDER, PROVIDER_PREFIX, AccessPolicy, AccessPolicyError, Persona, make_persona
from ..domain.models import AccessEvent, Case, Engagement, ProviderStaff, new_id, now
from ..tenancy import is_platform_session, tenant_of
from .registry import TenantRuntime, runtime

STAFF_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


class EngagementError(ValueError):
    pass


def persona_id(staff_id: str) -> str:
    return PROVIDER_PREFIX + staff_id


def staff_id_of(pid: str | None) -> str | None:
    return pid[len(PROVIDER_PREFIX):] if pid and pid.startswith(PROVIDER_PREFIX) else None


def is_provider(pid: str | None) -> bool:
    return staff_id_of(pid) is not None


# --- the provider's staff directory ------------------------------------------------------------------------------
def staff(s: Session, sid: str | None) -> ProviderStaff | None:
    return s.get(ProviderStaff, sid) if sid else None


def directory(s: Session) -> list[ProviderStaff]:
    return list(s.scalars(select(ProviderStaff).order_by(ProviderStaff.name)))


def add_staff(s: Session, *, id: str, name: str, title: str = "", email: str = "", console: bool = False) -> ProviderStaff:
    if not is_platform_session(s):
        raise EngagementError("The staff directory is changed from the operator console")
    if not STAFF_ID.fullmatch(id or ""):
        raise EngagementError("Staff id must be 2 to 31 characters: lower-case letters, digits and hyphens, starting with a letter")
    if not (name or "").strip():
        raise EngagementError("A name is required")
    if s.get(ProviderStaff, id) is not None:
        raise EngagementError(f"There is already a member of staff with id '{id}'")
    row = ProviderStaff(id=id, name=name.strip()[:120], title=title.strip()[:120], email=email.strip()[:200], console=console)
    s.add(row)
    s.flush()
    return row


def bootstrap(s: Session, spec: str) -> ProviderStaff | None:
    """Make sure a fresh installation has one console operator. Does nothing once anyone is in the directory."""
    if not spec or s.scalars(select(ProviderStaff).limit(1)).first() is not None:
        return None
    sid, _, name = spec.partition(":")
    return add_staff(s, id=sid.strip(), name=(name or sid).strip(), title="Platform operations", console=True)


# --- engagements -------------------------------------------------------------------------------------------------
def _live(e: Engagement, at: datetime) -> bool:
    return e.status == "active" and e.starts <= at < e.ends


def staff_persona(policy: AccessPolicy, member: ProviderStaff, e: Engagement) -> Persona:
    ms = policy.managed_service
    provider = ms.provider if ms else "Provider"
    first = policy.roles[e.roles[0]]
    return make_persona(policy.roles, policy.queues, persona_id(member.id), member.name, list(e.roles), list(e.queues),
                        f"{first.title} · {provider}", f"{member.title or 'Managed-service team'} at {provider}, here until {e.ends:%d %b %Y}.",
                        org=PROVIDER, engagement=e.id)


def refresh(s: Session, rt: TenantRuntime, at: datetime | None = None) -> TenantRuntime:
    """Put exactly the live engagements' holders into the client's runtime policy."""
    at = at or now()
    rows = s.scalars(select(Engagement).where(Engagement.tenant_id == rt.id, Engagement.status == "active"))
    extra = {}
    for e in rows:
        member = staff(s, e.staff_id)
        if member is None or not member.active or not _live(e, at):
            continue
        try:
            extra[persona_id(member.id)] = staff_persona(rt.policy, member, e)
        except AccessPolicyError:
            continue  # the policy changed under it; publish ends such engagements, this only keeps the runtime safe
    rt.policy = rt.policy.with_people(extra)
    return rt


def check(policy: AccessPolicy, member: ProviderStaff | None, roles: list[str], queues: list[str], ends: datetime,
          at: datetime | None = None) -> None:
    """Whether this engagement could exist under this policy. Raises EngagementError with the reason."""
    at = at or now()
    ms = policy.managed_service
    if ms is None:
        raise EngagementError("This client's access policy does not allow the provider's staff to work its cases")
    if member is None:
        raise EngagementError("No member of staff with that id")
    if not member.active:
        raise EngagementError(f"{member.name} is no longer active")
    if not roles:
        raise EngagementError("Choose at least one role")
    outside = [r for r in roles if r not in ms.roles]
    if outside:
        raise EngagementError(f"The client allows the provider's staff only these roles: {', '.join(sorted(ms.roles))}. Not allowed: {', '.join(outside)}")
    kept = sorted({p for r in roles if r in policy.roles for p in policy.roles[r].permissions} & NEVER_DELEGATED)
    if kept:
        raise EngagementError(f"{', '.join(kept)} stays with the client's own people")
    if ends <= at:
        raise EngagementError("The end date must be in the future")
    if ends > at + timedelta(days=ms.max_days):
        raise EngagementError(f"This client allows engagements of at most {ms.max_days} days")
    try:
        make_persona(policy.roles, policy.queues, persona_id(member.id), member.name, roles, queues)
    except AccessPolicyError as e:
        raise EngagementError(str(e)) from None


def log(s: Session, tenant_id: str, actor: str, org: str, kind: str, subject: str = "", **detail) -> None:
    """Works in a client's session and in the operator console's platform session alike."""
    s.add(AccessEvent(tenant_id=tenant_id, actor=actor, org=org, kind=kind, subject=subject[:200], detail=detail))


def _get(s: Session, eid: str) -> Engagement:
    e = s.scalars(select(Engagement).where(Engagement.id == eid)).first()
    if e is None:
        raise EngagementError("No such engagement")
    return e


def propose(s: Session, tenant_id: str, staff_id: str, roles: list[str], queues: list[str], ends: datetime, by: str,
            note: str = "", at: datetime | None = None) -> Engagement:
    """The provider asks for a place for one of its staff. Starts at once if the client waives approval."""
    at = at or now()
    rt = runtime(tenant_id)
    member = staff(s, staff_id)
    check(rt.policy, member, roles, queues, ends, at)
    open_ = s.scalars(select(Engagement).where(Engagement.tenant_id == tenant_id, Engagement.staff_id == staff_id,
                                               Engagement.status.in_(("proposed", "active")))).first()
    if open_ is not None and (open_.status == "proposed" or _live(open_, at) or open_.starts > at):
        raise EngagementError(f"{member.name} already has an engagement at {rt.name} ({open_.status}); end it before proposing another")
    e = Engagement(id=new_id("eng"), tenant_id=tenant_id, staff_id=staff_id, roles=list(roles), queues=list(queues), starts=at, ends=ends,
                   note=note.strip()[:500], proposed_by=by, proposed=at)
    s.add(e)
    log(s, tenant_id, by, PROVIDER, "engagement.proposed", e.id, staff=staff_id, roles=list(roles), queues=list(queues), ends=ends.isoformat())
    if rt.policy.managed_service.approval == "none":
        e.status, e.decided_by, e.decided = "active", "policy", at
        log(s, tenant_id, "policy", "system", "engagement.started", e.id, staff=staff_id, reason="this client's policy needs no approval")
    s.flush()
    refresh(s, rt, at)
    return e


def decide(s: Session, eid: str, approve: bool, by: Persona, reason: str = "", at: datetime | None = None) -> Engagement:
    """The client approves or turns down a proposed engagement. Only the client's own people with manage_access."""
    at = at or now()
    if by.external or not by.can("manage_access"):
        raise EngagementError("Approving the provider's staff needs the 'manage access' permission at this client")
    e = _get(s, eid)
    if e.status != "proposed":
        raise EngagementError(f"This engagement is already {e.status}")
    rt = runtime(tenant_of(s))
    if approve:
        check(rt.policy, staff(s, e.staff_id), list(e.roles), list(e.queues), e.ends, at)
    e.status, e.decided_by, e.decided = ("active" if approve else "rejected"), by.id, at
    if not approve:
        e.end_reason = reason.strip()[:300]
    log(s, e.tenant_id, by.id, "client", "engagement.approved" if approve else "engagement.rejected", e.id, staff=e.staff_id, reason=reason.strip()[:300])
    s.flush()
    refresh(s, rt, at)
    return e


def end(s: Session, eid: str, by: str, org: str, reason: str, at: datetime | None = None) -> Engagement:
    """Either side ends an engagement. Cases the person owned go back to their queues."""
    at = at or now()
    if not reason.strip():
        raise EngagementError("Say why the engagement is ending")
    e = _get(s, eid)
    if e.status not in ("active", "proposed"):
        raise EngagementError(f"This engagement is already {e.status}")
    _close(s, e, by, org, reason.strip()[:300], at)
    s.flush()
    refresh(s, runtime(e.tenant_id), at)
    return e


def _close(s: Session, e: Engagement, by: str, org: str, reason: str, at: datetime) -> None:
    from ..domain.models import CaseEvent
    from ..visibility import system

    was = e.status
    e.status, e.ended_by, e.ended, e.end_reason = "ended", by, at, reason
    log(s, e.tenant_id, by, org, "engagement.ended" if was == "active" else "engagement.withdrawn", e.id, staff=e.staff_id, reason=reason)
    if was != "active":
        return
    pid = persona_id(e.staff_id)
    with system(s):
        for c in s.scalars(select(Case).where(Case.tenant_id == e.tenant_id, Case.owner == pid, Case.closed.is_(None))):
            s.add(CaseEvent(tenant_id=e.tenant_id, case_id=c.id, actor="access", actor_kind="system", kind="case.handed_off", ts=at,
                            payload={"from_owner": pid, "queue": c.queue, "reason": f"engagement ended: {reason}"}))
            c.owner = None


def expire(s: Session, tenant_id: str, at: datetime | None = None) -> int:
    """End every engagement whose date has passed. Run at start-up and whenever engagements are listed."""
    at = at or now()
    due = list(s.scalars(select(Engagement).where(Engagement.tenant_id == tenant_id, Engagement.status == "active", Engagement.ends <= at)))
    for e in due:
        _close(s, e, "calendar", "system", f"reached its end date, {e.ends:%d %b %Y}", at)
    if due:
        s.flush()
        refresh(s, runtime(tenant_id), at)
    return len(due)


def after_policy_change(s: Session, rt: TenantRuntime, by: str, at: datetime | None = None) -> list[str]:
    """A new policy may no longer allow what an engagement holds. End those, and say so. Returns warnings."""
    at = at or now()
    out = []
    for e in s.scalars(select(Engagement).where(Engagement.tenant_id == rt.id, Engagement.status.in_(("active", "proposed")))):
        try:
            check(rt.policy, staff(s, e.staff_id), list(e.roles), list(e.queues), max(e.ends, at + timedelta(seconds=1)), at)
            continue
        except EngagementError as err:
            if rt.policy.managed_service and "at most" in str(err):
                continue  # a shorter limit applies to new engagements, not to ones already agreed
            reason = f"access policy {rt.policy.version}: {err}"
        member = staff(s, e.staff_id)
        _close(s, e, by, "client", reason, at)
        out.append(f"Ends the engagement of {member.name if member else e.staff_id} ({reason})")
    s.flush()
    refresh(s, rt, at)
    return out


def impact(s: Session, rt: TenantRuntime, policy: AccessPolicy, at: datetime | None = None) -> list[str]:
    """What a draft policy would do to the engagements in force, without changing anything."""
    at = at or now()
    out = []
    for e in s.scalars(select(Engagement).where(Engagement.tenant_id == rt.id, Engagement.status.in_(("active", "proposed")))):
        member = staff(s, e.staff_id)
        try:
            check(policy, member, list(e.roles), list(e.queues), max(e.ends, at + timedelta(seconds=1)), at)
        except EngagementError as err:
            if policy.managed_service and "at most" in str(err):
                continue
            out.append(f"Ends the engagement of {member.name if member else e.staff_id}: {err}")
    return out


@dataclass(frozen=True)
class Live:
    """Why a provider person may act here right now, or why not."""
    ok: bool
    reason: str = ""


def still_live(s: Session, p: Persona, at: datetime | None = None) -> Live:
    """Checked on every request a provider person makes: the runtime may lag the calendar by a few minutes."""
    if not p.external:
        return Live(True)
    at = at or now()
    e = s.scalars(select(Engagement).where(Engagement.id == p.engagement)).first()
    if e is None or not _live(e, at):
        return Live(False, "Your engagement at this client has ended")
    member = staff(s, e.staff_id)
    if member is None or not member.active:
        return Live(False, "Your provider account is no longer active")
    return Live(True)


def out(e: Engagement, member: ProviderStaff | None, rt: TenantRuntime | None = None, at: datetime | None = None) -> dict:
    at = at or now()
    titles = {r: rt.policy.roles[r].title for r in e.roles if rt and r in rt.policy.roles}
    labels = {q: rt.policy.queues.get(q, q) for q in e.queues} if rt else {}
    return {"id": e.id, "tenant": e.tenant_id, "tenant_name": rt.name if rt else e.tenant_id, "staff_id": e.staff_id,
            "person_id": persona_id(e.staff_id), "name": member.name if member else e.staff_id, "title": member.title if member else "",
            "roles": [{"id": r, "title": titles.get(r, r)} for r in e.roles], "queues": [{"id": q, "label": labels.get(q, q)} for q in e.queues],
            "status": "expired" if e.status == "active" and e.ends <= at else e.status, "live": _live(e, at),
            "starts": e.starts.isoformat(), "ends": e.ends.isoformat(), "days_left": max(0, (e.ends - at).days),
            "note": e.note, "proposed_by": e.proposed_by, "proposed": e.proposed.isoformat(), "decided_by": e.decided_by,
            "decided": e.decided.isoformat() if e.decided else None, "ended_by": e.ended_by,
            "ended": e.ended.isoformat() if e.ended else None, "end_reason": e.end_reason}


def staff_out(m: ProviderStaff) -> dict:
    return {"id": m.id, "name": m.name, "title": m.title, "email": m.email, "console": m.console, "active": m.active,
            "person_id": persona_id(m.id)}


def allowance(policy: AccessPolicy) -> dict | None:
    ms = policy.managed_service
    if ms is None:
        return None
    return {"provider": ms.provider, "roles": [{"id": r, "title": policy.roles[r].title} for r in sorted(ms.roles)],
            "approval": ms.approval, "max_days": ms.max_days, "client_signoff": sorted(ms.client_signoff),
            "summary": [f"{ms.provider} staff may hold: {', '.join(policy.roles[r].title for r in sorted(ms.roles))}"] + ms.describe()[1:],
            "queues": [{"id": q, "label": label} for q, label in policy.queues.items()]}
