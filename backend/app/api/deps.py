"""Shared request dependencies and response shapes.

Every request is for one client. In this demo the client and the person come from the `X-Tenant` and `X-Persona`
headers; a real deployment takes both from the sign-in token, never from the caller.
"""
from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlparse

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import sessions
from ..auth.identity import Identity, authenticate
from ..auth.oidc import AuthError
from ..cases.service import definition, stage_due
from ..config import settings
from ..db import session_scope
from ..directory import service as directory
from ..domain.access import MFA_METHODS
from ..domain.models import Case, now
from ..domain.personas import Persona, policy_of
from ..visibility import bind_viewer, find_case
from ..tenants import provider
from ..tenants.registry import TenantRuntime, UnknownTenant, all_runtimes, runtime

# Requests by provider staff that are only the shell keeping itself up to date; everything else they do is logged.
QUIET = ("/api/navigation", "/api/me", "/api/auth/", "/api/provider/")


def staff_member(sid: str | None) -> dict | None:
    """An active member of the provider's staff, from the provider's own directory."""
    if not sid:
        return None
    with session_scope(platform=True) as s:
        m = provider.staff(s, sid)
        return provider.staff_out(m) if m is not None and m.active else None


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _same_origin(request: Request) -> bool:
    """Whether a request made with the session cookie came from this application's own pages."""
    if request.headers.get("sec-fetch-site") == "same-origin":
        return True
    origin, home = request.headers.get("origin"), urlparse(settings.public_url)
    return bool(origin) and origin.rstrip("/") == f"{home.scheme}://{home.netloc}"


def _from_session(request: Request) -> str | None:
    """The bearer token a browser's sign-in session stands for, or None when it has none."""
    cookie = request.cookies.get(sessions.COOKIE)
    if not cookie:
        return None
    if request.method not in SAFE_METHODS and not _same_origin(request):
        # The cookie is SameSite=Strict already; this also stops a change sent from a page that is not ours.
        raise HTTPException(403, "This request did not come from CaseForge's own pages")
    with session_scope(platform=True) as s:
        try:
            return f"Bearer {sessions.access_token(s, cookie)}"
        except AuthError as e:  # caught inside, so a session that has just ended stays ended
            problem = str(e)
    raise HTTPException(401, problem, headers={"WWW-Authenticate": "Bearer"})


def identity(request: Request, authorization: str | None = Header(default=None)) -> Identity | None:
    """Who is calling: from a bearer token, or from the browser's sign-in session. None in demo mode."""
    if settings.auth_mode != "oidc":
        return None
    if not authorization:
        authorization = _from_session(request)
    try:
        who = authenticate(authorization)
    except AuthError as e:
        raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"}) from None
    if who.operator and settings.oidc_operator_mfa and not who.second_factor(MFA_METHODS, ()):
        raise HTTPException(401, "Platform staff sign in with a second factor. Sign in again using it.",
                            headers={"WWW-Authenticate": 'Bearer error="insufficient_user_authentication"'})
    return who


def tenant(x_tenant: str | None = Header(default=None), who: Identity | None = Depends(identity)) -> TenantRuntime:
    if who is not None and who.operator:
        # Provider staff reach a client only through an engagement the client agreed to; the header names which one.
        if not x_tenant:
            raise HTTPException(403, "Platform staff have no access to client data except where they are engaged. Choose a client.")
        try:
            rt = runtime(x_tenant)
        except UnknownTenant:
            raise HTTPException(404, "Unknown client") from None
        if provider.persona_id(who.subject) not in rt.policy.people:
            raise HTTPException(403, f"You have no engagement at {rt.name}")
    elif who is not None:  # signed in: the client is whoever's identity provider issued the token, whatever the headers say
        rt = who.tenant
    else:
        try:
            rt = runtime(x_tenant or settings.default_tenant)
        except UnknownTenant:
            if x_tenant or not all_runtimes():
                raise HTTPException(404, "Unknown client") from None
            rt = all_runtimes()[0]
    if rt.status != "live":
        raise HTTPException(403, f"{rt.name} is suspended")
    return rt


def machine_session(rt: TenantRuntime = Depends(tenant)) -> Iterator[Session]:
    """A session for a system calling in (a monitoring engine pushing alerts): this client's rows, no person's view."""
    with session_scope(rt.id) as s:
        yield s


def operator(x_operator: str | None = Header(default=None), who: Identity | None = Depends(identity)) -> dict:
    name = (who.subject if who.operator else None) if who is not None else x_operator
    member = staff_member(name)
    if member is None or not member["console"]:
        raise HTTPException(403, "The operator console is for platform staff")
    return member


def staff(x_operator: str | None = Header(default=None), who: Identity | None = Depends(identity)) -> dict:
    """Any active member of the provider's staff, console or not: for the list of clients they are engaged at."""
    name = (who.subject if who.operator else None) if who is not None else x_operator
    member = staff_member(name)
    if member is None:
        raise HTTPException(403, "This is for the provider's staff")
    return member


def platform_session(_: dict = Depends(operator)) -> Iterator[Session]:
    with session_scope(platform=True) as s:
        yield s


def persona(request: Request, rt: TenantRuntime = Depends(tenant), x_persona: str | None = Header(default=None),
            who: Identity | None = Depends(identity)) -> Persona:
    if who is not None:  # no default person and no switching: you are who your token says, or you are nobody here
        pid = provider.persona_id(who.subject) if who.operator else who.subject
        if not who.operator and provider.is_provider(pid):
            # Only the provider's own identity system speaks for its staff; a client's provider cannot name one of them.
            raise HTTPException(403, f"'{who.subject}' is a name kept for the provider's staff")
        p = rt.policy.people.get(pid) if who.operator else _client_person(rt, who)
        if p is None:
            raise HTTPException(403, f"'{who.subject}' is signed in but has no role at {rt.name}. Ask an administrator to add you on the Access page.")
        if not who.operator and rt.policy.sign_in.requires_mfa(p) and not who.second_factor(rt.policy.sign_in.methods, rt.policy.sign_in.acr):
            # RFC 9470: the client asks the identity provider again for a sign-in with a second factor
            raise HTTPException(401, f"{rt.name} requires a second factor for {p.title}. Sign in again using it.",
                                headers={"WWW-Authenticate": 'Bearer error="insufficient_user_authentication"'})
    else:
        if x_persona and x_persona not in rt.policy.people:
            raise HTTPException(403, f"'{x_persona}' is not a user of {rt.name}")
        p = rt.policy.person(x_persona)
    if p.external:
        _provider_request(rt, p, request)
    return p


def _client_person(rt: TenantRuntime, who: Identity) -> Persona | None:
    """Who a client's signed-in user is here. The identity provider's directory is asked first, then the policy."""
    try:
        with session_scope(rt.id) as s:
            return directory.signing_in(s, rt.policy, who.subject, who.groups, who.name)
    except directory.DirectoryError as e:
        raise HTTPException(403, str(e)) from None


def _provider_request(rt: TenantRuntime, p: Persona, request: Request) -> None:
    """Provider staff act at a client only while their engagement lasts, and the client sees everything they do."""
    with session_scope(rt.id) as s:
        live = provider.still_live(s, p)
        if not live.ok:
            provider.expire(s, rt.id)
            raise HTTPException(403, live.reason)
        path = request.url.path
        if not path.startswith(QUIET):
            provider.log(s, rt.id, p.id, "provider", "provider.request", f"{request.method} {path}"[:200], engagement=p.engagement)


def signed_in(who: Identity | None = Depends(identity)) -> None:
    """Applied to every API route: in OIDC mode nothing at all is answered without a valid token."""


def get_session(rt: TenantRuntime = Depends(tenant), p: Persona = Depends(persona)) -> Iterator[Session]:
    """A database session that can only see this client's rows, and of those only the cases this person may see."""
    with session_scope(rt.id) as s:
        yield bind_viewer(s, p)


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
    c = find_case(s, case_id)
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
            "disposition": c.disposition, "restricted": c.restricted, "agent_recommendation": c.agent_recommendation}
