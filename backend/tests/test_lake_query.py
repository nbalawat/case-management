"""SQL over the lake: DuckDB over real Iceberg tables in a temporary warehouse. Isolation between clients, read-only
queries, time travel, the backfill from the operational store, and backtests reading the lake."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from app.domain.models import Account, Party, Transaction
from app.lake.backfill import backfill
from app.lake.iceberg import IcebergStore
from app.lake.query import DuckDbEngine, QueryError
from support import T

OTHER = "zenith"
T0 = datetime(2026, 3, 1, 9)


@pytest.fixture
def store(tmp_path):
    return IcebergStore({"type": "sql", "uri": f"sqlite:///{tmp_path / 'catalog.db'}", "warehouse": f"file://{tmp_path / 'warehouse'}"})


def txn(i, party="P1", amount=100.0, channel="cash", ts=T0):
    return {"txn_id": f"t{i}", "account_id": f"A-{party}", "party_id": party, "ts": ts + timedelta(hours=i), "amount": amount, "currency": "USD",
            "direction": "in", "channel": channel, "counterparty": "", "counterparty_country": "US"}


def test_sql_over_a_clients_tables(store):
    store.append(T, "transactions", [txn(i, amount=10.0 * i) for i in range(1, 11)], {})
    store.append(T, "transactions", [txn(i, party="P2") for i in range(11, 14)], {})
    eng = DuckDbEngine(store)
    r = eng.query(T, "SELECT party_id, count(*) AS n, sum(amount) AS total FROM transactions GROUP BY party_id ORDER BY party_id")
    assert r.columns == ["party_id", "n", "total"] and r.rows == [["P1", 10, 550.0], ["P2", 3, 300.0]]
    assert r.scanned["transactions"]["files"] >= 2 and r.scanned["transactions"]["rows"] == 13
    assert eng.query(T, "SELECT count(*) FROM alerts").rows == [[0]]  # a table not written yet is empty, not an error


def test_clients_never_see_each_other(store, tmp_path):
    store.append(T, "transactions", [txn(1)], {})
    store.append(OTHER, "transactions", [txn(2), txn(3)], {})
    eng = DuckDbEngine(store)
    assert eng.query(OTHER, "SELECT count(*) FROM transactions").rows == [[2]]
    other_files = [p.as_posix() for p in (tmp_path / "warehouse").rglob("*.parquet") if "zenith" in p.as_posix()]
    assert other_files
    with pytest.raises(QueryError, match="(?i)permission|disabled|not allowed"):
        eng.query(T, f"SELECT * FROM read_parquet('{other_files[0]}')")
    with pytest.raises(QueryError, match="(?i)permission|disabled|not allowed"):
        eng.query(T, "SELECT * FROM read_csv('/etc/passwd')")
    assert eng.query(T, "SELECT current_setting('lock_configuration')").rows == [[True]]


@pytest.mark.parametrize("sql", ["DELETE FROM transactions", "SELECT 1; SELECT 2", "COPY transactions TO '/tmp/x.csv'", "ATTACH 'x.db'",
                                 "INSTALL httpfs", "", "SET threads = 1"])
def test_only_one_select(store, sql):
    with pytest.raises(QueryError):
        DuckDbEngine(store).query(T, sql)


def test_time_travel(store):
    store.append(T, "transactions", [txn(1)], {})
    time.sleep(0.02)
    between = datetime.utcnow()
    time.sleep(0.02)
    store.append(T, "transactions", [txn(2)], {})
    eng = DuckDbEngine(store)
    assert eng.query(T, "SELECT count(*) FROM transactions").rows == [[2]]
    assert eng.query(T, "SELECT count(*) FROM transactions", as_of=between).rows == [[1]]
    assert eng.query(T, "SELECT count(*) FROM transactions", as_of=datetime(2000, 1, 1)).rows == [[0]]


def test_rows_are_capped_and_long_queries_stopped(store):
    store.append(T, "transactions", [txn(i) for i in range(30)], {})
    r = DuckDbEngine(store).query(T, "SELECT * FROM transactions", max_rows=10)
    assert len(r.rows) == 10 and r.truncated
    slow = DuckDbEngine(store, timeout=0.2)
    with pytest.raises(QueryError, match="longer than"):
        slow.query(T, "SELECT count(*) FROM range(100000000000) a")


def world(s, n_parties=3, per=20):
    for p in range(n_parties):
        pid = f"P{p}"
        s.add(Party(id=pid, name=f"Person {p}"))
        s.add(Account(id=f"A-{pid}", party_id=pid))
    s.flush()
    for p in range(n_parties):
        for i in range(per):
            s.add(Transaction(id=f"t{p}-{i}", account_id=f"A-P{p}", party_id=f"P{p}", ts=T0 + timedelta(hours=i), amount=9_500.0, direction="in", channel="cash"))
    s.flush()


def test_backfill_copies_once(s, store):
    world(s)
    store.append(T, "transactions", [txn(0) | {"txn_id": "t0-0", "party_id": "P0", "account_id": "A-P0"}], {})  # the stream already wrote one
    first = backfill(s, store, T, batch=25)
    assert (first["written"], first["already_there"]) == (59, 1)
    assert backfill(s, store, T)["written"] == 0
    assert DuckDbEngine(store).query(T, "SELECT count(DISTINCT txn_id), count(*) FROM transactions").rows == [[60, 60]]


def test_backtests_read_the_lake_and_find_the_same(s, store, monkeypatch):
    from app.detection import history, tuning
    from app.detection.scenarios import REGISTRY
    import app.lake.query as q

    world(s)
    name = "structuring"
    params = REGISTRY[name]["params"]
    from_db = tuning.hits(s, name, params)
    backfill(s, store, T)
    monkeypatch.setenv("DETECTION_HISTORY", "lake")
    monkeypatch.setattr(q, "engine", lambda: DuckDbEngine(store))
    assert history.source() == "lake"
    from_lake = tuning.hits(s, name, params)
    assert from_db and set(from_lake) == set(from_db)
    assert [h.score for _, h in from_lake.values()] == [h.score for _, h in from_db.values()]


def test_the_lake_falls_back_when_it_holds_nothing_for_the_client(s, store, monkeypatch):
    from app.detection import tuning
    from app.detection.scenarios import REGISTRY
    import app.lake.query as q

    world(s)
    monkeypatch.setenv("DETECTION_HISTORY", "lake")
    monkeypatch.setattr(q, "engine", lambda: DuckDbEngine(store))
    assert tuning.hits(s, "structuring", REGISTRY["structuring"]["params"])


def test_api_without_a_lake(client):
    r = client.get("/api/lake/tables", headers={"X-Tenant": "meridian", "X-Persona": "maya"})
    assert r.status_code == 409 and "LAKE_CATALOG" in r.json()["detail"]
    assert client.get("/api/lake/tables", headers={"X-Tenant": "meridian", "X-Persona": "ivan"}).status_code == 403
