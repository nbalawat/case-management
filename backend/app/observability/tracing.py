"""OpenTelemetry traces, sent over OTLP to whatever collector the deployment runs (Jaeger, Grafana Tempo, the
OpenTelemetry Collector in front of a cloud's tracing service). Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set and the
`tracing` dependency group is installed; the standard OTEL_* variables configure the rest (service name, sampling,
headers). Spans the platform opens itself (`span(...)`) cost nothing when tracing is off."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager

log = logging.getLogger("caseforge.tracing")
_on = False


def enabled() -> bool:
    return _on


def configure(service: str, app=None) -> bool:
    """Start exporting. True when tracing is on. Safe to call once per process."""
    global _on
    if _on or not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return _on
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but the tracing packages are not installed (uv sync --group tracing)")
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", service)}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    from ..db import engine

    trace_sql(engine)
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
        except ImportError:
            pass
    _on = True
    log.info("Traces go to %s as %s", os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"), service)
    return True


@contextmanager
def span(name: str, **attributes):
    """A span around platform work (a stream batch, a detection run). A no-op without tracing."""
    if not _on:
        yield None
        return
    from opentelemetry import trace

    with trace.get_tracer("caseforge").start_as_current_span(name) as sp:
        for k, v in attributes.items():
            if v is not None:
                sp.set_attribute(k, v)
        yield sp


def trace_sql(engine) -> None:
    """A span for every statement, from SQLAlchemy's own events. (The packaged instrumentor lags SQLAlchemy releases.)
    The statement is recorded without its parameters, which are bound separately and may hold customer data."""
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind
    from sqlalchemy import event

    tracer = trace.get_tracer("caseforge.sql")
    system = engine.dialect.name

    @event.listens_for(engine, "before_cursor_execute")
    def _start(conn, cursor, statement, parameters, context, executemany):
        op = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else "SQL"
        sp = tracer.start_span(f"{op} {system}", kind=SpanKind.CLIENT,
                               attributes={"db.system": system, "db.statement": statement[:2000], "db.operation": op})
        conn.info.setdefault("_otel_spans", []).append(sp)

    @event.listens_for(engine, "after_cursor_execute")
    def _end(conn, cursor, statement, parameters, context, executemany):
        spans = conn.info.get("_otel_spans")
        if spans:
            spans.pop().end()

    @event.listens_for(engine, "handle_error")
    def _error(ctx):
        spans = ctx.connection.info.get("_otel_spans") if ctx.connection is not None else None
        if spans:
            sp = spans.pop()
            sp.record_exception(ctx.original_exception)
            sp.set_status(trace.Status(trace.StatusCode.ERROR))
            sp.end()
