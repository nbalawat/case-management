"""The developer page: a client's administrator registers the applications that call the public API, and watches them."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import now
from ..domain.personas import Persona
from ..public import clients, scopes as catalogue
from ..public.app import PREFIX, VERSION
from ..public.models import ApiCall, ApiClient, WebhookEndpoint
from ..public.principal import APP_ACTOR, NEVER_FOR_APPS
from ..tenants.registry import TenantRuntime
from .deps import get_session, require, tenant

router = APIRouter(prefix="/api/developer")
MANAGE = "manage_access"


class ClientIn(BaseModel):
    name: str = Field(max_length=80)
    description: str = Field("", max_length=2000)
    roles: list[str] = Field(max_length=10)
    queues: list[str] = Field(default_factory=list, max_length=50)
    scopes: list[str] = Field(max_length=40)
    allowed_ips: list[str] = Field(default_factory=list, max_length=50)
    rate_limit: int = Field(0, ge=0)
    token_ttl: int = Field(0, ge=0)
    expires: datetime | None = None

    def spec(self) -> clients.Spec:
        return clients.Spec(self.name, self.roles, self.scopes, self.description, tuple(self.queues), tuple(self.allowed_ips),
                            self.rate_limit, self.token_ttl, self.expires.replace(tzinfo=None) if self.expires else None)


class RotateIn(BaseModel):
    overlap_hours: int = Field(24, ge=0, le=168)


class StatusIn(BaseModel):
    status: str
    reason: str = Field("", max_length=300)


def _do(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except clients.ClientError as e:
        raise HTTPException(404 if str(e) == "No such API client" else 422, str(e)) from None


def _usage(calls: list[ApiCall]) -> dict:
    return {"calls": len(calls), "errors": sum(1 for c in calls if c.status >= 400), "throttled": sum(1 for c in calls if c.status == 429),
            "p95_ms": sorted(c.ms for c in calls)[int(len(calls) * 0.95)] if calls else None,
            "by_status": dict(Counter(str(c.status) for c in calls))}


@router.get("")
def overview(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(require(MANAGE))):
    since = now() - timedelta(hours=24)
    calls = list(s.scalars(select(ApiCall).where(ApiCall.ts >= since)))
    by_caller: dict[str, list[ApiCall]] = {}
    for c in calls:
        by_caller.setdefault(c.caller, []).append(c)
    hooks = Counter(e.client_id for e in s.scalars(select(WebhookEndpoint)))
    eligible = {rid: r for rid, r in rt.policy.roles.items() if not (r.permissions & catalogue.FORBIDDEN_PERMISSIONS)}
    return {
        "api": {"base_url": PREFIX, "version": VERSION, "openapi": f"{PREFIX}/openapi.json", "docs": f"{PREFIX}/docs", "token_url": f"{PREFIX}/oauth/token"},
        "scopes": catalogue.describe(),
        "roles": [{"id": rid, "title": r.title, "permissions": sorted(r.permissions - NEVER_FOR_APPS), "read_only": r.read_only,
                   "sees": r.sees.describe() if r.sees else "Every case"} for rid, r in eligible.items()],
        "queues": [{"id": q, "label": label} for q, label in rt.policy.queues.items()],
        "clients": [clients.row(c) | {"usage": _usage(by_caller.get(APP_ACTOR + c.id, [])), "webhooks": hooks.get(APP_ACTOR + c.id, 0)}
                    for c in s.scalars(select(ApiClient).order_by(ApiClient.created.desc()))],
        "people_usage": _usage([c for c in calls if c.caller_kind == "person"]),
        "rules": ["Applications never hold manage access or recertify access, and never see restricted cases.",
                  "Moving cases, completing tasks, narratives and filings need a person's own token.",
                  "Secrets are shown once and stored only as keyed hashes. Tokens live at most an hour."],
    }


@router.post("/clients", status_code=201)
def create_client(body: ClientIn, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    c, secret = _do(clients.create, s, body.spec(), p)
    return clients.row(c) | {"client_secret": secret}


@router.get("/clients/{client_id}")
def read_client(client_id: str, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    c = _do(clients.get, s, client_id)
    calls = list(s.scalars(select(ApiCall).where(ApiCall.caller == APP_ACTOR + c.id).order_by(ApiCall.ts.desc(), ApiCall.id.desc()).limit(200)))
    from ..public.webhooks import service as webhooks

    eps = list(s.scalars(select(WebhookEndpoint).where(WebhookEndpoint.client_id == APP_ACTOR + c.id).order_by(WebhookEndpoint.created)))
    return clients.row(c) | {"secrets": clients.secrets_of(s, c.id), "history": list(reversed(c.history or [])), "usage": _usage(calls),
                             "calls": [{"ts": x.ts.isoformat(), "method": x.method, "route": x.route, "status": x.status, "ms": x.ms,
                                        "request_id": x.request_id, "ip": x.ip} for x in calls[:50]],
                             "webhooks": [webhooks.row(e) | {"recent": [webhooks.delivery_row(d) for d in webhooks.deliveries(s, e, limit=10)]} for e in eps]}


@router.put("/clients/{client_id}")
def update_client(client_id: str, body: ClientIn, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    return clients.row(_do(clients.update_client, s, client_id, body.spec(), p))


@router.post("/clients/{client_id}/secret")
def rotate_secret(client_id: str, body: RotateIn, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    return {"client_secret": _do(clients.rotate, s, client_id, p, body.overlap_hours), "overlap_hours": body.overlap_hours}


@router.post("/clients/{client_id}/status")
def set_status(client_id: str, body: StatusIn, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    return clients.row(_do(clients.set_status, s, client_id, body.status, p, body.reason))
