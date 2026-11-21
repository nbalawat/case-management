"""Liveness and readiness, and the detail behind them for the operator console.

    /healthz   the process is up and answering. Kubernetes restarts the pod when this fails.
    /readyz    the process can do its job: the database answers and holds the schema this code expects. A load
               balancer stops sending traffic while this fails. Optional services (bus, lake, search, graph,
               durable timers) are checked and reported, but they never make the API unready, because the platform
               falls back without each of them (the outbox holds events, names are searched in the database...).

Every check runs with a time limit, so a service that hangs is reported as slow rather than hanging the probe.
"""
from __future__ import annotations

import functools
import socket
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as Late
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..config import settings

TIMEOUT_SECONDS = 2.0
STARTED = time.time()
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health")


@dataclass(frozen=True)
class Check:
    name: str
    required: bool
    run: Callable[[], str]  # returns a short note; raises when unhealthy
    label: str = ""


def _database() -> str:
    from sqlalchemy import text

    from ..db import engine

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine.dialect.name


_schema_ok_until = 0.0


@functools.cache
def _head() -> str:
    from .. import migrations

    return migrations.head()


def _schema() -> str:
    """Once the database is found at the newest revision it stays there for this process, so the answer is kept for a
    minute rather than asked of the database on every probe."""
    global _schema_ok_until
    from .. import migrations
    from ..db import engine

    want = _head()
    if time.monotonic() < _schema_ok_until:
        return want
    with engine.connect() as conn:
        at = migrations.current(conn)
    if at != want:
        raise RuntimeError(f"database is at {at or 'no revision'}, this code expects {want}")
    _schema_ok_until = time.monotonic() + 60
    return want


def _tcp(url: str, default_port: int) -> Callable[[], str]:
    """Can a connection be opened to the host and port in a service URL. Says nothing about credentials, which the
    service's own page reports; it does tell an operator in a second whether the thing is there at all."""
    def run() -> str:
        first = url.split(",")[0]
        u = urlparse(first if "://" in first else f"x://{first}")
        host, port = u.hostname or "localhost", u.port or default_port
        with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS):
            pass
        return f"{u.scheme} at {host}:{port}"
    return run


def _lake() -> str:
    from ..lake.iceberg import connect

    store = connect()
    store.ping()
    return store.url


def checks() -> list[Check]:
    out = [Check("database", True, _database, "Operational database"), Check("schema", True, _schema, "Schema at the newest migration")]
    if settings.event_bus_url.startswith("kafka://"):
        out.append(Check("bus", False, _tcp(settings.event_bus_url.removeprefix("kafka://"), 9092), "Event bus (Kafka)"))
    if settings.lake_catalog:
        out.append(Check("lake", False, _lake, "Lake catalog (Iceberg)"))
    if settings.search_url:
        out.append(Check("search", False, _tcp(settings.search_url, 9200), "Search (OpenSearch)"))
    if settings.graph_url:
        out.append(Check("graph", False, _tcp(settings.graph_url, 5432), "Graph (Apache AGE)"))
    if settings.temporal_url:
        out.append(Check("durable", False, _tcp(settings.temporal_url, 7233), "Durable timers (Temporal)"))
    return out


def run_checks(which: list[Check] | None = None) -> dict:
    which = checks() if which is None else which
    futures = {c.name: (c, time.perf_counter(), _pool.submit(c.run)) for c in which}
    results = []
    for name, (c, t0, f) in futures.items():
        try:
            note, ok = f.result(timeout=TIMEOUT_SECONDS), True
        except Late:
            note, ok = f"no answer within {TIMEOUT_SECONDS:.0f} s", False
        except Exception as e:  # noqa: BLE001  any failure is the answer
            note, ok = (str(e).splitlines() or [type(e).__name__])[0][:200], False
        results.append({"name": name, "label": c.label, "required": c.required, "ok": ok, "note": note,
                        "ms": round((time.perf_counter() - t0) * 1000, 1)})
    ready = all(r["ok"] for r in results if r["required"])
    status = "ready" if ready and all(r["ok"] for r in results) else "degraded" if ready else "unready"
    return {"status": status, "ready": ready, "checks": results}


router = APIRouter(include_in_schema=False)


@router.get("/healthz")
def healthz():
    return {"status": "ok", "uptime_seconds": round(time.time() - STARTED)}


@router.get("/readyz")
def readyz():
    # Only what readiness depends on: optional services are left to the operator view, so a slow search engine
    # never takes the API out of the load balancer.
    result = run_checks([c for c in checks() if c.required])
    return JSONResponse(result, status_code=200 if result["ready"] else 503)


def _operator():
    from ..api.deps import operator

    return operator


@router.get("/api/platform/health")
def platform_health(_: dict = Depends(_operator())):
    """Everything, for the operator console: required and optional checks, and the process itself."""
    import platform as py

    return run_checks() | {"uptime_seconds": round(time.time() - STARTED), "version": _version(), "python": py.python_version(),
                           "schema_head": _head(), "tracing": _tracing(), "log_format": _log_format()}


def _version() -> str:
    from ..main import app

    return app.version


def _tracing() -> bool:
    from . import tracing

    return tracing.enabled()


def _log_format() -> str:
    import os

    return os.getenv("LOG_FORMAT", "text")
