"""Everything that happens to every public API request, around the route itself.

In order: a request id; a cap on the body size; authentication; rate limits per caller and per client;
idempotent replay of writes; security headers on every answer; and a record of the call. Written as plain ASGI so
the body can be read once, checked and passed on unchanged.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.routing import Match

from ..auth.identity import authenticate
from ..auth.oidc import AuthError
from ..config import settings
from ..db import session_scope
from ..observability import logs
from ..domain.models import now
from . import clients, credentials, ratelimit, scopes as catalogue
from .conventions import problem
from .models import ApiCall, ApiIdempotency
from .principal import Principal

log = logging.getLogger("caseforge.public")
OPEN = ("/oauth/token", "/oauth/revoke", "/openapi.json", "/docs", "/health", "/scopes")
WRITES = {"POST", "PUT", "PATCH", "DELETE"}
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{8,120}$")
IDEMPOTENCY_HOURS = 24
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"cross-origin-resource-policy", b"same-origin"),
]
DOCS_CSP = (b"content-security-policy", b"default-src 'none'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            b"style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'")
API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")


@dataclass
class Captured:
    status: int = 0
    headers: list = field(default_factory=list)
    body: bytearray = field(default_factory=bytearray)


def client_ip(scope) -> str:
    """The caller's address as the server saw it. Behind a proxy, run uvicorn with --forwarded-allow-ips so this is the real one."""
    c = scope.get("client")
    return c[0] if c else ""


def authenticate_bearer(value: str, ip: str) -> Principal:
    """An application's access token, or a person's token from their organisation's identity provider."""
    scheme, _, token = value.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise clients.AuthFailure("Send an access token: Authorization: Bearer <token>", 401, "unauthenticated")
    if token.startswith(credentials.TOKEN_PREFIX):
        with session_scope(platform=True) as ps:
            return clients.resolve_token(ps, token, ip)
    try:
        who = authenticate(value)
    except AuthError as e:
        raise clients.AuthFailure(str(e), 401, "invalid_token") from None
    if who.tenant is None:
        raise clients.AuthFailure("Platform staff have no access to client data through the API", 403, "forbidden")
    rt = who.tenant
    if rt.status != "live":
        raise clients.AuthFailure(f"{rt.name} is suspended", 403, "forbidden")
    persona = rt.policy.people.get(who.subject)
    if persona is None:
        raise clients.AuthFailure(f"'{who.subject}' is signed in but has no role at {rt.name}", 403, "forbidden")
    return Principal("person", rt, persona, frozenset(catalogue.CATALOGUE), rate_limit=settings.api_rate_limit)


def _fingerprint(method: str, path: str, query: str, body: bytes) -> str:
    return hashlib.sha256(b"\n".join([method.encode(), path.encode(), query.encode(), body])).hexdigest()


def _claim_key(p: Principal, key: str, fp: str) -> tuple[str, ApiIdempotency | None]:
    """new: this request goes ahead. replay: the stored answer is returned. busy / mismatch: refused."""
    with session_scope(p.tenant_id) as s:
        s.execute(delete(ApiIdempotency).where(ApiIdempotency.created < now() - timedelta(hours=IDEMPOTENCY_HOURS)))
        row = s.scalars(select(ApiIdempotency).where(ApiIdempotency.caller == p.caller, ApiIdempotency.key == key)).first()
        if row is not None:
            if row.fingerprint != fp:
                return "mismatch", None
            return ("replay", row) if row.status else ("busy", None)
        s.add(ApiIdempotency(caller=p.caller, key=key, fingerprint=fp))
        try:
            s.flush()
        except IntegrityError:  # a twin request got there first
            return "busy", None
    return "new", None


def _settle_key(p: Principal, key: str, out: Captured) -> None:
    with session_scope(p.tenant_id) as s:
        row = s.scalars(select(ApiIdempotency).where(ApiIdempotency.caller == p.caller, ApiIdempotency.key == key)).first()
        if row is None:
            return
        if out.status >= 500:  # nothing was changed for certain, so let the caller try again
            s.delete(row)
            return
        ctype = next((v.decode() for k, v in out.headers if k.lower() == b"content-type"), "application/json")
        row.status, row.body, row.content_type = out.status, bytes(out.body).decode("utf-8", "replace"), ctype[:80]


def _record(p: Principal, request_id: str, method: str, route: str, status: int, ms: int, ip: str) -> None:
    try:
        with session_scope(p.tenant_id) as s:
            s.add(ApiCall(request_id=request_id, caller=p.caller, caller_kind=p.kind, method=method, route=route[:160], status=status, ms=ms, ip=ip[:45]))
    except Exception:  # noqa: BLE001  the call record must never fail the call
        log.exception("Could not record an API call")


class PublicApiMiddleware:
    def __init__(self, app, routes: list | None = None):
        self.app = app
        self.routes = routes if routes is not None else list(getattr(app, "routes", []))

    def _route(self, scope) -> str:
        for r in self.routes:
            m, _ = r.matches(scope)
            if m == Match.FULL:
                return getattr(r, "path", "(unmatched)")
        return "(unmatched)"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        headers = Headers(scope=scope)
        outer = logs.request_id.get()  # the platform's request middleware already named this request and sends the header
        rid = headers.get("x-request-id", "")
        rid = outer or (rid if REQUEST_ID.match(rid) else uuid.uuid4().hex)
        scope.setdefault("state", {})["request_id"] = rid
        request = Request(scope)
        path = scope["path"][len(scope.get("root_path", "")):] if scope["path"].startswith(scope.get("root_path", "") or "\0") else scope["path"]
        docs = path in ("/docs", "/openapi.json")
        extra: list[tuple[bytes, bytes]] = ([] if outer else [(b"x-request-id", rid.encode())]) + [DOCS_CSP if docs else API_CSP]

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [h for h in message.get("headers", []) if h[0].lower() not in {k for k, _ in SECURITY_HEADERS}] \
                    + SECURITY_HEADERS + extra
            await send(message)

        async def answer(resp):
            await resp(scope, receive, send_with_headers)

        # 1. the body, read once, never more than the limit
        declared = headers.get("content-length")
        if declared and (not declared.isdigit() or int(declared) > settings.api_max_body):
            return await answer(problem(request, 413, f"The body is larger than {settings.api_max_body} bytes", "payload_too_large"))
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > settings.api_max_body:
                return await answer(problem(request, 413, f"The body is larger than {settings.api_max_body} bytes", "payload_too_large"))
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        if any(path == o or path.startswith(o + "/") for o in OPEN):
            return await self.app(scope, replay, send_with_headers)

        # 2. who is calling
        ip = client_ip(scope)
        try:
            p = await run_in_threadpool(authenticate_bearer, headers.get("authorization", ""), ip)
        except clients.AuthFailure as e:
            hdrs = {"WWW-Authenticate": f'Bearer error="{e.code}"'} if e.status == 401 else None
            return await answer(problem(request, e.status, str(e), e.code, hdrs))
        except credentials.CredentialError as e:
            log.error("Public API credentials are misconfigured: %s", e)
            return await answer(problem(request, 503, "The API is not configured to accept credentials", "unavailable"))
        scope["state"]["principal"] = p

        # 3. how often
        per_caller = ratelimit.limiter.take(f"caller:{p.tenant_id}:{p.caller}", p.rate_limit or settings.api_rate_limit)
        per_client = ratelimit.limiter.take(f"tenant:{p.tenant_id}", settings.api_tenant_rate_limit) if per_caller.allowed else per_caller
        decision = per_caller if not per_caller.allowed else per_client
        extra += [(b"ratelimit-limit", str(per_caller.limit).encode()), (b"ratelimit-remaining", str(per_caller.remaining).encode()),
                  (b"ratelimit-reset", str(per_caller.reset).encode())]
        route = self._route(scope)
        method = scope["method"]
        if not decision.allowed:
            await answer(problem(request, 429, "Too many requests. Slow down and retry after the time given.", "rate_limited",
                                 {"Retry-After": str(decision.retry_after)}))
            await run_in_threadpool(_record, p, rid, method, route, 429, int((time.perf_counter() - started) * 1000), ip)
            return

        # 4. writes that may be retried safely
        key = headers.get("idempotency-key")
        out = Captured()
        if key is not None and method in WRITES:
            if not IDEMPOTENCY_KEY.match(key):
                return await answer(problem(request, 400, "Idempotency-Key must be 8 to 120 visible characters", "invalid_idempotency_key"))
            fp = _fingerprint(method, path, scope.get("query_string", b"").decode(), bytes(body))
            state, row = await run_in_threadpool(_claim_key, p, key, fp)
            if state == "mismatch":
                return await answer(problem(request, 422, "This Idempotency-Key was used for a different request", "idempotency_key_reused"))
            if state == "busy":
                return await answer(problem(request, 409, "A request with this Idempotency-Key is still being handled", "idempotency_in_progress"))
            if state == "replay":
                extra.append((b"idempotent-replayed", b"true"))
                await send_with_headers({"type": "http.response.start", "status": row.status,
                                         "headers": [(b"content-type", row.content_type.encode())]})
                await send({"type": "http.response.body", "body": row.body.encode()})
                return

        async def capture(message):
            if message["type"] == "http.response.start":
                out.status, out.headers = message["status"], list(message.get("headers", []))
            elif message["type"] == "http.response.body" and key is not None and method in WRITES:
                out.body += message.get("body", b"")
            await send_with_headers(message)

        try:
            await self.app(scope, replay, capture)
        finally:
            if key is not None and method in WRITES:
                await run_in_threadpool(_settle_key, p, key, out if out.status else Captured(500))
            await run_in_threadpool(_record, p, rid, method, route, out.status or 500, int((time.perf_counter() - started) * 1000), ip)

