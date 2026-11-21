"""What the browser needs before anyone is signed in: how sign-in works here, and where to send the user."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..auth import sessions
from ..auth.identity import Identity
from ..auth.oidc import AuthError
from ..config import settings
from ..db import session_scope
from .deps import identity, staff_member

router = APIRouter(prefix="/api/auth")
PUBLIC = True  # signing in cannot require being signed in


@router.get("/config")
def config():
    return {"mode": settings.auth_mode, "dev_idp": settings.dev_idp and settings.auth_mode == "oidc"}


@router.get("/provider/{organisation}")
def provider(organisation: str):
    """Where a named organisation signs in. Names are not listed: you have to know your own."""
    org = _organisation(organisation)
    p = org.provider
    return {"organisation": org.realm, "name": org.name, "issuer": p.issuer, "client_id": p.client_id,
            "discovery": p.issuer.rstrip("/") + "/.well-known/openid-configuration"}


def _organisation(name: str) -> sessions.Organisation:
    if settings.auth_mode != "oidc":
        raise HTTPException(404, "Sign-in is not switched on")
    try:
        return sessions.organisation(name)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None


def _back(problem: str) -> RedirectResponse:
    """Back to the app's sign-in page, saying what went wrong. Only our own words go in the address."""
    r = RedirectResponse(f"{sessions.home_url()}?{urlencode({'signin_error': problem})}", status_code=303)
    r.delete_cookie(sessions.PENDING, path="/api/auth")
    return r


@router.get("/login")
def login(organisation: str):
    """Start signing in: off to the organisation's own identity provider, remembering this attempt in a cookie."""
    _organisation(organisation)
    try:
        url, pending = sessions.begin(organisation)
    except AuthError as e:
        return _back(str(e))
    r = RedirectResponse(url, status_code=303)
    # Lax, not Strict: the provider sends the browser back here from another site, and this cookie must come along.
    r.set_cookie(sessions.PENDING, pending, max_age=sessions.PENDING_TTL, httponly=True, secure=sessions.cookie_secure(),
                 samesite="lax", path="/api/auth")
    return r


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if settings.auth_mode != "oidc":
        raise HTTPException(404, "Sign-in is not switched on")
    if error or not code:
        return _back("Sign-in was cancelled" if error in ("", "access_denied") else "The identity provider could not sign you in")
    try:
        with session_scope(platform=True) as s:
            cookie = sessions.complete(s, code, state, request.cookies.get(sessions.PENDING), request.headers.get("user-agent", ""))
    except AuthError as e:
        return _back(str(e))
    r = RedirectResponse(sessions.home_url(), status_code=303)
    r.delete_cookie(sessions.PENDING, path="/api/auth")
    # Strict: no other site can make the browser send it. httpOnly: no script, ours included, can read it.
    r.set_cookie(sessions.COOKIE, cookie, httponly=True, secure=sessions.cookie_secure(), samesite="strict", path="/",
                 max_age=settings.session_max_hours * 3600)
    return r


@router.post("/logout")
def logout(request: Request):
    """Sign out here and at the identity provider. The answer says where the browser goes to finish signing out."""
    with session_scope(platform=True) as s:
        url = sessions.sign_out(s, request.cookies.get(sessions.COOKIE))
    r = JSONResponse({"redirect": url})
    r.delete_cookie(sessions.COOKIE, path="/")
    return r


@router.get("/whoami")
def whoami(who: Identity | None = Depends(identity)):
    if who is None:
        return {"mode": "demo"}
    if who.operator:
        member = staff_member(who.subject)
        return {"mode": "oidc", "operator": member if member and member["console"] else None, "staff": member, "subject": who.subject}
    known = who.subject in who.tenant.policy.people
    return {"mode": "oidc", "subject": who.subject, "tenant": {"id": who.tenant.id, "name": who.tenant.name}, "known": known}
