"""Health and readiness, metrics, request ids and structured logs."""
from __future__ import annotations

import json
import logging

import pytest

from app.config import settings
from app.observability import health, logs, metrics
from app.observability.http import route_of


def test_liveness_readiness_and_metrics_over_http(client):
    assert client.get("/healthz").json()["status"] == "ok"
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["ready"] and {c["name"] for c in r.json()["checks"]} == {"database", "schema"}
    client.get("/api/cases", headers={"X-Tenant": "meridian", "X-Persona": "maya"})
    text = client.get("/metrics").text
    assert 'caseforge_http_requests_total{method="GET",route="/api/cases",status="200"}' in text
    assert 'caseforge_http_request_seconds_bucket{le="0.005",method="GET",route="/api/cases"}' in text
    assert 'caseforge_cases{status="open",tenant="meridian"}' in text and "caseforge_platform_gauges_up 1.0" in text


def test_a_route_is_labelled_by_its_template_never_its_path(client):
    r = client.get("/api/cases/C-does-not-exist", headers={"X-Tenant": "meridian", "X-Persona": "maya"})
    assert r.status_code == 404
    text = client.get("/metrics").text
    assert 'route="/api/cases/{case_id}"' in text and "C-does-not-exist" not in text
    assert route_of({}) == "unmatched"


def test_a_request_id_is_kept_when_sane_and_replaced_when_not(client):
    assert client.get("/healthz", headers={"X-Request-Id": "trace-abc-123"}).headers["x-request-id"] == "trace-abc-123"
    made = client.get("/healthz", headers={"X-Request-Id": "bad id\nwith newline"}).headers["x-request-id"]
    assert len(made) == 32 and "\n" not in made


def test_the_operator_health_view_needs_platform_staff(client):
    assert client.get("/api/platform/health").status_code == 403
    v = client.get("/api/platform/health", headers={"X-Operator": "olivia"}).json()
    assert v["status"] == "ready" and v["schema_head"] and "uptime_seconds" in v and v["tracing"] is False


def test_an_unready_database_fails_readiness_and_optional_services_only_degrade():
    def boom():
        raise RuntimeError("connection refused\nmore detail")

    ok = health.Check("database", True, lambda: "fine")
    down = health.Check("database", True, boom)
    slow_optional = health.Check("search", False, boom)
    assert health.run_checks([ok])["status"] == "ready"
    r = health.run_checks([ok, slow_optional])
    assert r["status"] == "degraded" and r["ready"] and r["checks"][1]["note"] == "connection refused"
    r = health.run_checks([down, slow_optional])
    assert r["status"] == "unready" and not r["ready"]


def test_a_check_that_hangs_is_reported_as_late(monkeypatch):
    import threading

    gate = threading.Event()
    monkeypatch.setattr(health, "TIMEOUT_SECONDS", 0.05)
    r = health.run_checks([health.Check("lake", False, lambda: gate.wait(1) and "late")])
    gate.set()
    assert not r["checks"][0]["ok"] and "no answer" in r["checks"][0]["note"]


def test_optional_services_are_checked_only_when_configured():
    names = ("event_bus_url", "lake_catalog", "search_url", "graph_url", "temporal_url")
    saved = {k: getattr(settings, k) for k in names}
    try:
        for k in names:
            object.__setattr__(settings, k, "")
        assert [c.name for c in health.checks()] == ["database", "schema"]
        object.__setattr__(settings, "search_url", "opensearch://127.0.0.1:1")
        assert [c.name for c in health.checks()] == ["database", "schema", "search"]
        r = health.run_checks([c for c in health.checks() if c.name == "search"])
        assert not r["checks"][0]["ok"] and r["status"] == "degraded"
    finally:
        for k, v in saved.items():
            object.__setattr__(settings, k, v)


def test_stream_batches_are_counted_by_outcome():
    before = metrics.REGISTRY.get_sample_value("caseforge_stream_messages_total", {"stream": "transactions", "tenant": "t-obs", "outcome": "loaded"}) or 0
    metrics.record_stream("transactions", "t-obs", 10, 7, 2, 1, 3, 0.2)
    get = lambda outcome: metrics.REGISTRY.get_sample_value("caseforge_stream_messages_total", {"stream": "transactions", "tenant": "t-obs", "outcome": outcome})  # noqa: E731
    assert get("loaded") - before == 7 and get("duplicate") >= 2 and get("rejected") >= 1
    assert metrics.REGISTRY.get_sample_value("caseforge_alerts_raised_total", {"stream": "transactions", "tenant": "t-obs"}) >= 3


@pytest.fixture
def captured():
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    h = Grab()
    h.setFormatter(logs.JsonFormatter())
    h.addFilter(logs.ContextFilter())
    lg = logging.getLogger("caseforge.test-logs")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    yield lg, records
    lg.removeHandler(h)


def test_json_logs_carry_the_request_and_client(captured):
    lg, records = captured
    a, b = logs.request_id.set("req-12345678"), logs.tenant.set("meridian")
    try:
        lg.info("loaded %d", 5, extra={"stream": "transactions", "rows": 5})
    finally:
        logs.request_id.reset(a)
        logs.tenant.reset(b)
    doc = json.loads(records[-1])
    assert doc["message"] == "loaded 5" and doc["request_id"] == "req-12345678" and doc["tenant"] == "meridian"
    assert doc["stream"] == "transactions" and doc["rows"] == 5 and doc["level"] == "info"


def test_configuring_logs_twice_does_not_double_lines():
    logs.configure("json")
    logs.configure("text")
    root = logging.getLogger()
    assert sum(1 for h in root.handlers if getattr(h, "_caseforge", False)) == 1


def test_with_a_metrics_port_the_api_does_not_serve_metrics_itself(client, monkeypatch):
    monkeypatch.setenv("METRICS_PORT", "9100")
    assert client.get("/metrics").status_code == 404
    monkeypatch.delenv("METRICS_PORT")
    assert client.get("/metrics").status_code == 200


def test_gauges_before_migrations_log_no_traceback(monkeypatch, caplog):
    """On a first start the gauges are asked for before the tables exist: that is expected, so it is said quietly."""
    from sqlalchemy.exc import OperationalError, ProgrammingError

    c = metrics.PlatformCollector()
    for err in (ProgrammingError("SELECT", {}, Exception('relation "tenant" does not exist')),
                OperationalError("SELECT", {}, Exception("no such table: tenant"))):
        monkeypatch.setattr(c, "_read", lambda err=err: (_ for _ in ()).throw(err))
        c._at = -1e9
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="caseforge.metrics"):
            got = list(c.collect())
        assert [m.name for m in got] == ["caseforge_platform_gauges_up"]
        assert not any(r.exc_info for r in caplog.records) and any("schema" in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(c, "_read", lambda: (_ for _ in ()).throw(RuntimeError("database gone")))
    c._at = -1e9
    with caplog.at_level(logging.INFO, logger="caseforge.metrics"):
        list(c.collect())
    assert any(r.exc_info for r in caplog.records if r.levelno == logging.WARNING)  # anything else still warns in full
