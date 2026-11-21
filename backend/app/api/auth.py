"""What the browser needs before anyone is signed in: how sign-in works here, and where to send the user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import dev_idp
from ..auth.identity import PLATFORM, Identity, operator_provider, provider_of
from ..config import settings
from ..tenants.registry import UnknownTenant, runtime
from .deps import OPERATORS, identity

router = APIRouter(prefix="/api/auth")
PUBLIC = True  # signing in cannot require being signed in


@router.get("/config")
def config():
    return {"mode": settings.auth_mode, "dev_idp": settings.dev_idp and settings.auth_mode == "oidc"}


@router.get("/provider/{organisation}")
def provider(organisation: str):
    """Where a named organisation signs in. Names are not listed: you have to know your own."""
    if settings.auth_mode != "oidc":
        raise HTTPException(404, "Sign-in is not switched on")
    key = organisation.strip().lower()
    if key == PLATFORM:
        p, name = operator_provider(), "Platform staff"
    else:
        try:
            rt = runtime(key)
        except UnknownTenant:
            raise HTTPException(404, "No organisation with that name signs in here") from None
        p, name = provider_of(rt), rt.name
    if p is None:
        raise HTTPException(404, "No organisation with that name signs in here")
    return {"organisation": key, "name": name, "issuer": p.issuer, "client_id": p.client_id,
            "discovery": p.issuer.rstrip("/") + "/.well-known/openid-configuration"}


@router.get("/whoami")
def whoami(who: Identity | None = Depends(identity)):
    if who is None:
        return {"mode": "demo"}
    if who.operator:
        return {"mode": "oidc", "operator": OPERATORS.get(who.subject), "subject": who.subject}
    known = who.subject in who.tenant.policy.people
    return {"mode": "oidc", "subject": who.subject, "tenant": {"id": who.tenant.id, "name": who.tenant.name}, "known": known}
