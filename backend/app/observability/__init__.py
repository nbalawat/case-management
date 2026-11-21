"""Seeing the platform run: health and readiness, Prometheus metrics, OpenTelemetry traces and structured logs.

Everything here speaks an open standard, so the deployment chooses the tools: Prometheus or anything that scrapes
its format (Grafana Mimir, Thanos, a cloud's managed Prometheus), any OTLP collector for traces (Jaeger, Tempo, a
cloud's tracing service through the OpenTelemetry Collector), and JSON logs on stdout for whatever ships them.

    install(app)   wires the request middleware, /metrics, /healthz and /readyz into the API
"""
from __future__ import annotations

from fastapi import FastAPI


def install(app: FastAPI) -> None:
    from . import health, http, logs, metrics, tracing

    logs.configure()
    tracing.configure("caseforge-api", app)
    app.add_middleware(http.RequestMiddleware)
    app.include_router(health.router)
    app.include_router(metrics.router)
