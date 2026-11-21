"""Request guards for public API routes: who is calling, whether their scopes and roles allow it, and a session
that sees only their client's rows and, of those, only the cases they may see."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..db import session_scope
from ..visibility import bind_viewer
from . import scopes as catalogue
from .conventions import ApiError
from .principal import Principal


def principal(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if p is None:  # the middleware authenticates every route that is not open; this is a second lock on the same door
        raise ApiError(401, "Send an access token: Authorization: Bearer <token>", "unauthenticated", headers={"WWW-Authenticate": "Bearer"})
    return p


def allowed(p: Principal, scope: str) -> str | None:
    """Why this caller may not use the scope, or None if they may."""
    sc = catalogue.CATALOGUE[scope]
    if scope not in p.scopes:
        return f"This token does not carry the '{scope}' scope"
    if sc.people_only and not p.is_person:
        return f"'{scope}' is a decision the workflow keeps for people. Call with the person's own token, not an application token."
    if sc.permission and not p.persona.can(sc.permission):
        return f"{p.persona.title} does not hold the '{sc.permission}' permission this needs"
    return None


def need(scope: str):
    if scope not in catalogue.CATALOGUE:
        raise RuntimeError(f"Route asks for unknown scope {scope}")

    def dep(p: Principal = Depends(principal)) -> Principal:
        why = allowed(p, scope)
        if why:
            raise ApiError(403, why, "insufficient_scope" if scope not in p.scopes else "forbidden",
                           headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'} if scope not in p.scopes else None)
        return p
    dep.__name__ = f"need_{scope.replace(':', '_')}"
    dep.scope = scope  # read by the OpenAPI builder to document each operation's scope
    return dep


def permission(p: Principal, name: str) -> None:
    if not p.persona.can(name):
        raise ApiError(403, f"{p.persona.title} does not hold the '{name}' permission this needs", "forbidden")


def session(p: Principal = Depends(principal)) -> Iterator[Session]:
    with session_scope(p.tenant_id) as s:
        yield bind_viewer(s, p.persona)
