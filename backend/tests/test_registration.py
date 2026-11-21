"""Guards for the conventions that let features be built in parallel without editing shared lines.

See app/api/__init__.py (routers) and app/migrations/__init__.py (migrations)."""
from __future__ import annotations

from collections import Counter

from fastapi.routing import APIRoute

from app import migrations
from app.api import feature_modules
from app.main import app


def _routes(routes):
    for r in routes:
        if hasattr(r, "original_router"):  # FastAPI keeps included routers whole
            yield from _routes(r.original_router.routes)
        elif isinstance(r, APIRoute):
            yield r


def test_migrations_form_one_line():
    assert migrations.problems() == []


def test_every_feature_router_is_served():
    served = {id(r) for r in _routes(app.routes)}
    for module in feature_modules():
        missing = [r.path for r in module.router.routes if isinstance(r, APIRoute) and id(r) not in served]
        assert not missing, f"{module.__name__} routes not served: {missing}"


def test_no_two_features_claim_the_same_endpoint():
    claims = Counter((m, r.path) for r in _routes(app.routes) for m in r.methods)
    assert [k for k, n in claims.items() if n > 1] == []


def test_only_sign_in_is_public():
    assert sorted(m.__name__ for m in feature_modules() if getattr(m, "PUBLIC", False)) == ["app.api.auth"]
