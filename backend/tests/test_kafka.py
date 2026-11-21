"""The backbone against a real Kafka broker: the bus contract, the relay and the inbound streams.

Runs when TEST_KAFKA_URL names a disposable broker, for example TEST_KAFKA_URL=kafka://127.0.0.1:9092. Every test
uses its own topic prefix, so a run leaves topics behind but never reads another run's messages.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import app.db as appdb
from app.backbone import relay, topics
from app.backbone.bus import BusError, Message, connect
from app.backbone.streams import StreamConsumer
from app.config import settings
from app.domain import models as m
from app.tenants import registry
from conftest import Db
from support import T
from test_backbone import OTHER, count, customer, ext, outbox_rows, txn, write_events

URL = os.getenv("TEST_KAFKA_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("kafka://"), reason="set TEST_KAFKA_URL to a disposable Kafka broker to run")


@pytest.fixture
def world():
    saved = registry.swap({})
    db = Db()
    old = appdb.engine, appdb.SessionLocal, settings.event_topic_prefix
    appdb.engine, appdb.SessionLocal = db.engine, sessionmaker(bind=db.engine, expire_on_commit=False)
    for k, v in {"event_bus_url": URL, "outbox_publisher": "relay", "stream_unknown_customer": "reject", "event_topic_prefix": f"cft{uuid.uuid4().hex[:10]}"}.items():
        object.__setattr__(settings, k, v)
    db.onboard(T)
    db.onboard(OTHER)
    db.bus = connect()
    db.bus.ensure_topics(topics.topic(t, n) for t in (T, OTHER) for n in topics.ALL)
    yield db
    db.bus.close()
    object.__setattr__(settings, "event_bus_url", "")
    object.__setattr__(settings, "event_topic_prefix", old[2])
    appdb.engine, appdb.SessionLocal = old[0], old[1]
    registry.swap(saved)


def read(sub, n, seconds=30):
    """Poll until n messages have arrived. Joining a group takes a broker a few seconds."""
    got, end = [], time.monotonic() + seconds
    while len(got) < n and time.monotonic() < end:
        got += sub.poll(timeout=1.0)
    return got


def steps(consumer, want, seconds=30):
    """Run the consumer until it has taken `want` messages for the reference client."""
    total, last, end = 0, None, time.monotonic() + seconds
    while total < want and time.monotonic() < end:
        for tenant, out in consumer.step(timeout=1.0).items():
            if tenant == T:
                total, last = total + out.received, out
    assert total == want, f"only {total} of {want} messages arrived"
    return last


def test_a_message_keeps_its_key_value_and_headers(world):
    name = topics.topic(T, topics.ALERTS)
    assert world.bus.publish([Message(name, "P1", b'{"a":1}', {"content-type": "application/cloudevents+json", "id": "e1"})]) == 1
    (got,) = read(world.bus.subscribe(f"g-{uuid.uuid4().hex}", topics.pattern(topics.ALERTS)), 1)
    assert (got.topic, got.key, got.value) == (name, "P1", b'{"a":1}') and got.headers == {"content-type": "application/cloudevents+json", "id": "e1"}


def test_messages_with_one_key_stay_in_order_across_partitions(world):
    name = topics.topic(T, topics.CASE_EVENTS)
    world.bus.publish(Message(name, f"case-{i % 7}", str(i).encode()) for i in range(700))
    got = read(world.bus.subscribe(f"g-{uuid.uuid4().hex}", topics.pattern(topics.CASE_EVENTS)), 700)
    assert len(got) == 700 and len({x.partition for x in got}) > 1  # the topic really is spread out
    for k in range(7):
        mine = [int(x.value) for x in got if x.key == f"case-{k}"]
        assert mine == sorted(mine) and len(mine) == 100


def test_one_subscription_reads_every_clients_topic_of_its_kind_only(world):
    world.bus.publish([Message(topics.topic(T, topics.TRANSACTIONS), "a", b"1"), Message(topics.topic(OTHER, topics.TRANSACTIONS), "b", b"2"),
                       Message(topics.topic(T, topics.ALERTS_IN), "c", b"3")])
    got = read(world.bus.subscribe(f"g-{uuid.uuid4().hex}", topics.pattern(topics.TRANSACTIONS)), 2)
    assert sorted(x.value for x in got) == [b"1", b"2"]


def test_what_was_not_committed_is_delivered_again(world):
    name, group = topics.topic(T, topics.ALERTS), f"g-{uuid.uuid4().hex}"
    world.bus.publish(Message(name, "k", str(i).encode()) for i in range(3))
    first = world.bus.subscribe(group, topics.pattern(topics.ALERTS))
    assert len(read(first, 3)) == 3
    first.close()  # never committed
    second = world.bus.subscribe(group, topics.pattern(topics.ALERTS))
    assert [x.value for x in read(second, 3)] == [b"0", b"1", b"2"]
    second.commit()
    second.close()
    world.bus.publish([Message(name, "k", b"3")])
    assert [x.value for x in read(world.bus.subscribe(group, topics.pattern(topics.ALERTS)), 1)] == [b"3"]


def test_creating_topics_twice_is_harmless(world):
    names = [topics.topic(T, n) for n in topics.ALL]
    world.bus.ensure_topics(names)
    assert set(names) <= set(world.bus.topics())


def test_an_unreachable_broker_is_reported_not_hung():
    from app.backbone.kafka import KafkaBus

    bus = KafkaBus("kafka://127.0.0.1:1")
    with pytest.raises(BusError, match="did not acknowledge"):
        bus.publish([Message("t", "k", b"v")], timeout=3)
    with pytest.raises(BusError, match="cannot be reached"):
        bus.topics()


def test_the_relay_publishes_the_outbox_to_kafka_in_order(world):
    write_events(world, 25, case="case-a")
    write_events(world, 25, case="case-b")
    write_events(world, 5, OTHER, "case-k")
    assert relay.drain(world.bus, batch=10) == 55 and relay.drain(world.bus) == 0
    got = read(world.bus.subscribe(f"g-{uuid.uuid4().hex}", topics.pattern(topics.CASE_EVENTS)), 55)
    assert len(got) == 55 and {x.headers["id"] for x in got} == {r.event_id for t in (T, OTHER) for r in outbox_rows(world, t)}
    for case in ("case-a", "case-b"):
        assert [json.loads(x.value)["data"]["payload"]["i"] for x in got if x.key == case] == list(range(25))
    assert all(json.loads(x.value)["tenantid"] == OTHER for x in got if x.topic == topics.topic(OTHER, topics.CASE_EVENTS))
    assert all(x.headers["content-type"] == "application/cloudevents+json" for x in got)


def test_payments_on_kafka_become_alerts_and_alert_events_on_kafka(world):
    customer(world)
    world.bus.publish(Message(topics.topic(T, topics.TRANSACTIONS), "P1", json.dumps(txn(i, amount=9100 + i)).encode()) for i in range(4))
    c = StreamConsumer(world.bus, topics.TRANSACTIONS)
    steps(c, 4)
    c.close()
    assert count(world, m.Transaction) == 4
    with world.tenant(T) as s:
        assert sorted(s.scalars(select(m.Alert).where(m.Alert.scenario == "structuring")).one().txn_ids) == ["T0", "T1", "T2", "T3"]
    assert relay.drain(world.bus) > 0
    (alert,) = read(world.bus.subscribe(f"g-{uuid.uuid4().hex}", topics.pattern(topics.ALERTS)), 1)
    body = json.loads(alert.value)
    assert alert.key == "P1" and body["type"].endswith(".alert.created") and body["data"]["scenario"] == "structuring"


def test_a_consumer_that_restarts_without_committing_reloads_nothing_twice(world):
    customer(world)
    world.bus.publish(Message(topics.topic(T, topics.TRANSACTIONS), "P1", json.dumps(txn(i, amount=9100 + i)).encode()) for i in range(4))
    group = f"g-{uuid.uuid4().hex}"
    sub = world.bus.subscribe(group, topics.pattern(topics.TRANSACTIONS))
    assert len(read(sub, 4)) == 4
    sub.close()  # a consumer that died after reading
    c = StreamConsumer(world.bus, topics.TRANSACTIONS, group=group)
    assert steps(c, 4).loaded == 4
    c.close()
    before = (count(world, m.Transaction), count(world, m.Alert), count(world, m.CaseEvent))
    replay = StreamConsumer(world.bus, topics.TRANSACTIONS, group=f"g-{uuid.uuid4().hex}")  # sees the topic from the start
    out = steps(replay, 4)
    replay.close()
    assert (out.loaded, out.duplicates) == (0, 4) and (count(world, m.Transaction), count(world, m.Alert), count(world, m.CaseEvent)) == before


def test_bad_records_reach_the_dead_letter_topic_on_kafka(world):
    customer(world)
    name = topics.topic(T, topics.TRANSACTIONS)
    world.bus.publish([Message(name, "P1", b"not json"), Message(name, "P1", json.dumps(txn(1)).encode()), Message(name, "GHOST", json.dumps(txn(2, party="GHOST", account="AG")).encode())])
    c = StreamConsumer(world.bus, topics.TRANSACTIONS)
    steps(c, 3)
    c.close()
    got = read(world.bus.subscribe(f"g-{uuid.uuid4().hex}", topics.pattern(topics.DEAD_LETTER)), 2)
    assert sorted(x.headers["dl_reason"] for x in got) == ["customer GHOST is not known", "not valid JSON"]
    assert all(x.headers["dl_topic"] == name for x in got) and count(world, m.Transaction) == 1


def test_alerts_from_another_engine_arrive_over_kafka(world):
    customer(world)
    world.bus.publish(Message(topics.topic(T, topics.ALERTS_IN), "P1", json.dumps(ext(i)).encode()) for i in (1, 2, 1))
    c = StreamConsumer(world.bus, topics.ALERTS_IN)
    steps(c, 3)
    c.close()
    assert count(world, m.Alert) == 2 and count(world, m.Case) == 1


def test_the_worker_loop_runs_relay_and_streams_together(world):
    import threading

    from app.backbone import worker

    customer(world)
    world.bus.publish(Message(topics.topic(T, topics.TRANSACTIONS), "P1", json.dumps(txn(i, amount=9100 + i)).encode()) for i in range(4))
    stop = threading.Event()
    t = threading.Thread(target=worker.run, args=(world.bus, stop), daemon=True)
    t.start()
    got = read(world.bus.subscribe(f"g-{uuid.uuid4().hex}", topics.pattern(topics.ALERTS)), 1, seconds=45)
    stop.set()
    t.join(20)
    assert not t.is_alive() and len(got) == 1 and json.loads(got[0].value)["data"]["scenario"] == "structuring"
    assert all(r.published_at is not None for r in outbox_rows(world))
