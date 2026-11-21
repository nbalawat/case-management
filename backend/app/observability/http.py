"""Every API request: an id (taken from X-Request-Id when the caller sent a sane one), a timing, a metric under its
route template, and one access log line. Written as plain ASGI so it adds microseconds, not a second event loop hop."""
from __future__ import annotations

import logging
import re
import time
import uuid

from . import logs, metrics

log = logging.getLogger("caseforge.http")
_SANE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
QUIET = {"/healthz", "/readyz", "/metrics"}  # probes and scrapes are counted, not logged


def route_of(scope: dict) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return "unmatched"
    return "/{spa}" if path == "/{path:path}" else path


class RequestMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        given = headers.get("x-request-id", "")
        rid = given if _SANE_ID.match(given) else uuid.uuid4().hex
        from ..config import settings

        tenant = headers.get("x-tenant") if settings.auth_mode != "oidc" else None  # signed in: the token decides, not a header
        t_rid, t_tenant = logs.request_id.set(rid), logs.tenant.set(tenant)
        status = 500
        t0 = time.perf_counter()

        async def sending(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, sending)
        finally:
            took = time.perf_counter() - t0
            route = route_of(scope)
            method = scope.get("method", "GET")
            metrics.HTTP_REQUESTS.labels(method, route, str(status)).inc()
            metrics.HTTP_SECONDS.labels(method, route).observe(took)
            if scope.get("path") not in QUIET:
                log.info("%s %s %d %.1fms", method, scope.get("path"), status, took * 1000,
                         extra={"method": method, "route": route, "status": status, "ms": round(took * 1000, 1)})
            logs.request_id.reset(t_rid)
            logs.tenant.reset(t_tenant)
