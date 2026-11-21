"""The structuring job on a real Apache Flink cluster, reading and writing a real Kafka broker.

Runs when all three are set:
    TEST_KAFKA_URL=kafka://127.0.0.1:9092      a disposable broker
    TEST_FLINK_URL=http://127.0.0.1:8081       a running Flink cluster that can reach that broker
    TEST_FLINK_HOME=/path/to/flink             its distribution, with flink-sql-connector-kafka in lib/

Each test submits its own job under its own topic prefix and cancels it afterwards.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.backbone import status, topics
from app.backbone.streams import StreamConsumer
from app.config import settings
from app.domain import models as m
from app.streamjobs import flink
from app.streamjobs.jobs import render
from support import T
from test_backbone import NOW, OTHER, count, customer, send, txn
from test_detection_governance import live
from test_kafka import URL, read, world  # noqa: F401  fixture

FLINK, HOME = os.getenv("TEST_FLINK_URL", ""), os.getenv("TEST_FLINK_HOME", "")
pytestmark = pytest.mark.skipif(not (URL.startswith("kafka://") and FLINK.startswith("http") and HOME),
                                reason="set TEST_KAFKA_URL, TEST_FLINK_URL and TEST_FLINK_HOME to run")


@pytest.fixture
def jobs(world, tmp_path):  # noqa: F811
    """Submit rendered jobs to the cluster; cancel them when the test ends."""
    object.__setattr__(settings, "flink_url", FLINK)
    started: list[str] = []

    def submit(tenant=T, params=None):
        path = tmp_path / f"{tenant}.sql"
        path.write_text(render("structuring", tenant, params))
        done = subprocess.run([os.path.join(HOME, "bin", "sql-client.sh"), "-f", str(path)], capture_output=True, text=True, timeout=180, check=False)
        assert "Job ID:" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        started.append(done.stdout.split("Job ID:")[1].split()[0])
        end = time.monotonic() + 60
        while flink.status(tenant)["jobs"][0]["state"] != "RUNNING" and time.monotonic() < end:
            time.sleep(1)
        assert flink.status(tenant)["jobs"][0]["state"] == "RUNNING"

    yield submit
    object.__setattr__(settings, "flink_url", "")
    for jid in started:
        urllib.request.urlopen(urllib.request.Request(f"{FLINK}/jobs/{jid}?mode=cancel", method="PATCH"), timeout=10).close()


def later(i, day):
    """A small card payment after everything else. Event time only moves when traffic does."""
    return txn(0, party="P-OTHER", account="A-OTHER", amount=12, direction="out", channel="card") | {
        "txn_id": f"N{day}-{i}", "ts": (NOW + timedelta(days=day, hours=3, minutes=i)).isoformat()}


def alerts(world, tenant, want, seconds=120, day=20, group="probe"):  # noqa: F811
    """The alerts the job has written for a client, nudging event time along to `day` until `want` have arrived."""
    sub = world.bus.subscribe(f"{group}-{tenant}-{time.monotonic_ns()}", f"^{topics.topic(tenant, topics.ALERTS_IN).replace('.', chr(92) + '.')}$")
    got, end, i = [], time.monotonic() + seconds, 0
    try:
        while len(got) < want and time.monotonic() < end:
            # A partition that has been quiet for ten seconds stops holding event time back, so keep the nudges coming.
            send(world, topics.TRANSACTIONS, [later(i, day)], tenant=tenant)
            i += 1
            stop = time.monotonic() + 6
            while len(got) < want and time.monotonic() < stop:
                got += sub.poll(timeout=1.0)
    finally:
        sub.close()
    return [json.loads(x.value) for x in got], got


def test_three_deposits_on_the_topic_become_one_alert_in_the_canonical_format(world, jobs):  # noqa: F811
    jobs()
    send(world, topics.TRANSACTIONS, [txn(1, amount=9000), txn(2, amount=9500.5, day=1), txn(3, amount=8200, day=2),
                                      txn(4, amount=9100, day=1, channel="wire"), txn(5, amount=700, day=1), txn(6, party="P2", amount=9100)])
    world.bus.publish([topics_message(T, b"not json at all")])
    found, raw = alerts(world, T, 1)
    assert len(found) == 1, found
    (a,) = found
    assert a == {"source": "flink-structuring", "external_id": f"P1:{(NOW + timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%S')}", "scenario": "structuring",
                 "party_id": "P1", "score": 0.79, "explanation": "3 cash deposits between $8,000 and $10,000 within 7 days, totalling $26701.",
                 "txn_ids": ["T1", "T2", "T3"]}
    assert raw[0].key == "P1"


def topics_message(tenant, value):
    from app.backbone.bus import Message

    return Message(topics.topic(tenant, topics.TRANSACTIONS), "P1", value)


def test_the_alert_opens_a_case_through_the_ordinary_alert_stream(world, jobs):  # noqa: F811
    customer(world)
    with world.tenant(T) as s:
        live(s, enabled=False, note="Flink raises this rule for this client from now on.")
        s.commit()
    jobs()
    send(world, topics.TRANSACTIONS, [txn(i, day=i) for i in (1, 2, 3)])
    found, _ = alerts(world, T, 1)
    assert len(found) == 1
    loader, hub = StreamConsumer(world.bus, topics.TRANSACTIONS), StreamConsumer(world.bus, topics.ALERTS_IN)
    end = time.monotonic() + 60
    while count(world, m.Alert) < 1 and time.monotonic() < end:
        loader.step(timeout=1.0), hub.step(timeout=1.0)
    loader.close(), hub.close()
    with world.tenant(T) as s:
        (alert,) = s.scalars(select(m.Alert)).all()
        assert (alert.source, alert.scenario, alert.party_id, alert.score, alert.txn_ids) == ("flink-structuring", "structuring", "P1", 0.79, ["T1", "T2", "T3"])
        assert count(world, m.Case) == 1 and s.scalar(select(m.Case.party_id)) == "P1"
        assert {"T1", "T2", "T3"} <= set(s.scalars(select(m.Transaction.id)))
        assert status.view(s)["stream_jobs"]["jobs"][0] | {"since": None} == {
            "job": "structuring", "title": "Cash structuring below reporting threshold", "scenario": "structuring", "source": "flink-structuring",
            "state": "RUNNING", "since": None, "also_in_application": False}


def test_a_replayed_alert_from_the_job_opens_nothing_new(world, jobs):  # noqa: F811
    customer(world)
    jobs()
    send(world, topics.TRANSACTIONS, [txn(i, day=i) for i in (1, 2, 3)])
    _, raw = alerts(world, T, 1)
    world.bus.publish([type(raw[0])(raw[0].topic, raw[0].key, raw[0].value)])  # the same alert again, as after a job restart
    hub = StreamConsumer(world.bus, topics.ALERTS_IN, group=f"hub-{time.monotonic_ns()}")
    seen, end = 0, time.monotonic() + 60
    while seen < 2 and time.monotonic() < end:
        seen += sum(o.received for o in hub.step(timeout=1.0).values())
    hub.close()
    assert seen == 2 and count(world, m.Alert) == 1 and count(world, m.Case) == 1


def test_a_clients_job_runs_on_its_own_thresholds_and_never_reads_another_clients_topic(world, jobs):  # noqa: F811
    jobs(T, {"count": 4})
    jobs(OTHER)
    deposits = [txn(i, day=i) for i in (1, 2, 3)]
    send(world, topics.TRANSACTIONS, deposits, tenant=T)
    send(world, topics.TRANSACTIONS, deposits, tenant=OTHER)
    theirs, _ = alerts(world, OTHER, 1)
    assert [a["txn_ids"] for a in theirs] == [["T1", "T2", "T3"]]
    ours, _ = alerts(world, T, 1, seconds=25, day=3)  # event time moves past the third deposit, not past the fourth to come
    assert ours == []  # three is one short of this client's four
    send(world, topics.TRANSACTIONS, [txn(4, day=4)], tenant=T)
    ours, _ = alerts(world, T, 1)
    assert [a["txn_ids"] for a in ours] == [["T1", "T2", "T3", "T4"]] and "4 cash deposits" in ours[0]["explanation"]
    assert ours[0]["score"] == 0.87


def test_deposits_arriving_out_of_order_are_still_counted_in_order(world, jobs):  # noqa: F811
    jobs()
    send(world, topics.TRANSACTIONS, [txn(3, day=0), txn(1, day=0), txn(2, day=0)])  # minutes apart, inside the hour allowed
    found, _ = alerts(world, T, 1)
    assert [a["txn_ids"] for a in found] == [["T1", "T2", "T3"]]


def test_deposits_spread_wider_than_the_window_raise_nothing(world, jobs):  # noqa: F811
    jobs()
    send(world, topics.TRANSACTIONS, [txn(1, day=0), txn(2, day=4), txn(3, day=9)])
    found, _ = alerts(world, T, 1, seconds=30)
    assert found == []


def test_a_deposit_that_arrives_after_event_time_has_moved_on_is_not_counted_by_the_job(world, jobs):  # noqa: F811
    """The job's stated limit. The application still loads the payment and its own scenario still sees it."""
    jobs()
    send(world, topics.TRANSACTIONS, [txn(1, day=1), txn(2, day=2)])
    alerts(world, T, 1, seconds=20, day=10)  # nothing to find; this moves event time on by days
    send(world, topics.TRANSACTIONS, [txn(3, day=3)])
    found, _ = alerts(world, T, 1, seconds=30, day=11)
    assert found == []
