"""The MCP server over streamable HTTP, at /mcp on the API, signed in the way the rest of the API is.

Who is calling is worked out twice, on purpose: once at the door, so an unknown caller is turned away with a plain
401/403 before any MCP traffic, and again inside every tool call from that call's own HTTP request, so a tool can
never run as someone other than the request that invoked it. The server is stateless: no session outlives a request.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings

from ..api import deps
from .server import Principal, Unauthorized, build

PATH = "/mcp"
LOCAL_HOSTS = "127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*"


def _security(allowed_hosts: str | None) -> TransportSecuritySettings:
    """DNS-rebinding protection: only these Host headers (and matching browser origins) may reach the server.
    MCP_ALLOWED_HOSTS widens it for a deployment behind its own host name, for example caseforge.example.com:*."""
    hosts = [h.strip() for h in (allowed_hosts or os.getenv("MCP_ALLOWED_HOSTS") or LOCAL_HOSTS).split(",") if h.strip()]
    origins = [f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


KEY = "caseforge.principal"


def principal_of(request: Request) -> Principal:
    """The person at the client the request is for, by the API's own rules (token in OIDC mode, headers in the demo).
    Provider staff are held to their engagement, and their request is logged for the client, as on any other route."""
    headers = request.headers
    try:
        who = deps.identity(headers.get("authorization"))
        rt = deps.tenant(headers.get("x-tenant"), who)
        return Principal(rt.id, deps.persona(request, rt, headers.get("x-persona"), who))
    except HTTPException as e:
        raise Unauthorized(str(e.detail), e.status_code) from None


def _from_request(ctx: Context) -> Principal:
    """The caller of this tool call: the person the door resolved for this same HTTP request."""
    request = ctx.request_context.request
    if request is None:
        raise Unauthorized("No HTTP request carried this call")
    who = request.scope.get("state", {}).get(KEY)
    if who is None:
        raise Unauthorized("This request did not come through the sign-in check")
    return who


class _Door:
    """Turns away callers who are nobody at any client before they reach the MCP transport."""

    def __init__(self, app: ASGIApp | None):
        self.app = app  # set while the app's lifespan runs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            try:
                who = principal_of(Request(scope))
            except Unauthorized as e:
                await JSONResponse({"detail": str(e)}, status_code=e.status)(scope, receive, send)
                return
            # /mcp and /mcp/ are both the transport's root. Each tool call reads the caller from this request's state,
            # so a request is resolved (and, for provider staff, logged) once.
            scope = dict(scope, path="/", raw_path=b"/", state={**scope.get("state", {}), KEY: who})
        if self.app is None:
            await JSONResponse({"detail": "The MCP server is starting or stopping"}, status_code=503)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def mount(app: FastAPI, allowed_hosts: str | None = None):
    """Mount the server on the app. Returns an async context manager the app's lifespan must enter.

    Each time the lifespan runs (once per process in production; once per test client in the tests) it gets a server of
    its own, because the SDK's session manager can only be run once."""
    door = _Door(None)
    for path in (PATH, PATH + "/"):  # ahead of every other route, so nothing else (the UI's catch-all) answers first
        app.router.routes.insert(0, Route(path, endpoint=door, methods=["GET", "POST", "DELETE"]))

    @asynccontextmanager
    async def running():
        server = build(_from_request)
        door.app = server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                              transport_security=_security(allowed_hosts))
        try:
            async with server.session_manager.run():
                yield
        finally:
            door.app = None

    return running
