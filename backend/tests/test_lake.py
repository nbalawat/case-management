"""History on the lake: real Apache Iceberg tables in a temporary warehouse, filled from the in-process bus."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.backbone import relay, status, topics
from app.backbone.bus import Message
from app.backbone.streams import StreamConsumer
from app.config import settings
from app.lake import iceberg
from app.lake.base import LakeError
from app.lake.iceberg import IcebergStore
from app.lake.sink import LakeSink
from app.lake.tables import TABLES, alert_row, case_event_row, transaction_row
from support import T
from test_backbone import OTHER, customer, ext, send, txn, world, write_events  # noqa: F401  (world is a fixture)


@pytest.fixture
def store(tmp_path):
    return IcebergStore({"type": "sql", "uri": f"sqlite:///{tmp_path / 'catalog.db'}", "warehouse": f"file://{tmp_path / 'warehouse'}"})


def event(i, case="case-1", ts="2026-11-02T09:00:00Z"):
    return {"event_id": f"e{i}", "type": "caseforge.note.added", "case_id": case, "actor": "x", "actor_kind": "system",
            "ts": datetime(2026, 11, 2, 9, 0) + timedelta(minutes=i), "payload": "{}"}


# ---- the store

def test_rows_and_positions_commit_together(store):
    assert store.marks(T, "case_events") == {} and store.scan(T, "case_events") == [] and store.tables(T) == []
    assert store.append(T, "case_events", [event(1), event(2)], {"t/0": 2}) == 2
    assert store.marks(T, "case_events") == {"t/0": 2}
    assert [r["event_id"] for r in store.scan(T, "case_events")] == ["e1", "e2"]


def test_positions_for_untouched_partitions_carry_forward(store):
    store.append(T, "case_events", [event(1)], {"t/0": 5})
    store.append(T, "case_events", [event(2)], {"t/1": 9})
    assert store.marks(T, "case_events") == {"t/0": 5, "t/1": 9}


def test_an_empty_batch_writes_nothing(store):
    assert store.append(T, "case_events", [], {"t/0": 3}) == 0 and store.tables(T) == []


def test_each_client_has_its_own_tables(store):
    store.append(T, "case_events", [event(1)], {})
    store.append(OTHER, "case_events", [event(2), event(3)], {})
    assert [r["event_id"] for r in store.scan(T, "case_events")] == ["e1"] and len(store.scan(OTHER, "case_events")) == 2
    assert store.namespace(T) != store.namespace(OTHER) and store.tables("nobody") == []
    assert store.namespace("new-client-9") == "caseforge_new_client_9"


def test_a_filter_and_a_limit(store):
    store.append(T, "case_events", [event(1, "a"), event(2, "b"), event(3, "a")], {})
    assert [r["event_id"] for r in store.scan(T, "case_events", equals={"case_id": "a"})] == ["e1", "e3"]
    assert len(store.scan(T, "case_events", limit=2)) == 2


def test_the_table_can_be_read_as_it_stood_earlier(store):
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    store.append(T, "case_events", [event(1)], {})
    time.sleep(0.05)
    between = datetime.now(timezone.utc)
    time.sleep(0.05)
    store.append(T, "case_events", [event(2)], {})
    assert store.scan(T, "case_events", as_of=before) == []
    assert [r["event_id"] for r in store.scan(T, "case_events", as_of=between)] == ["e1"]
    assert len(store.scan(T, "case_events", as_of=datetime.now(timezone.utc))) == 2
    assert len(store.scan(T, "case_events", as_of=datetime.now(timezone.utc).replace(tzinfo=None))) == 2  # a time with no zone is taken as UTC


def test_table_facts(store):
    store.append(T, "case_events", [event(1), event(2)], {})
    store.append(T, "case_events", [event(3)], {})
    (info,) = store.tables(T)
    assert (info.name, info.rows, info.commits) == ("case_events", 3, 2) and info.last_commit is not None and "case_events" in info.location


def test_a_second_process_sees_what_the_first_wrote(store, tmp_path):
    store.append(T, "case_events", [event(1)], {"t/0": 1})
    other = IcebergStore({"type": "sql", "uri": f"sqlite:///{tmp_path / 'catalog.db'}", "warehouse": f"file://{tmp_path / 'warehouse'}"})
    assert other.marks(T, "case_events") == {"t/0": 1}
    other.append(T, "case_events", [event(2)], {"t/0": 2})
    assert store.marks(T, "case_events") == {"t/0": 2} and len(store.scan(T, "case_events")) == 2


def test_rows_are_partitioned_by_day(store, tmp_path):
    store.append(T, "case_events", [event(1), {**event(2), "ts": datetime(2026, 11, 5, 9, 0)}], {})
    days = {p.name for p in (tmp_path / "warehouse").rglob("day=*")}
    assert days == {"day=2026-11-02", "day=2026-11-05"}


def test_an_unknown_table_is_refused(store):
    with pytest.raises(LakeError, match="Unknown lake table"):
        store.append(T, "secrets", [{}], {})


@pytest.mark.parametrize("config,reason", [("not json", "not valid JSON"), ("[]", "JSON object"), ("{}", "JSON object"),
                                           ('{"type": "nonsense"}', "cannot be opened")])
def test_a_bad_catalog_setting_is_explained(config, reason):
    with pytest.raises(LakeError, match=reason):
        iceberg.connect(config)


def test_the_display_name_never_shows_a_credential():
    s = IcebergStore({"type": "sql", "uri": "sqlite://", "warehouse": "s3://user:hunter2@bucket/wh"})
    assert "hunter2" not in s.url and "bucket" not in s.url and s.url == "sql catalog"


# ---- messages to rows

def test_a_case_event_becomes_a_row():
    env = {"specversion": "1.0", "id": "e1", "type": "caseforge.case.created", "time": "2026-11-02T09:00:00Z",
           "data": {"case_id": "c1", "actor": "amara", "actor_kind": "human", "payload": {"b": 1, "a": 2}}}
    assert case_event_row(json.dumps(env).encode()) == {"event_id": "e1", "type": "caseforge.case.created", "case_id": "c1", "actor": "amara", "actor_kind": "human",
                                                        "ts": datetime(2026, 11, 2, 9, 0), "payload": '{"a": 2, "b": 1}'}


def test_a_time_with_an_offset_is_stored_as_utc():
    env = {"id": "e1", "time": "2026-11-02T10:00:00+01:00", "data": {"alert_id": "a", "scenario": "s", "party_id": "p", "score": 0.5}}
    assert alert_row(json.dumps(env).encode())["ts"] == datetime(2026, 11, 2, 9, 0)


@pytest.mark.parametrize("value", [b"junk", b"[]", b"{}", b'{"id": "x", "data": {}}', b'{"id": "x", "time": "t", "data": 3}'])
def test_what_is_not_an_event_is_not_a_row(value):
    with pytest.raises(ValueError):
        case_event_row(value)


def test_a_transaction_is_stored_in_canonical_form():
    row = transaction_row(json.dumps(txn(1, direction="CR", channel="CSH")).encode())
    assert row["direction"] == "in" and row["channel"] == "cash" and row["currency"] == "USD" and row["amount"] == 9000.0
    with pytest.raises(ValueError):
        transaction_row(json.dumps(txn(1, channel="pigeon")).encode())


def test_every_table_reads_a_topic_the_backbone_knows():
    assert {t.topic for t in TABLES.values()} <= set(topics.ALL)


# ---- the sink

def rows(store, table="case_events", tenant=T):
    return store.scan(tenant, table)


def test_events_published_by_the_relay_land_in_the_lake(world, store):
    write_events(world, 5)
    write_events(world, 2, OTHER, "case-k")
    relay.drain(world.bus)
    done = LakeSink(world.bus, store).step()
    assert done[(T, "case_events")].rows == 5 and done[(OTHER, "case_events")].rows == 2
    got = sorted(rows(store), key=lambda r: json.loads(r["payload"])["i"])
    assert [json.loads(r["payload"])["i"] for r in got] == [0, 1, 2, 3, 4] and {r["case_id"] for r in got} == {"case-1"}
    assert {r["case_id"] for r in rows(store, tenant=OTHER)} == {"case-k"}


def test_a_redelivered_batch_is_not_written_twice(world, store):
    """A sink that wrote the table but died before committing on the bus: the next one reads the same messages."""
    write_events(world, 4)
    relay.drain(world.bus)
    first = LakeSink(world.bus, store, group="g")
    first._sub = world.bus.subscribe("g", topics.pattern_any([t.topic for t in TABLES.values()]))
    first._write(first._sub.poll())  # written, never committed on the bus
    first.close()
    again = LakeSink(world.bus, store, group="g").step()
    assert again[(T, "case_events")].rows == 0 and again[(T, "case_events")].repeats == 4 and len(rows(store)) == 4


def test_new_messages_after_a_redelivery_are_still_written(world, store):
    write_events(world, 2)
    relay.drain(world.bus)
    LakeSink(world.bus, store, group="a").step()
    write_events(world, 3)
    relay.drain(world.bus)
    out = LakeSink(world.bus, store, group="b").step()[(T, "case_events")]  # a new group reads from the start
    assert (out.repeats, out.rows) == (2, 3) and len(rows(store)) == 5


def test_a_failed_write_is_retried_without_loss_or_repeat(world, store, monkeypatch):
    write_events(world, 3)
    relay.drain(world.bus)
    sink = LakeSink(world.bus, store)
    real = store.append
    monkeypatch.setattr(store, "append", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("object storage went away")))
    with pytest.raises(RuntimeError):
        sink.step()
    monkeypatch.setattr(store, "append", real)
    assert sink.step()[(T, "case_events")].rows == 3 and sink.step() == {} and len(rows(store)) == 3


def test_messages_that_are_not_rows_are_skipped_and_do_not_block(world, store):
    world.bus.publish([Message(topics.topic(T, topics.CASE_EVENTS), "k", b"junk")])
    write_events(world, 1)
    relay.drain(world.bus)
    out = LakeSink(world.bus, store).step()[(T, "case_events")]
    assert (out.rows, out.skipped) == (1, 1) and store.marks(T, "case_events") == {f"{topics.topic(T, topics.CASE_EVENTS)}/0": 2}


def test_payments_and_alerts_from_the_stream_reach_the_lake(world, store):
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(i, amount=9100 + i) for i in range(4)] + [txn(9, channel="pigeon")])
    StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    relay.drain(world.bus)
    done = LakeSink(world.bus, store).step()
    assert (done[(T, "transactions")].rows, done[(T, "transactions")].skipped) == (4, 1)
    assert sorted(r["txn_id"] for r in rows(store, "transactions")) == ["T0", "T1", "T2", "T3"]
    (alert,) = rows(store, "alerts")
    assert alert["scenario"] == "structuring" and json.loads(alert["txn_ids"]) == ["T0", "T1", "T2", "T3"] and alert["party_id"] == "P1"
    assert len(rows(store)) > 0 and rows(store, "alerts", OTHER) == []


def test_inbound_third_party_alerts_are_not_history_until_accepted(world, store):
    """The lake keeps the alerts this platform holds (the outbound topic), not whatever is sent to the inbound one."""
    send(world, topics.ALERTS_IN, [ext(score=7)])
    assert LakeSink(world.bus, store).step() == {}


def test_an_idle_sink_does_nothing(world, store):
    assert LakeSink(world.bus, store).step() == {}


# ---- what a client sees

def test_the_status_view_lists_this_clients_tables(world, store, tmp_path, monkeypatch):
    write_events(world, 3)
    write_events(world, 1, OTHER)
    relay.drain(world.bus)
    LakeSink(world.bus, store).step()
    config = json.dumps({"type": "sql", "uri": f"sqlite:///{tmp_path / 'catalog.db'}", "warehouse": f"file://{tmp_path / 'warehouse'}"})
    object.__setattr__(settings, "lake_catalog", config)
    try:
        got = status.lake(T)
        assert got["connected"] and got["note"] == "Apache Iceberg (sql catalog)"
        assert [(t["name"], t["rows"], t["commits"]) for t in got["tables"]] == [("case_events", 3, 1)]
        assert [(t["name"], t["rows"]) for t in status.lake(OTHER)["tables"]] == [("case_events", 1)]
        object.__setattr__(settings, "lake_catalog", "not json")
        assert status.lake(T) == {"connected": False, "note": status.lake(T)["note"], "tables": []} and "not valid JSON" in status.lake(T)["note"]
    finally:
        object.__setattr__(settings, "lake_catalog", "")
    assert status.lake(T)["connected"] is False and "operational database" in status.lake(T)["note"]
