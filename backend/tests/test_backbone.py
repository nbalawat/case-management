"""The event backbone: the outbox, the bus contract, the relay and the inbound streams, on the in-process bus.

The same contract runs against a real broker in test_kafka.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.alerts.hub import ingest_external_alert
from app.backbone import outbox, relay, status, streams, topics
from app.backbone.bus import BusError, MemoryBus, Message, connect, reset_memory
from app.backbone.streams import StreamConsumer
from app.cases.service import record
from app.config import settings
from app.domain import models as m
from app.tenancy import TenantError
from app.tenants import registry
from app.tenants.onboarding import set_status
from conftest import Db, app_database
from support import T

OTHER = "kestrel"
NOW = datetime(2026, 11, 2, 9, 0)


@pytest.fixture
def world():
    """Two clients on an empty platform, with the application's own session factory pointed at it and a fresh in-process bus."""
    saved = registry.swap({})
    db = Db()
    with app_database(db.engine, sessionmaker(bind=db.engine, expire_on_commit=False)):
        for k, v in {"event_bus_url": "memory://", "outbox_publisher": "relay", "stream_unknown_customer": "reject", "event_topic_prefix": "caseforge"}.items():
            object.__setattr__(settings, k, v)
        reset_memory()
        db.onboard(T)
        db.onboard(OTHER)
        db.bus = connect()
        yield db
        reset_memory()
        object.__setattr__(settings, "event_bus_url", "")
    registry.swap(saved)


def customer(db, tenant=T, party="P1", account="A1"):
    with db.tenant(tenant) as s:
        s.add(m.Party(id=party, name=f"Customer {party}", risk_rating="medium"))
        s.add(m.Account(id=account, party_id=party))
        s.commit()


def outbox_rows(db, tenant=T):
    with db.tenant(tenant) as s:
        return list(s.scalars(select(m.OutboxEvent).order_by(m.OutboxEvent.id)))


def txn(i, party="P1", account="A1", amount=9000 + 0, day=0, **over):
    return {"txn_id": f"T{i}", "account_id": account, "party_id": party, "ts": (NOW + timedelta(days=day, minutes=i)).isoformat(), "amount": amount,
            "direction": "in", "channel": "cash", "counterparty": "", **over}


def send(db, stream, records, tenant=T, raw=False):
    db.bus.publish(Message(topics.topic(tenant, stream), str(r.get("party_id", "")) if isinstance(r, dict) else "", r if raw else json.dumps(r).encode())
                   for r in records)


def dead(db, tenant=T):
    return db.bus.read(topics.topic(tenant, topics.DEAD_LETTER))


# ---- topics

def test_each_client_has_its_own_topics():
    assert topics.topic("meridian", topics.ALERTS) == "caseforge.meridian.alerts"
    assert topics.parse("caseforge.meridian.case-events") == ("meridian", "case-events")


@pytest.mark.parametrize("name", ["other.meridian.alerts", "caseforge.meridian.unknown", "caseforge..alerts", "caseforge.meridian", "caseforgex.meridian.alerts"])
def test_a_topic_that_is_not_ours_is_not_parsed(name):
    assert topics.parse(name) is None


@pytest.mark.parametrize("stream", topics.STREAMS)
def test_a_stream_pattern_matches_every_client_and_nothing_else(stream):
    import re

    p = re.compile(topics.pattern(stream))
    assert p.match(f"caseforge.meridian.{stream}") and p.match(f"caseforge.new-client-9.{stream}")
    assert not p.match(f"caseforge.meridian.{stream}.x") and not p.match(f"caseforge.a.b.{stream}") and not p.match("caseforge.meridian.dead-letter")
    assert not p.match(f"xcaseforge.meridian.{stream}")


# ---- the outbox

def test_an_audit_event_writes_one_cloudevent_in_the_same_transaction(world):
    with world.tenant(T) as s:
        record(s, "case-1", "amara", "human", "case.state_changed", {"from": "triage", "to": "investigation"}, ts=NOW)
        s.commit()
    (row,) = outbox_rows(world)
    env = json.loads(row.payload)
    assert row.topic == "caseforge.meridian.case-events" and row.key == "case-1" and row.type == "caseforge.case.state_changed"
    assert row.published_at is None and row.tenant_id == T and row.event_id == env["id"]
    assert env["specversion"] == "1.0" and env["source"] == "/caseforge/meridian" and env["subject"] == "case-1" and env["tenantid"] == T
    assert env["time"] == "2026-11-02T09:00:00Z" and env["datacontenttype"] == "application/json"
    assert env["data"] == {"case_id": "case-1", "actor": "amara", "actor_kind": "human", "payload": {"from": "triage", "to": "investigation"}}


def test_a_rolled_back_change_leaves_no_event(world):
    with world.tenant(T) as s:
        record(s, "case-1", "amara", "human", "note.added", {})
        s.flush()
        s.rollback()
    assert outbox_rows(world) == []


def test_flushing_twice_does_not_write_the_event_twice(world):
    with world.tenant(T) as s:
        record(s, "case-1", "amara", "human", "note.added", {})
        s.flush()
        s.flush()
        record(s, "case-1", "amara", "human", "note.added", {})
        s.commit()
    assert len(outbox_rows(world)) == 2


def test_a_new_alert_and_its_case_events_are_all_in_the_outbox(world):
    with world.tenant(T) as s:
        alert, case = ingest_external_alert(s, source="actimize", external_id="X1", scenario="structuring", party_id="P9", score=0.9, explanation="why")
        s.commit()
    rows = outbox_rows(world)
    kinds = [r.type for r in rows]
    assert kinds.count("caseforge.alert.created") == 1 and "caseforge.case.created" in kinds and "caseforge.alert.attached" in kinds
    a = next(json.loads(r.payload) for r in rows if r.type == "caseforge.alert.created")
    assert a["subject"] == alert.id and a["data"]["party_id"] == "P9" and a["data"]["source"] == "actimize" and a["data"]["score"] == 0.9
    assert next(r for r in rows if r.type == "caseforge.alert.created").topic == "caseforge.meridian.alerts"
    assert {r.key for r in rows if r.topic.endswith("case-events")} == {case.id}


def test_events_carry_their_own_client_and_no_other(world):
    for t in (T, OTHER):
        with world.tenant(t) as s:
            record(s, f"case-{t}", "x", "system", "note.added", {})
            s.commit()
    for t in (T, OTHER):
        (row,) = outbox_rows(world, t)
        assert row.topic == f"caseforge.{t}.case-events" and json.loads(row.payload)["tenantid"] == t and row.key == f"case-{t}"


def test_a_platform_session_stamps_the_event_with_the_rows_client(world):
    with world.platform() as p:
        p.add(m.CaseEvent(tenant_id=OTHER, case_id="case-7", actor="platform", actor_kind="system", kind="note.added", payload={}))
        p.commit()
    assert [r.topic for r in outbox_rows(world, OTHER)] == ["caseforge.kestrel.case-events"] and outbox_rows(world, T) == []


def test_an_event_for_another_client_is_refused_with_its_message(world):
    with world.tenant(T) as s:
        s.add(m.CaseEvent(tenant_id=OTHER, case_id="case-7", actor="x", actor_kind="system", kind="note.added", payload={}))
        with pytest.raises(TenantError):
            s.commit()
    assert outbox_rows(world, T) == [] and outbox_rows(world, OTHER) == []


def test_event_ids_are_unique(world):
    with world.tenant(T) as s:
        for i in range(50):
            record(s, f"case-{i % 3}", "x", "system", "note.added", {"i": i})
        s.commit()
    rows = outbox_rows(world)
    assert len({r.event_id for r in rows}) == 50 and [json.loads(r.payload)["data"]["payload"]["i"] for r in rows] == list(range(50))


def test_payloads_that_are_not_plain_json_still_serialise(world):
    with world.tenant(T) as s:
        ev = m.CaseEvent(case_id="c", actor="x", actor_kind="system", kind="k", payload={"at": NOW})
        row = outbox.for_case_event(ev, T)
    assert json.loads(row.payload)["data"]["payload"]["at"].startswith("2026-11-02")


# ---- the bus contract, in process

@pytest.fixture
def bus():
    return MemoryBus()


def msgs(topic, n, key="k"):
    return [Message(topic, key, str(i).encode()) for i in range(n)]


def test_messages_come_back_in_order_with_offsets(bus):
    bus.publish(msgs("caseforge.a.alerts", 5))
    got = bus.subscribe("g", r"^caseforge\..*\.alerts$").poll()
    assert [x.value for x in got] == [b"0", b"1", b"2", b"3", b"4"] and [x.offset for x in got] == [0, 1, 2, 3, 4]


def test_groups_read_independently(bus):
    bus.publish(msgs("t.one", 3))
    a, b = bus.subscribe("a", "^t\\."), bus.subscribe("b", "^t\\.")
    assert len(a.poll()) == 3 and len(b.poll()) == 3 and a.poll() == []


def test_what_was_not_committed_is_delivered_again(bus):
    bus.publish(msgs("t.one", 4))
    first = bus.subscribe("g", "^t\\.")
    assert len(first.poll(max_messages=2)) == 2
    first.commit()
    assert len(first.poll()) == 2  # read, never committed
    first.close()
    again = bus.subscribe("g", "^t\\.")
    assert [x.value for x in again.poll()] == [b"2", b"3"]


def test_a_subscription_sees_topics_created_after_it(bus):
    sub = bus.subscribe("g", r"^caseforge\.[^.]+\.transactions$")
    assert sub.poll() == []
    bus.publish(msgs("caseforge.new.transactions", 1))
    bus.publish(msgs("caseforge.new.alerts", 1))
    assert [x.topic for x in sub.poll()] == ["caseforge.new.transactions"]


def test_poll_respects_the_batch_size_across_topics(bus):
    bus.publish(msgs("t.a", 3) + msgs("t.b", 3))
    sub = bus.subscribe("g", "^t\\.")
    assert len(sub.poll(max_messages=4)) == 4 and len(sub.poll(max_messages=4)) == 2


def test_a_refused_publish_raises_and_stores_nothing(bus):
    bus.fail_next = 1
    with pytest.raises(BusError):
        bus.publish(msgs("t.a", 2))
    assert bus.read("t.a") == []


@pytest.mark.parametrize("url", ["redis://x", "nats://x:4222"])
def test_an_unknown_bus_is_named_in_the_error(url):
    with pytest.raises(BusError, match=url.split(":")[0]):
        connect(url)


def test_no_bus_is_configured_by_default():
    assert connect("") is None


# ---- the relay

def write_events(db, n, tenant=T, case="case-1"):
    with db.tenant(tenant) as s:
        for i in range(n):
            record(s, case, "x", "system", "note.added", {"i": i})
        s.commit()


def test_the_relay_publishes_in_order_and_marks_rows(world):
    write_events(world, 7)
    assert relay.drain(world.bus) == 7
    out = world.bus.read("caseforge.meridian.case-events")
    assert [json.loads(x.value)["data"]["payload"]["i"] for x in out] == list(range(7))
    assert all(x.key == "case-1" and x.headers["content-type"] == "application/cloudevents+json" and x.headers["type"] == "caseforge.note.added" for x in out)
    assert [x.headers["id"] for x in out] == [r.event_id for r in outbox_rows(world)]
    assert all(r.published_at is not None for r in outbox_rows(world)) and relay.drain(world.bus) == 0


@pytest.mark.parametrize("batch", [1, 3, 500])
def test_the_relay_empties_the_outbox_whatever_the_batch_size(world, batch):
    write_events(world, 10)
    assert relay.drain(world.bus, batch=batch) == 10 and len(world.bus.read("caseforge.meridian.case-events")) == 10


def test_a_limit_stops_the_relay_early(world):
    write_events(world, 10)
    assert relay.drain(world.bus, batch=4, limit=6) == 6
    assert sum(r.published_at is None for r in outbox_rows(world)) == 4


def test_a_bus_failure_leaves_the_rows_waiting_and_the_next_run_sends_them_once(world):
    write_events(world, 3)
    world.bus.fail_next = 1
    with pytest.raises(BusError):
        relay.drain(world.bus)
    assert all(r.published_at is None for r in outbox_rows(world)) and world.bus.read("caseforge.meridian.case-events") == []
    assert relay.drain(world.bus) == 3 and len(world.bus.read("caseforge.meridian.case-events")) == 3


def test_a_crash_after_publishing_resends_rather_than_loses(world, monkeypatch):
    """The bus has the batch but the mark was never committed: the next run publishes it again with the same ids."""
    write_events(world, 2)
    with world.platform() as p:
        assert relay.publish_batch(p, world.bus) == 2
        p.rollback()
    assert relay.drain(world.bus) == 2
    ids = [x.headers["id"] for x in world.bus.read("caseforge.meridian.case-events")]
    assert len(ids) == 4 and len(set(ids)) == 2


def test_each_clients_events_go_to_its_own_topic(world):
    write_events(world, 2, T, "case-m")
    write_events(world, 3, OTHER, "case-k")
    assert relay.drain(world.bus) == 5
    assert len(world.bus.read("caseforge.meridian.case-events")) == 2 and len(world.bus.read("caseforge.kestrel.case-events")) == 3
    assert all(json.loads(x.value)["tenantid"] == OTHER for x in world.bus.read("caseforge.kestrel.case-events"))


def test_the_relay_stands_down_when_change_data_capture_publishes(world):
    write_events(world, 2)
    object.__setattr__(settings, "outbox_publisher", "debezium")
    assert relay.drain(world.bus) == 0 and world.bus.read("caseforge.meridian.case-events") == []


def age(db, days):
    with db.platform() as p:
        for r in p.scalars(select(m.OutboxEvent)):
            r.created = m.now() - timedelta(days=days)
        p.commit()


def test_pruning_removes_only_old_published_rows(world):
    write_events(world, 3)
    relay.drain(world.bus)
    age(world, 30)
    write_events(world, 2)                # new and unpublished
    relay.drain(world.bus, limit=1)      # one new and published
    assert relay.prune(keep_days=7) == 3 and len(outbox_rows(world)) == 2


def test_pruning_never_removes_an_unpublished_row_however_old(world):
    write_events(world, 2)
    age(world, 400)
    assert relay.prune(keep_days=7) == 0 and len(outbox_rows(world)) == 2


def test_under_change_data_capture_age_alone_prunes(world):
    write_events(world, 2)
    age(world, 30)
    write_events(world, 1)
    object.__setattr__(settings, "outbox_publisher", "debezium")
    assert relay.prune(keep_days=7) == 2 and len(outbox_rows(world)) == 1


# ---- the transaction stream

def count(db, model, tenant=T):
    with db.tenant(tenant) as s:
        return s.scalar(select(func.count()).select_from(model))


def stat(db, stream=topics.TRANSACTIONS, tenant=T):
    with db.tenant(tenant) as s:
        return s.scalars(select(m.StreamStat).where(m.StreamStat.stream == stream)).first()


def test_transactions_on_the_stream_are_loaded_and_detection_runs(world):
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(i, amount=9100 + i) for i in range(4)])
    done = StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    assert done[T].loaded == 4 and done[T].alerts >= 1 and done[T].rejects == []
    assert count(world, m.Transaction) == 4 and count(world, m.Alert) >= 1 and count(world, m.Case) >= 1
    with world.tenant(T) as s:
        a = s.scalars(select(m.Alert).where(m.Alert.scenario == "structuring")).one()
        assert sorted(a.txn_ids) == ["T0", "T1", "T2", "T3"] and a.party_id == "P1"
        assert s.scalars(select(m.Transaction.feed_id)).first() == "stream"
    st = stat(world)
    assert (st.received, st.loaded, st.duplicates, st.rejected) == (4, 4, 0, 0) and st.alerts >= 1 and st.last_at is not None


def test_payments_below_the_scenario_raise_nothing(world):
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(i, amount=120) for i in range(4)])
    done = StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    assert done[T].loaded == 4 and done[T].alerts == 0 and count(world, m.Alert) == 0


def test_the_same_payment_twice_is_loaded_once(world):
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(1), txn(1), txn(2)])
    c = StreamConsumer(world.bus, topics.TRANSACTIONS)
    assert (c.step()[T].loaded, count(world, m.Transaction)) == (2, 2)
    send(world, topics.TRANSACTIONS, [txn(2), txn(3)])
    out = c.step()[T]
    assert (out.loaded, out.duplicates, count(world, m.Transaction)) == (1, 1, 3)
    assert stat(world).duplicates == 2


def test_a_redelivered_batch_changes_nothing(world):
    """A second consumer in the same group that never saw the commit: same messages, no new rows, alerts or cases."""
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(i, amount=9100 + i) for i in range(4)])
    first = StreamConsumer(world.bus, topics.TRANSACTIONS, group="g1")
    first.step()
    before = (count(world, m.Transaction), count(world, m.Alert), count(world, m.Case), count(world, m.CaseEvent))
    replay = StreamConsumer(world.bus, topics.TRANSACTIONS, group="g2").step()[T]
    assert (replay.loaded, replay.duplicates, replay.alerts) == (0, 4, 0)
    assert (count(world, m.Transaction), count(world, m.Alert), count(world, m.Case), count(world, m.CaseEvent)) == before


BAD = [
    (b"not json", "not valid JSON"),
    (b"[1, 2]", "not a JSON object"),
    (json.dumps({k: v for k, v in txn(1).items() if k != "amount"}).encode(), "missing amount"),
    (json.dumps(txn(1, channel="pigeon")).encode(), "unknown channel"),
    (json.dumps(txn(1, direction="sideways")).encode(), "unknown direction"),
    (json.dumps(txn(1, ts="last tuesday")).encode(), "unrecognised timestamp"),
    (json.dumps(txn(1, txn_id=None)).encode(), "missing txn_id"),
]


@pytest.mark.parametrize("value,reason", BAD, ids=[r for _, r in BAD])
def test_an_unusable_record_goes_to_the_dead_letter_topic_with_the_reason(world, value, reason):
    customer(world)
    send(world, topics.TRANSACTIONS, [value, json.dumps(txn(2)).encode()], raw=True)
    out = StreamConsumer(world.bus, topics.TRANSACTIONS).step()[T]
    assert out.loaded == 1 and len(out.rejects) == 1  # the good record beside it still loads
    (d,) = dead(world)
    assert reason in d.headers["dl_reason"] and d.value == value and d.headers["dl_topic"] == "caseforge.meridian.transactions" and d.headers["dl_offset"] == "0"
    assert stat(world).rejected == 1 and reason in stat(world).last_reject


def test_a_payment_for_an_unknown_customer_is_set_aside_by_default(world):
    send(world, topics.TRANSACTIONS, [txn(1, party="GHOST", account="AG")])
    out = StreamConsumer(world.bus, topics.TRANSACTIONS).step()[T]
    assert out.loaded == 0 and "GHOST is not known" in dead(world)[0].headers["dl_reason"] and count(world, m.Party) == 0


def test_a_client_can_choose_to_create_unknown_customers(world):
    object.__setattr__(settings, "stream_unknown_customer", "create")
    send(world, topics.TRANSACTIONS, [txn(1, party="NEW", account="AN")])
    out = StreamConsumer(world.bus, topics.TRANSACTIONS).step()[T]
    assert out.loaded == 1 and dead(world) == [] and count(world, m.Party) == 1


def test_an_account_that_belongs_to_someone_else_is_rejected(world):
    customer(world)
    customer(world, party="P2", account="A2")
    send(world, topics.TRANSACTIONS, [txn(1, party="P2", account="A1")])
    StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    assert "does not belong" in dead(world)[0].headers["dl_reason"]


def test_a_cloudevent_wrapper_is_accepted(world):
    customer(world)
    send(world, topics.TRANSACTIONS, [outbox.envelope(T, "transaction", "T1", NOW, txn(1))])
    assert StreamConsumer(world.bus, topics.TRANSACTIONS).step()[T].loaded == 1


def test_a_message_on_one_clients_topic_never_lands_at_another(world):
    for t in (T, OTHER):
        customer(world, t)
    send(world, topics.TRANSACTIONS, [txn(i, amount=9100) for i in range(4)], tenant=OTHER)
    done = StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    assert set(done) == {OTHER} and count(world, m.Transaction, T) == 0 and count(world, m.Transaction, OTHER) == 4
    assert count(world, m.Alert, T) == 0 and count(world, m.OutboxEvent, T) == 0 and count(world, m.OutboxEvent, OTHER) > 0
    assert stat(world, tenant=T) is None and stat(world, tenant=OTHER).loaded == 4


def test_the_same_ids_at_two_clients_are_two_sets_of_payments(world):
    for t in (T, OTHER):
        customer(world, t)
        send(world, topics.TRANSACTIONS, [txn(1), txn(2)], tenant=t)
    done = StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    assert done[T].loaded == 2 and done[OTHER].loaded == 2


@pytest.mark.parametrize("state", ["suspended", "missing"])
def test_messages_for_a_client_that_is_not_live_wait_on_its_dead_letter_topic(world, state):
    tenant = OTHER if state == "suspended" else "nobody"
    if state == "suspended":
        with world.platform() as p:
            set_status(p, OTHER, "suspended")
            p.commit()
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(1)], tenant=tenant)
    send(world, topics.TRANSACTIONS, [txn(2)])
    done = StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    assert done[T].loaded == 1 and dead(world, tenant)[0].headers["dl_reason"] == "this client is not live"
    assert count(world, m.Transaction, OTHER) == 0


def test_a_client_onboarded_elsewhere_is_found_by_reloading(world):
    """A worker beside the API learns of a new client the first time a message for it arrives."""
    rt = registry.runtime(OTHER)
    saved = registry.swap({T: registry.runtime(T)})
    customer(world, OTHER)
    send(world, topics.TRANSACTIONS, [txn(1)], tenant=OTHER)
    calls = []
    done = StreamConsumer(world.bus, topics.TRANSACTIONS, refresh=lambda: (calls.append(1), registry.install(rt))).step()
    registry.swap(saved)
    assert calls == [1] and done[OTHER].loaded == 1 and dead(world, OTHER) == []


def test_a_failed_batch_is_not_committed_and_comes_again(world, monkeypatch):
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(1), txn(2)])
    real = streams.HANDLERS[topics.TRANSACTIONS]
    state = {"fail": True}

    def flaky(s, batch):
        out = real(s, batch)
        if state.pop("fail", False):
            raise RuntimeError("database went away")
        return out

    monkeypatch.setitem(streams.HANDLERS, topics.TRANSACTIONS, flaky)
    c = StreamConsumer(world.bus, topics.TRANSACTIONS)
    with pytest.raises(RuntimeError):
        c.step()
    assert count(world, m.Transaction) == 0 and stat(world) is None  # the whole batch rolled back
    assert c.step()[T].loaded == 2 and c.step() == {}


def test_a_failed_batch_goes_back_in_place_without_leaving_the_group(world, monkeypatch):
    """Leaving and rejoining the group makes every consumer beside this one rebalance, and their commits then fail."""
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(1), txn(2)])
    real = streams.HANDLERS[topics.TRANSACTIONS]
    state = {"fail": True}

    def flaky(s, batch):
        if state.pop("fail", False):
            raise RuntimeError("database went away")
        return real(s, batch)

    monkeypatch.setitem(streams.HANDLERS, topics.TRANSACTIONS, flaky)
    c = StreamConsumer(world.bus, topics.TRANSACTIONS)
    with pytest.raises(RuntimeError):
        c.step()
    sub = c._sub
    assert sub is not None  # the same membership, wound back
    assert c.step()[T].loaded == 2 and c._sub is sub and count(world, m.Transaction) == 2


def test_the_consumer_that_loses_the_race_to_start_a_watchlist_uses_the_winners(world, monkeypatch):
    """Two consumers can reach a new client's first payments at once, and both find no watchlist. The second insert
    meets the first one's row: it must use that list, not fail its whole batch."""
    from app.db import session_scope
    from app.sanctions import lists

    with session_scope(T) as s:
        s.add(m.Watchlist(id="wl-winner", code=lists.STARTER["code"], name="Made by the other consumer", kind=lists.STARTER["kind"],
                          source="test", history=[]))
    with session_scope(T) as s:
        real, looked = s.scalars, []

        class Nothing:
            def first(self):
                return None

        def first_look_misses(stmt, *a, **k):  # as if the other consumer had not committed yet when this one looked
            if not looked:
                looked.append(1)
                return Nothing()
            return real(stmt, *a, **k)

        monkeypatch.setattr(s, "scalars", first_look_misses)
        lists.ensure(s)  # meets the unique code, steps back to the savepoint, finds the winner's list
        monkeypatch.setattr(s, "scalars", real)
        assert [w.id for w in s.scalars(select(m.Watchlist))] == ["wl-winner"]


def test_rejects_are_published_before_the_position_moves(world):
    """If the dead-letter publish fails the batch is retried; the rejects are published on the retry, not dropped."""
    customer(world)
    send(world, topics.TRANSACTIONS, [b"junk"], raw=True)
    c = StreamConsumer(world.bus, topics.TRANSACTIONS)
    world.bus.fail_next = 1
    with pytest.raises(BusError):
        c.step()
    assert dead(world) == []
    c.step()
    assert len(dead(world)) == 1


def test_an_idle_stream_does_nothing(world):
    assert StreamConsumer(world.bus, topics.TRANSACTIONS).step() == {}


# ---- the alert stream

def ext(i=1, **over):
    return {"source": "actimize", "external_id": f"X{i}", "scenario": "structuring", "party_id": "P1", "score": 0.91, "explanation": "Cash just under the threshold", **over}


def test_an_alert_from_another_engine_opens_a_case(world):
    customer(world)
    send(world, topics.ALERTS_IN, [ext()])
    out = StreamConsumer(world.bus, topics.ALERTS_IN).step()[T]
    assert out.loaded == 1 and count(world, m.Alert) == 1 and count(world, m.Case) == 1
    with world.tenant(T) as s:
        a = s.scalars(select(m.Alert)).one()
        assert a.source == "actimize" and a.dedupe_key == "ext:actimize:X1" and a.scenario_version == "external"


def test_a_replayed_alert_is_ignored(world):
    customer(world)
    send(world, topics.ALERTS_IN, [ext(), ext(), ext(2)])
    c = StreamConsumer(world.bus, topics.ALERTS_IN)
    out = c.step()[T]
    assert (out.loaded, out.duplicates, count(world, m.Alert)) == (2, 1, 2)
    events = count(world, m.CaseEvent)
    send(world, topics.ALERTS_IN, [ext()])
    assert c.step()[T].duplicates == 1 and count(world, m.Alert) == 2 and count(world, m.CaseEvent) == events


BAD_ALERTS = [({"score": 1.4}, "between 0 and 1"), ({"score": "high"}, "could not convert"), ({"party_id": ""}, "missing party_id"),
              ({"explanation": None}, "missing explanation"), ({"source": "s" * 41}, "longer than"), ({"external_id": "e" * 71}, "longer than")]


@pytest.mark.parametrize("change,reason", BAD_ALERTS, ids=[r for _, r in BAD_ALERTS])
def test_an_unusable_alert_is_set_aside(world, change, reason):
    customer(world)
    send(world, topics.ALERTS_IN, [ext(**change), ext(2)])
    out = StreamConsumer(world.bus, topics.ALERTS_IN).step()[T]
    assert out.loaded == 1 and reason in dead(world)[0].headers["dl_reason"] and count(world, m.Alert) == 1


def test_an_alert_for_an_unknown_customer_creates_the_customer(world):
    """As POST /api/alerts does: the sending engine is the authority on who the alert is about."""
    send(world, topics.ALERTS_IN, [ext(party_id="P77", party_name="Rosa Marin")])
    StreamConsumer(world.bus, topics.ALERTS_IN).step()
    with world.tenant(T) as s:
        assert s.scalars(select(m.Party.name)).one() == "Rosa Marin"


# ---- end to end

def test_a_payment_in_becomes_an_alert_event_out(world):
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(i, amount=9100 + i) for i in range(4)])
    StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    assert relay.drain(world.bus) > 0
    (alert,) = [json.loads(x.value) for x in world.bus.read("caseforge.meridian.alerts")]
    assert alert["type"] == "caseforge.alert.created" and alert["data"]["scenario"] == "structuring" and alert["data"]["txn_ids"] == ["T0", "T1", "T2", "T3"]
    case_types = [json.loads(x.value)["type"] for x in world.bus.read("caseforge.meridian.case-events")]
    assert "caseforge.case.created" in case_types and "caseforge.alert.attached" in case_types
    assert world.bus.read("caseforge.kestrel.alerts") == []


def test_events_for_one_case_keep_their_order_on_the_bus(world):
    customer(world)
    send(world, topics.ALERTS_IN, [ext(i) for i in range(1, 6)])
    StreamConsumer(world.bus, topics.ALERTS_IN).step()
    relay.drain(world.bus, batch=2)
    with world.tenant(T) as s:
        want = [(e.case_id, e.kind) for e in s.scalars(select(m.CaseEvent).order_by(m.CaseEvent.id))]
    got = [(json.loads(x.value)["subject"], json.loads(x.value)["type"].removeprefix("caseforge.")) for x in world.bus.read("caseforge.meridian.case-events")]
    assert got == want and len(got) >= 6


# ---- what a client sees

def test_the_status_view_counts_only_this_clients_events(world):
    write_events(world, 3)
    write_events(world, 5, OTHER)
    relay.drain(world.bus, limit=2)
    with world.tenant(T) as s:
        v = status.view(s)
    assert v["bus"]["kind"] == "memory" and v["publisher"] == "relay"
    assert v["outbox"]["total"] == 3 and v["outbox"]["waiting"] == 1 and v["outbox"]["published"] == 2 and v["outbox"]["last_published"]
    assert v["outbox"]["by_type"] == [{"type": "caseforge.note.added", "count": 3}]
    assert v["topics"]["in"] == ["caseforge.meridian.transactions", "caseforge.meridian.alerts-in"] and v["topics"]["dead_letter"] == "caseforge.meridian.dead-letter"
    assert [x["received"] for x in v["streams"]] == [0, 0]


def test_the_status_view_reports_stream_totals(world):
    customer(world)
    send(world, topics.TRANSACTIONS, [txn(1)])
    send(world, topics.TRANSACTIONS, [b"junk", b"more junk"], raw=True)
    StreamConsumer(world.bus, topics.TRANSACTIONS).step()
    with world.tenant(T) as s:
        tx = status.view(s)["streams"][0]
    assert (tx["stream"], tx["received"], tx["loaded"], tx["rejected"]) == ("transactions", 3, 1, 2) and tx["last_reject"]
    with world.tenant(OTHER) as s:
        assert status.view(s)["streams"][0]["received"] == 0


def test_without_a_bus_the_view_says_events_are_waiting(world):
    write_events(world, 2)
    object.__setattr__(settings, "event_bus_url", "")
    with world.tenant(T) as s:
        v = status.view(s)
    assert v["bus"]["kind"] == "none" and "outbox" in v["bus"]["note"] and v["outbox"]["waiting"] == 2


def test_under_change_data_capture_the_view_does_not_claim_a_backlog(world):
    write_events(world, 2)
    object.__setattr__(settings, "outbox_publisher", "debezium")
    with world.tenant(T) as s:
        v = status.view(s)
    assert v["publisher"] == "debezium" and v["outbox"]["waiting"] is None and v["outbox"]["total"] == 2


def test_the_api_shows_each_client_its_own_backbone(client):
    seen = {}
    for t in client.get("/api/tenants").json():
        h = {"X-Tenant": t["id"], "X-Persona": t["default_person"]}
        r = client.get("/api/backbone", headers=h)
        assert r.status_code == 200
        v = seen[t["id"]] = r.json()
        assert v["outbox"]["total"] > 0 and v["topics"]["out"][0] == f"caseforge.{t['id']}.case-events" and len(v["streams"]) == 2
    assert len(seen) >= 2
    live = {c["id"]: c["status"] for c in client.get("/api/integrations", headers=h).json()["connectors"]}
    assert live["kafka"] == "live" and live["cdc"] == "planned"


def test_consumers_side_by_side_never_lose_each_others_counts(world):
    """Two consumers holding the same totals row: each adds in the database, so neither overwrites the other."""
    with world.tenant(T) as a:
        streams._count(a, topics.TRANSACTIONS, streams.Outcome(received=1, loaded=1))
        a.commit()
    with world.tenant(T) as a, world.tenant(T) as b:
        b.scalars(select(m.StreamStat)).all()  # b has read the row before a changes it
        streams._count(a, topics.TRANSACTIONS, streams.Outcome(received=5, loaded=4, duplicates=1))
        a.commit()
        streams._count(b, topics.TRANSACTIONS, streams.Outcome(received=7, loaded=7, alerts=2))
        b.commit()
    with world.tenant(T) as c:
        st = c.scalars(select(m.StreamStat)).one()
        assert (st.received, st.loaded, st.duplicates, st.alerts) == (13, 12, 1, 2)
