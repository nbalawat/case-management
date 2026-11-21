"""The stream jobs on a real Apache Flink cluster, reading and writing a real Kafka broker.

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

    def submit(tenant=T, params=None, job="structuring"):
        path = tmp_path / f"{tenant}-{job}.sql"
        path.write_text(render(job, tenant, params))
        done = subprocess.run([os.path.join(HOME, "bin", "sql-client.sh"), "-f", str(path)], capture_output=True, text=True, timeout=180, check=False)
        assert "Job ID:" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]
        started.append(done.stdout.split("Job ID:")[1].split()[0])
        state = lambda: next(j["state"] for j in flink.status(tenant)["jobs"] if j["job"] == job)  # noqa: E731
        end = time.monotonic() + 60
        while state() != "RUNNING" and time.monotonic() < end:
            time.sleep(1)
        assert state() == "RUNNING"

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


# ---- rapid movement of funds

def wire(i, amount, direction="out", hours=0.0, **over):
    return txn(i, amount=amount, direction=direction, channel="wire", **over) | {"ts": (NOW + timedelta(hours=hours, minutes=i)).isoformat()}


def test_money_that_leaves_again_within_the_window_raises_one_alert_at_the_payment_that_tips_it(world, jobs):  # noqa: F811
    jobs(job="rapid_movement")
    send(world, topics.TRANSACTIONS, [
        wire(1, 50000, "in", counterparty="Volkov Maritime Trading"), wire(2, 15000, hours=2), wire(3, 300, "in", hours=3), wire(4, 20000, hours=5),
        wire(5, 9000, hours=9), wire(6, 4000, hours=10),                                   # 40,000 is reached at T5, so T6 is not in the alert
        wire(7, 60000, "in", party="P2"), wire(8, 60000, party="P3"),                      # one customer's money in, another's out
        txn(9, party="P4", amount=50000), wire(10, 50000, party="P4", hours=1),            # cash in is the structuring rule's business
        wire(11, 30000, "in", party="P5"), wire(12, 30000, party="P5", hours=60)])         # left, but after the 48 hours
    found, raw = alerts(world, T, 1, seconds=60, day=5)
    assert found == [{"source": "flink-rapid-movement", "external_id": "P1:T1", "scenario": "rapid_movement", "party_id": "P1", "score": 0.78,
                      "explanation": "$50000 received from Volkov Maritime Trading, $44000 (88%) sent out within 48 hours.",
                      "txn_ids": ["T1", "T2", "T4", "T5"]}]
    assert raw[0].key == "P1"


def test_two_large_receipts_close_together_are_followed_separately(world, jobs):  # noqa: F811
    jobs(job="rapid_movement", params={"min_in": 10000, "out_ratio": 0.5, "hours": 24})
    send(world, topics.TRANSACTIONS, [wire(1, 20000, "in"), wire(2, 12000, "in", hours=1), wire(3, 7000, hours=2), wire(4, 4000, hours=3)])
    found, _ = alerts(world, T, 2, seconds=60, day=5)
    assert sorted((a["external_id"], a["txn_ids"], a["score"]) for a in found) == [("P1:T1", ["T1", "T3", "T4"], 0.71), ("P1:T2", ["T2", "T3"], 0.72)]
    assert "within 24 hours" in found[0]["explanation"]


def test_a_rapid_movement_alert_opens_a_case_through_the_ordinary_alert_stream(world, jobs):  # noqa: F811
    customer(world)
    jobs(job="rapid_movement")
    send(world, topics.TRANSACTIONS, [wire(1, 50000, "in"), wire(2, 45000, hours=4)])
    found, _ = alerts(world, T, 1, seconds=60, day=5)
    assert len(found) == 1
    loader, hub = StreamConsumer(world.bus, topics.TRANSACTIONS), StreamConsumer(world.bus, topics.ALERTS_IN)
    end = time.monotonic() + 60
    while not _from(world, "flink-rapid-movement") and time.monotonic() < end:
        loader.step(timeout=1.0), hub.step(timeout=1.0)
    loader.close(), hub.close()
    (alert,) = _from(world, "flink-rapid-movement")
    assert (alert.scenario, alert.party_id, alert.txn_ids) == ("rapid_movement", "P1", ["T1", "T2"]) and count(world, m.Case) == 1


def _from(world, source):  # noqa: F811
    with world.tenant(T) as s:
        return s.scalars(select(m.Alert).where(m.Alert.source == source)).all()


# ---- wires to high-risk jurisdictions

def abroad(i, amount, country="IR", day=0, **over):
    return txn(i, **({"amount": amount, "direction": "out", "channel": "wire", "day": day, "counterparty_country": country} | over))


def test_wires_to_listed_countries_raise_one_alert_when_the_months_total_crosses_the_threshold(world, jobs):  # noqa: F811
    jobs(job="high_risk_geo")
    send(world, topics.TRANSACTIONS, [
        abroad(1, 4000), abroad(2, 3000, "KP", day=1), abroad(3, 9000, "DE", day=1), abroad(4, 2500, "SY", day=2, channel="ach"),
        abroad(5, 3500, "SY", day=3),                                                      # 10,500 with T1 and T2: over
        abroad(6, 8000, day=4),                                                            # already raised this month
        abroad(7, 9000, party="P2"), abroad(8, 9000, "IR", party="P3", direction="in")])
    found, raw = alerts(world, T, 1, seconds=60, day=6)
    month = NOW.strftime("%Y-%m")
    assert found == [{"source": "flink-high-risk-geo", "external_id": f"P1:{month}", "scenario": "high_risk_geo", "party_id": "P1", "score": 0.55,
                      "explanation": f"3 outbound wires totalling $10500 to high-risk jurisdictions in {month}.", "txn_ids": ["T1", "T2", "T5"]}]
    assert raw[0].key == "P1"
    more, _ = alerts(world, T, 2, seconds=25, day=7)
    assert len(more) <= 1  # the wire after the crossing raised nothing further


def test_each_calendar_month_is_totalled_on_its_own_and_on_the_clients_threshold(world, jobs):  # noqa: F811
    jobs(job="high_risk_geo", params={"min_total": 5000})
    first = (NOW.replace(day=1) + timedelta(days=32)).replace(day=1)  # the first of next month
    late, early = (first - NOW).days - 1, (first - NOW).days + 1
    send(world, topics.TRANSACTIONS, [abroad(1, 3000, day=late - 1), abroad(2, 3000, day=early), abroad(3, 2500, day=early + 1)])
    found, _ = alerts(world, T, 1, seconds=60, day=early + 5)
    assert [(a["external_id"], a["txn_ids"]) for a in found] == [(f"P1:{first.strftime('%Y-%m')}", ["T2", "T3"])]
