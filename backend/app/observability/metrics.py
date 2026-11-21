"""Prometheus metrics: what the process does (requests, stream batches) and what the platform holds (backlogs).

Process metrics are counted as work happens. Platform gauges (open cases and alerts, outbox backlog, stream totals
per client) are read from the database when Prometheus scrapes, at most once every CACHE_SECONDS, so a scrape costs a
handful of grouped queries however many clients there are. Labels are bounded: a route template, never a raw path;
a client id, of which a deployment has tens, never a case or customer id.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterator

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

log = logging.getLogger("caseforge.metrics")
REGISTRY = CollectorRegistry(auto_describe=True)
CACHE_SECONDS = 15.0

HTTP_REQUESTS = Counter("caseforge_http_requests", "API requests answered", ["method", "route", "status"], registry=REGISTRY)
HTTP_SECONDS = Histogram("caseforge_http_request_seconds", "Time to answer an API request", ["method", "route"], registry=REGISTRY,
                         buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
STREAM_MESSAGES = Counter("caseforge_stream_messages", "Inbound stream messages handled, by outcome", ["stream", "tenant", "outcome"], registry=REGISTRY)
STREAM_BATCH_SECONDS = Histogram("caseforge_stream_batch_seconds", "Time to load one client's micro-batch, detection included", ["stream"],
                                 registry=REGISTRY, buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60))
ALERTS_RAISED = Counter("caseforge_alerts_raised", "Alerts raised or accepted from the streams", ["stream", "tenant"], registry=REGISTRY)
LAKE_ROWS = Counter("caseforge_lake_rows_written", "Rows appended to lake tables", ["tenant", "table"], registry=REGISTRY)


def record_stream(stream: str, tenant: str, received: int, loaded: int, duplicates: int, rejected: int, alerts: int, seconds: float) -> None:
    for outcome, n in (("loaded", loaded), ("duplicate", duplicates), ("rejected", rejected)):
        if n:
            STREAM_MESSAGES.labels(stream, tenant, outcome).inc(n)
    if alerts:
        ALERTS_RAISED.labels(stream, tenant).inc(alerts)
    STREAM_BATCH_SECONDS.labels(stream).observe(seconds)


def _schema_not_ready(e: Exception) -> bool:
    """A missing table, as Postgres and SQLite each say it."""
    from sqlalchemy.exc import OperationalError, ProgrammingError

    text = str(getattr(e, "orig", e)).lower()
    return isinstance(e, (OperationalError, ProgrammingError)) and ("does not exist" in text or "no such table" in text)


class PlatformCollector(Collector):
    """Gauges read from the database at scrape time, cached briefly."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = 0.0
        self._cached: list[Metric] = []

    def collect(self) -> Iterator[Metric]:
        with self._lock:
            if time.monotonic() - self._at > CACHE_SECONDS:
                try:
                    self._cached = self._read()
                except Exception as e:  # noqa: BLE001  a database that cannot answer must not take /metrics down with it
                    if _schema_not_ready(e):  # a first start, before migrations have made the tables: expected, so no traceback
                        log.info("Platform gauges wait for the database schema (tables not created yet)")
                    else:
                        log.warning("Platform gauges could not be read", exc_info=True)
                    up = GaugeMetricFamily("caseforge_platform_gauges_up", "Whether the platform gauges could be read")
                    up.add_metric([], 0)
                    self._cached = [up]
                self._at = time.monotonic()
            yield from self._cached

    def _read(self) -> list[Metric]:
        from sqlalchemy import func, select

        from ..db import session_scope
        from ..config import settings
        from ..domain.models import Alert, Case, OutboxEvent, StreamStat, Tenant, now

        t0 = time.perf_counter()
        with session_scope(platform=True) as s:
            clients = s.execute(select(Tenant.id, Tenant.status)).all()
            open_ = Case.closed.is_(None)
            cases = s.execute(select(Case.tenant_id, func.count().filter(open_), func.count().filter(~open_),
                                     func.count().filter(open_ & (Case.sla_due < now()))).group_by(Case.tenant_id)).all()
            alerts = s.execute(select(Alert.tenant_id, Alert.status, func.count()).group_by(Alert.tenant_id, Alert.status)).all()
            waiting = [] if settings.outbox_publisher != "relay" else s.execute(select(OutboxEvent.tenant_id, func.count(), func.min(OutboxEvent.created))
                                .where(OutboxEvent.published_at.is_(None)).group_by(OutboxEvent.tenant_id)).all()
            streams = s.scalars(select(StreamStat)).all()
        out: list[Metric] = []
        g = GaugeMetricFamily("caseforge_clients", "Clients on the platform, by status", labels=["status"])
        for status in sorted({st for _, st in clients}):
            g.add_metric([status], sum(1 for _, st in clients if st == status))
        out.append(g)
        g = GaugeMetricFamily("caseforge_cases", "Cases, by client and whether open, closed, or open past their deadline", labels=["tenant", "status"])
        for t, opened, closed, overdue in cases:
            g.add_metric([t, "open"], opened)
            g.add_metric([t, "closed"], closed)
            g.add_metric([t, "overdue"], overdue)
        out.append(g)
        g = GaugeMetricFamily("caseforge_alerts", "Alerts, by client and status", labels=["tenant", "status"])
        for t, st, n in alerts:
            g.add_metric([t, st], n)
        out.append(g)
        # Under change data capture rows are never marked as sent here, so there is no backlog to report.
        g = GaugeMetricFamily("caseforge_outbox_waiting", "Events in the outbox not yet on the bus", labels=["tenant"])
        age = GaugeMetricFamily("caseforge_outbox_oldest_waiting_seconds", "Age of the oldest event not yet on the bus", labels=["tenant"])
        for t, n, oldest in waiting:
            g.add_metric([t], n)
            age.add_metric([t], max(0.0, (now() - oldest).total_seconds()) if oldest else 0.0)
        out += [g, age]
        totals = {k: GaugeMetricFamily(f"caseforge_stream_{k}_total_stored", f"Stream messages {k}, as the database counts them", labels=["tenant", "stream"])
                  for k in ("received", "loaded", "duplicates", "rejected", "alerts")}
        last = GaugeMetricFamily("caseforge_stream_last_message_timestamp_seconds", "When a stream last loaded a batch", labels=["tenant", "stream"])
        for st in streams:
            for k, fam in totals.items():
                fam.add_metric([st.tenant_id, st.stream], getattr(st, k))
            if st.last_at:
                last.add_metric([st.tenant_id, st.stream], st.last_at.timestamp())
        out += [*totals.values(), last]
        up = GaugeMetricFamily("caseforge_platform_gauges_up", "Whether the platform gauges could be read")
        up.add_metric([], 1)
        took = GaugeMetricFamily("caseforge_platform_gauges_seconds", "Time to read the platform gauges")
        took.add_metric([], time.perf_counter() - t0)
        return [*out, up, took]


_platform: PlatformCollector | None = None


def enable_platform_gauges() -> None:
    """The API exposes the platform gauges; the workers do not, so Prometheus never counts them twice."""
    global _platform
    if _platform is None:
        _platform = PlatformCollector()
        REGISTRY.register(_platform)


def exposition() -> bytes:
    return generate_latest(REGISTRY)


router = APIRouter(include_in_schema=False)


@router.get("/metrics")
def metrics() -> Response:
    # With METRICS_PORT set (the Helm chart sets it) the metrics are on that port only, which no ingress routes to, so
    # client ids and volumes are never served to the internet. On a laptop they are here as well, for convenience.
    if os.getenv("METRICS_PORT"):
        return Response(status_code=404)
    return Response(exposition(), media_type=CONTENT_TYPE_LATEST)


_serving: int | None = None


def serve(port: int) -> None:
    """The metrics on their own port, apart from the API: for the workers, and for the API pods in a deployment."""
    global _serving
    from prometheus_client import start_http_server

    if _serving == port:
        return
    start_http_server(port, registry=REGISTRY)
    _serving = port
    log.info("Metrics on :%d/metrics", port)
