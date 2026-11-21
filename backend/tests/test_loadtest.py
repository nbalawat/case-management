"""The load-test harness: synthetic data, the stages, the end-to-end run through the real stream path, the report."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import Db
from sqlalchemy.orm import sessionmaker

import app.db as appdb
from app.backbone.bus import connect, reset_memory
from app.config import settings
from app.lake.iceberg import IcebergStore
from app.lake.query import DuckDbEngine
from app.loadtest import stages
from app.loadtest.generate import Population, encode, transactions
from app.loadtest.report import as_json, as_markdown, projection
from app.loadtest.runner import Plan, run
from app.tenants import registry

SETTINGS = {"event_bus_url": "memory://", "stream_unknown_customer": "reject", "event_topic_prefix": "caseforge"}


@pytest.fixture
def world(tmp_path):
    saved = registry.swap({})
    db = Db()
    old = appdb.engine, appdb.SessionLocal
    before = {k: getattr(settings, k) for k in SETTINGS}
    appdb.engine, appdb.SessionLocal = db.engine, sessionmaker(bind=db.engine, expire_on_commit=False)
    for k, v in SETTINGS.items():
        object.__setattr__(settings, k, v)
    reset_memory()
    db.store = IcebergStore({"type": "sql", "uri": f"sqlite:///{tmp_path / 'catalog.db'}", "warehouse": f"file://{tmp_path / 'warehouse'}"})
    yield db
    reset_memory()
    for k, v in before.items():
        object.__setattr__(settings, k, v)
    appdb.engine, appdb.SessionLocal = old
    registry.swap(saved)


def test_the_data_is_the_same_for_the_same_seed_and_ids_are_unique_per_run():
    pop = Population(200)
    start = datetime(2026, 11, 1)
    a = list(transactions(pop, 500, run="r1", start=start))
    assert a == list(transactions(pop, 500, run="r1", start=start))
    assert len({t["txn_id"] for t in a}) == 500
    b = list(transactions(pop, 500, run="r2", start=start))
    assert not {t["txn_id"] for t in a} & {t["txn_id"] for t in b}
    assert all(t["party_id"].startswith("LT-C") and t["account_id"] == t["party_id"].replace("-C", "-A") for t in a)
    key, value = encode(a[0])
    assert key == a[0]["party_id"] and json.loads(value) == a[0]


def test_some_customers_behave_suspiciously():
    pop = Population(5_000, suspicious_share=0.02)
    bad = [n for n in range(pop.customers) if pop.suspicious(n)]
    assert 50 < len(bad) < 150
    cash = [t for t in transactions(pop, 20_000, run="r", start=datetime(2026, 11, 1)) if t["channel"] == "cash" and float(t["amount"]) > 8_000]
    assert cash and all(pop.suspicious(int(t["party_id"][4:])) for t in cash)


def test_percentiles():
    assert stages.percentile([], 95) == 0.0
    values = [float(i) for i in range(1, 101)]
    assert stages.percentile(values, 50) in (50.0, 51.0) and stages.percentile(values, 99) == 99.0 and stages.percentile(values, 100) == 100.0


def test_a_wait_that_stops_moving_says_how_far_it_got():
    n = iter([1, 2, 2, 2, 2, 2, 2, 2])
    s = stages.wait_for("x", 10, lambda: next(n, 2), pump=lambda: None, stall_seconds=0.0)
    assert not s.complete and s.items == 2 and s.detail["target"] == 10


def test_a_wait_already_done_at_the_first_look_claims_no_throughput():
    s = stages.wait_for("x", 10, lambda: 10, pump=lambda: None)
    assert s.complete and "busy_per_second" not in s.detail


def test_an_inline_run_goes_through_the_real_stream_path_into_the_store_and_the_lake(world, tmp_path):
    plan = Plan(transactions=1_200, customers=60, inline=True, stall_seconds=10)
    o = run(plan, bus=connect(), store=world.store, query_engine=DuckDbEngine(world.store))
    names = [s.name for s in o.stages]
    assert names == ["customers", "publish", "stream", "lake", "lake queries"]
    assert o.stage("customers").items == 60
    stream = o.stage("stream")
    assert stream.complete and stream.items == 1_200 and stream.detail["loaded"] == 1_200 and stream.detail["rejected"] == 0
    assert o.stage("lake").complete and o.stage("lake").detail["rows_in_lake"] == 1_200
    assert o.environment["rows_in_store"] == 1_200 and o.environment["bus"] == "memory"
    q = o.stage("lake queries").detail["queries"]
    assert len(q) == len(stages.LAKE_QUERIES) and all(r["rows_scanned"] == 1_200 for r in q)

    again = run(Plan(transactions=300, customers=60, inline=True, stall_seconds=10), bus=connect(), store=world.store)
    assert again.stage("customers").items == 0 and again.stage("customers").detail["already_there"] == 60
    assert again.stage("stream").detail["loaded"] == 300 and again.environment["rows_in_store"] == 1_500

    md = as_markdown(o)
    assert "## What was measured" in md and "## Projection (arithmetic, not measured)" in md and "a fleet beyond that was not measured" in md
    doc = json.loads(as_json(o))
    assert doc["plan"]["transactions"] == 1_200 and doc["projection"]["targets"][0]["consumers_if_linear"] >= 1


def test_the_projection_needs_a_measured_stream():
    o = type("O", (), {"stage": lambda self, n: None})()
    assert projection(o) is None


class _Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("X-Tenant") == "loadtest" and not self.path.startswith("/api/broken")
        self.send_response(200 if ok else 500)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def test_api_load_measures_latency_per_page_and_counts_errors():
    server = HTTPServer(("127.0.0.1", 0), _Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        good = stages.api_load(url, "loadtest", "maya", seconds=0.3, concurrency=2, paths=("/api/a", "/api/b"))
        assert good.complete and good.items > 4 and good.detail["errors"] == 0 and good.detail["status_codes"].keys() == {"200"}
        assert {p["path"] for p in good.detail["pages"]} == {"/api/a", "/api/b"} and good.detail["p99_ms"] >= good.detail["p50_ms"]
        bad = stages.api_load(url, "loadtest", "maya", seconds=0.2, concurrency=1, paths=("/api/broken",))
        assert not bad.complete and bad.detail["errors"] == bad.items
    finally:
        server.shutdown()


def test_redelivered_messages_do_not_end_the_wait_before_every_payment_is_in(world, monkeypatch):
    """After a rebalance a consumer is handed messages another one already loaded; they come back as duplicates. The
    wait counts payments loaded, not messages received, so it ends only when the store holds every one."""
    from app.backbone.bus import BusError, MemorySubscription

    real, refused = MemorySubscription.commit, []

    def lose_the_first_commit(self):
        if not refused:  # the batch is in the database, but the bus never hears so: it is delivered again
            refused.append(1)
            raise BusError("Kafka did not accept the commit: Broker: Specified group generation id is not valid")
        return real(self)

    monkeypatch.setattr(MemorySubscription, "commit", lose_the_first_commit)
    o = run(Plan(transactions=2_500, customers=50, inline=True, stall_seconds=10), bus=connect(), store=None)
    stream = o.stage("stream")
    assert refused and stream.complete and stream.detail["duplicates"] > 0
    assert stream.detail["loaded"] == 2_500 and o.environment["rows_in_store"] == 2_500
