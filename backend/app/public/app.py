"""The public API application, mounted at /api/v1 with its own OpenAPI document and interactive reference."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from . import conventions, scopes as catalogue
from .middleware import PublicApiMiddleware
from .v1 import alerts, cases, customers, operations, platform, transactions

VERSION = "1.0.0"
PREFIX = "/api/v1"
DESCRIPTION = """
The CaseForge public API: build applications on the case management platform.

**Authentication.** Register an application on the *Developer* page (it needs the *manage access* permission). Exchange
its client id and secret for a short-lived token at `POST /oauth/token` (OAuth 2.0 client credentials), then send
`Authorization: Bearer <token>`. An application acts under roles its client's administrator chose and sees what a
person holding them would see. Restricted cases never reach an application.

**People decide.** Moving a case, completing its tasks, writing its narrative and sending filings are a person's
decisions. Call those with the person's own token from your organisation's identity provider; the workflow's four-eyes
and segregation rules apply as on the web interface.

**Conventions.** Errors are RFC 9457 problem documents with a stable `code` and the `request_id`. Lists are
cursor-paginated (`limit`, `cursor`, `next_cursor`). Send `Idempotency-Key` on any write to retry it safely for 24
hours. Case writes accept `If-Match` with the case's `ETag`. Rate limits are per application and per client, with
`RateLimit-*` headers and `Retry-After` on 429.

**Events.** Follow changes with `GET /events` or register webhooks; deliveries are signed per the Standard Webhooks
specification and retried for about a day and a half.
"""
TAGS = [{"name": n} for n in ("Authentication", "Cases", "Alerts", "Customers", "Transactions", "Regulatory filings", "Screening",
                              "Case types", "Ask anything", "Data pipelines", "Events and webhooks", "Metrics")]


def walk(routes) -> list:
    """Every route, through included routers (FastAPI keeps those as wrappers around the original router)."""
    out = []
    for r in routes:
        inner = getattr(r, "original_router", None)
        out += walk(inner.routes) if inner is not None else [r]
    return out


def _scope_of(route: APIRoute) -> str | None:
    for dep in route.dependant.dependencies:
        sc = getattr(dep.call, "scope", None)
        if sc:
            return sc
        for sub in dep.dependencies:
            sc = getattr(sub.call, "scope", None)
            if sc:
                return sc
    return None


def build() -> FastAPI:
    api = FastAPI(title="CaseForge API", version=VERSION, description=DESCRIPTION, openapi_tags=TAGS, docs_url="/docs", redoc_url=None,
                  openapi_url="/openapi.json", servers=[{"url": PREFIX}], swagger_ui_parameters={"persistAuthorization": False})
    conventions.install(api)
    for r in (platform.oauth, platform.me, cases.router, alerts.router, customers.router, transactions.router, operations.filings,
              operations.screening, operations.workflows, operations.ask, operations.data, platform.events, platform.hooks, operations.metrics):
        api.include_router(r)

    def openapi():
        if api.openapi_schema:
            return api.openapi_schema
        doc = get_openapi(title=api.title, version=api.version, description=api.description, routes=api.routes, tags=TAGS, servers=api.servers)
        comps = doc.setdefault("components", {})
        comps["securitySchemes"] = {
            "app": {"type": "oauth2", "description": "An application's token from the client-credentials grant",
                    "flows": {"clientCredentials": {"tokenUrl": f"{PREFIX}/oauth/token", "scopes": {s.name: s.description for s in catalogue.CATALOGUE.values()}}}},
            "person": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT", "description": "A person's token from their organisation's identity provider"},
        }
        comps.setdefault("schemas", {})["Problem"] = conventions.Problem.model_json_schema()
        problem_ref = {"content": {conventions.PROBLEM: {"schema": {"$ref": "#/components/schemas/Problem"}}}}
        for route in walk(api.routes):
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                continue
            sc = _scope_of(route)
            for method in route.methods:
                op = doc["paths"].get(route.path_format, {}).get(method.lower())
                if op is None:
                    continue
                responses = op.setdefault("responses", {})
                for code, what in (("401", "No valid token"), ("403", "Not allowed"), ("429", "Rate limited")):
                    responses.setdefault(code, {"description": what} | problem_ref)
                if route.path_format.startswith("/oauth"):
                    op["security"] = []
                    continue
                if sc:
                    people_only = catalogue.CATALOGUE[sc].people_only
                    op["security"] = ([] if people_only else [{"app": [sc]}]) + [{"person": []}]
                    op["x-scope"] = sc
                    if people_only:
                        op["x-people-only"] = True
                else:
                    op["security"] = [{"app": []}, {"person": []}]
                if method in ("POST", "PUT", "PATCH", "DELETE"):
                    op.setdefault("parameters", []).append({"name": "Idempotency-Key", "in": "header", "required": False,
                                                            "schema": {"type": "string", "minLength": 8, "maxLength": 120},
                                                            "description": "Retry this write safely for 24 hours"})
        api.openapi_schema = doc
        return doc

    api.openapi = openapi
    return api


public_api = build()
asgi = PublicApiMiddleware(public_api, walk(public_api.routes))
