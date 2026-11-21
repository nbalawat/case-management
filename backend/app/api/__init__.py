"""The HTTP API, one module per feature.

Every module in this package that defines a module-level `router` (a FastAPI `APIRouter`) is included in the app
automatically, behind the signed-in check. Adding a feature's endpoints is one new file here; nothing in
`app/main.py` changes, so parallel features never edit the same line.

    router = APIRouter(prefix="/api/things")   # included behind the signed-in check
    PUBLIC = True                              # optional: include without it (sign-in itself, health probes)

Modules are included in name order. Paths must not depend on that order: give each feature its own prefix.
`tests/test_routers.py` fails if two features declare the same method and path.
"""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator
from types import ModuleType

from fastapi import APIRouter


def feature_modules() -> Iterator[ModuleType]:
    """Every module in this package that carries a router, in name order."""
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.ispkg or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if isinstance(getattr(module, "router", None), APIRouter):
            yield module
