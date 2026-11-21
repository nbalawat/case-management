"""Tokens, the caller's own identity, the event feed and webhook endpoints."""
from __future__ import annotations

import base64
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ...db import session_scope
from .. import clients, events as feed, scopes as catalogue
from ..conventions import ApiError, limit_of, problem
from ..guard import need, principal, session
from ..middleware import client_ip
from ..principal import Principal
from ..webhooks import service as webhooks

oauth = APIRouter(prefix="/oauth", tags=["Authentication"])
me = APIRouter(tags=["Authentication"])
events = APIRouter(tags=["Events and webhooks"])
hooks = APIRouter(prefix="/webhooks", tags=["Events and webhooks"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str


async def _form(request: Request) -> dict:
    """OAuth requests are application/x-www-form-urlencoded (RFC 6749); JSON is accepted too, for convenience."""
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    raw = await request.body()
    if ctype == "application/json":
        import json

        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ApiError(400, "The body is not a JSON object", "invalid_request")
        return {k: str(v) for k, v in data.items() if isinstance(v, (str, int))}
    if ctype in ("application/x-www-form-urlencoded", ""):
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
    raise ApiError(415, "Send application/x-www-form-urlencoded", "unsupported_media_type")


def _client_auth(request: Request, form: dict) -> tuple[str, str]:
    """client_secret_basic, or client_secret_post."""
    h = request.headers.get("authorization", "")
    if h.lower().startswith("basic "):
        try:
            cid, _, secret = base64.b64decode(h[6:].strip()).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            raise ApiError(401, "Malformed Basic credentials", "invalid_client") from None
        return cid, secret
    return form.get("client_id", ""), form.get("client_secret", "")


def _oauth_error(request: Request, e: clients.AuthFailure):
    # RFC 6749 section 5.2: the error code travels as `error`; the problem fields are added alongside.
    hdrs = {"WWW-Authenticate": 'Basic realm="caseforge"'} if e.status == 401 else None
    return problem(request, e.status, str(e), e.code, hdrs, error=e.code, error_description=str(e))


def _issue(cid: str, secret: str, requested: list[str] | None, ip: str) -> dict:
    with session_scope(platform=True) as ps:
        value, tok = clients.issue_token(ps, cid, secret, requested, ip)
        ttl = int((tok.expires - tok.issued).total_seconds())
        return {"access_token": value, "token_type": "Bearer", "expires_in": ttl, "scope": " ".join(tok.scopes)}


@oauth.post("/token", response_model=TokenOut, summary="Get an access token (OAuth 2.0 client credentials)",
            openapi_extra={"requestBody": {"content": {"application/x-www-form-urlencoded": {"schema": {
                "type": "object", "required": ["grant_type"],
                "properties": {"grant_type": {"type": "string", "enum": ["client_credentials"]}, "scope": {"type": "string"},
                               "client_id": {"type": "string"}, "client_secret": {"type": "string", "format": "password"}}}}}}})
async def token(request: Request):
    """Authenticate with HTTP Basic (client_id:client_secret) or with client_id and client_secret in the form. Ask for a
    space-separated `scope` to get a narrower token than the client holds; leave it out for all of them. Tokens are
    short-lived and opaque; get a new one when it expires."""
    form = await _form(request)
    if form.get("grant_type") != "client_credentials":
        return problem(request, 400, "Only grant_type=client_credentials is supported", "unsupported_grant_type", error="unsupported_grant_type")
    cid, secret = _client_auth(request, form)
    requested = form.get("scope", "").split() or None
    try:
        return await run_in_threadpool(_issue, cid, secret, requested, client_ip(request.scope))
    except clients.AuthFailure as e:
        return _oauth_error(request, e)


@oauth.post("/revoke", status_code=200, summary="Revoke an access token (RFC 7009)")
async def revoke(request: Request):
    form = await _form(request)
    cid, secret = _client_auth(request, form)

    def run():
        with session_scope(platform=True) as ps:
            clients.authenticate_client(ps, cid, secret, client_ip(request.scope))
            clients.revoke_token(ps, form.get("token", ""), cid)

    try:
        await run_in_threadpool(run)
    except clients.AuthFailure as e:
        return _oauth_error(request, e)
    return {}


@me.get("/me", summary="Who the token belongs to, and what it may do")
def whoami(p: Principal = Depends(principal)) -> dict:
    sees = "every case" if p.persona.views is None else "; ".join(v.describe() for v in p.persona.views)
    return {"kind": p.kind, "tenant": {"id": p.tenant_id, "name": p.runtime.name}, "caller": p.caller, "name": p.persona.name,
            "client_id": p.client_id, "roles": list(p.persona.roles), "queues": list(p.persona.queues),
            "permissions": sorted(x for x in p.persona.permissions if p.persona.can(x)), "scopes": sorted(p.scopes),
            "sees": sees + ("" if p.kind == "person" or p.persona.views is None else ", plus cases it has acted on"),
            "restricted_cases": p.persona.can("view_restricted")}


@me.get("/scopes", summary="Every scope, and what it needs")
def list_scopes() -> dict:
    return {"scopes": catalogue.describe()}


@me.get("/health", summary="Liveness", include_in_schema=False)
def health() -> dict:
    return {"ok": True}


# --- the event feed -----------------------------------------------------------------------------------------------------

@events.get("/events", summary="The event feed: every change the caller may see, in commit order")
def list_events(after: int = Query(0, ge=0, description="The `next_after` of the previous page; 0 to start from the oldest kept"),
                types: str | None = Query(None, max_length=500, description="Comma-separated types or prefixes, for example case.*,alert.created"),
                limit: int = 100, p: Principal = Depends(need("events:read")), s: Session = Depends(session)) -> dict:
    """CloudEvents 1.0 envelopes, each with a `sequence`. Poll with `after` set to the last `next_after` to follow changes
    without webhooks. Case events need cases:read and only cover cases the caller sees; alert events need alerts:read."""
    try:
        patterns = feed.check_patterns(types.split(",")) if types else None
    except ValueError as e:
        raise ApiError(422, str(e), "invalid_request") from None
    data, next_after = feed.feed(s, p, after, limit_of(limit, 500), patterns)
    return {"data": data, "next_after": next_after}


@events.get("/event-types", summary="The event types there are")
def event_types() -> dict:
    return {"data": [{"type": k, "description": v} for k, v in feed.KNOWN.items()] + [{"type": "webhook.test", "description": "A test delivery"}],
            "note": "Every entry on a case's audit trail is an event; new kinds are added without a new API version. Subscribe with prefixes such as case.*"}


# --- webhooks -----------------------------------------------------------------------------------------------------------

class WebhookIn(BaseModel):
    url: str = Field(max_length=500, description="A public https address. Redirects are not followed.")
    event_types: list[str] = Field(default_factory=list, max_length=50, description="Types or prefixes such as case.*; empty for all")
    description: str = Field("", max_length=500)


class WebhookPatch(BaseModel):
    url: str | None = Field(None, max_length=500)
    event_types: list[str] | None = Field(None, max_length=50)
    description: str | None = Field(None, max_length=500)
    status: str | None = Field(None, description="active or paused")


class RotateIn(BaseModel):
    overlap_hours: int = Field(24, ge=0, le=168, description="Keep signing with the old secret too for this long")


def _wh(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except webhooks.WebhookError as e:
        raise ApiError(404 if "No such" in str(e) else 422, str(e), "webhook_not_found" if "No such" in str(e) else "invalid_webhook") from None


def _ws(p: Principal):
    """Webhook management reads and writes endpoints only; it needs no view of cases, so no viewer filter is involved."""
    return session_scope(p.tenant_id)


@hooks.get("", summary="Your webhook endpoints")
def list_hooks(p: Principal = Depends(need("webhooks:manage"))) -> dict:
    with _ws(p) as s:
        return {"data": [webhooks.row(e) for e in webhooks.mine(s, p)]}


@hooks.post("", status_code=201, summary="Register a webhook endpoint")
def create_hook(body: WebhookIn, p: Principal = Depends(need("webhooks:manage"))) -> dict:
    """Returns the signing secret once. Deliveries follow the Standard Webhooks specification: check `webhook-signature`
    over `{webhook-id}.{webhook-timestamp}.{body}` with HMAC-SHA256, refuse stale timestamps, and drop repeats by webhook-id."""
    with _ws(p) as s:
        ep, secret = _wh(webhooks.create, s, p, body.url, body.event_types, body.description)
        return webhooks.row(ep) | {"secret": secret}


@hooks.get("/{endpoint_id}", summary="One webhook endpoint")
def read_hook(endpoint_id: str, p: Principal = Depends(need("webhooks:manage"))) -> dict:
    with _ws(p) as s:
        return webhooks.row(_wh(webhooks.get, s, p, endpoint_id))


@hooks.patch("/{endpoint_id}", summary="Change, pause or resume a webhook endpoint")
def patch_hook(endpoint_id: str, body: WebhookPatch, p: Principal = Depends(need("webhooks:manage"))) -> dict:
    with _ws(p) as s:
        ep = _wh(webhooks.get, s, p, endpoint_id)
        return webhooks.row(_wh(webhooks.update, s, ep, url=body.url, types=body.event_types, description=body.description, status=body.status))


@hooks.delete("/{endpoint_id}", status_code=204, summary="Remove a webhook endpoint")
def delete_hook(endpoint_id: str, p: Principal = Depends(need("webhooks:manage"))):
    from fastapi import Response
    from sqlalchemy import delete

    from ..models import WebhookDelivery

    with _ws(p) as s:
        ep = _wh(webhooks.get, s, p, endpoint_id)
        s.execute(delete(WebhookDelivery).where(WebhookDelivery.endpoint_id == ep.id))
        s.delete(ep)
    return Response(status_code=204)


@hooks.post("/{endpoint_id}/secret", summary="Rotate the signing secret")
def rotate_hook(endpoint_id: str, body: RotateIn, p: Principal = Depends(need("webhooks:manage"))) -> dict:
    with _ws(p) as s:
        ep = _wh(webhooks.get, s, p, endpoint_id)
        return {"secret": _wh(webhooks.rotate, s, ep, body.overlap_hours), "overlap_hours": body.overlap_hours}


@hooks.post("/{endpoint_id}/test", status_code=202, summary="Send a test event")
def test_hook(endpoint_id: str, p: Principal = Depends(need("webhooks:manage"))) -> dict:
    with _ws(p) as s:
        return webhooks.delivery_row(webhooks.ping(s, _wh(webhooks.get, s, p, endpoint_id)))


@hooks.get("/{endpoint_id}/deliveries", summary="Recent deliveries and their outcome")
def hook_deliveries(endpoint_id: str, status: str | None = Query(None, pattern="^(pending|delivered|failed)$"), limit: int = 50,
                    p: Principal = Depends(need("webhooks:manage"))) -> dict:
    with _ws(p) as s:
        ep = _wh(webhooks.get, s, p, endpoint_id)
        return {"data": [webhooks.delivery_row(d) for d in webhooks.deliveries(s, ep, status, limit_of(limit))]}


@hooks.post("/{endpoint_id}/deliveries/{delivery_id}/retry", status_code=202, summary="Send a delivery again")
def retry_delivery(endpoint_id: str, delivery_id: str, p: Principal = Depends(need("webhooks:manage"))) -> dict:
    with _ws(p) as s:
        ep = _wh(webhooks.get, s, p, endpoint_id)
        return webhooks.delivery_row(_wh(webhooks.retry, s, ep, delivery_id))

