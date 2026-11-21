"""Change data capture: Debezium publishes the outbox exactly as the built-in relay would.

Needs a running stack, so it is skipped unless both are set:
  TEST_CDC_DATABASE_URL  the Postgres the application has seeded (wal_level=logical), for example
                         postgresql+psycopg://caseforge@127.0.0.1:5432/caseforge
  TEST_KAFKA_URL         the broker a Kafka Connect worker writes to, with deploy/debezium/outbox-connector.json registered (see deploy/debezium/README.md)
The test only adds one audit event to the database; it removes nothing.
"""
from __future__ import annotations

import os
import time
import uuid
from collections import defaultdict

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.backbone import relay
from app.backbone.bus import connect
from app.db import make_engine
from app.domain import models as m
from app.tenancy import bind_tenant

DB, KAFKA = os.getenv("TEST_CDC_DATABASE_URL", ""), os.getenv("TEST_KAFKA_URL", "")
pytestmark = pytest.mark.skipif(not (DB.startswith("postgresql") and KAFKA.startswith("kafka://")),
                                reason="set TEST_CDC_DATABASE_URL and TEST_KAFKA_URL to a running Debezium stack to run")


def everything(sub, want: set[str], seconds=90):
    got, end = {}, time.monotonic() + seconds
    while want - set(got) and time.monotonic() < end:
        for x in sub.poll(timeout=1.0):
            got.setdefault(x.headers.get("id"), []).append(x)
    return got


def test_debezium_publishes_every_outbox_row_as_the_relay_would():
    factory = sessionmaker(bind=make_engine(DB), expire_on_commit=False)
    with factory() as s:
        s.info["platform"] = True
        tenant = s.scalars(select(m.Tenant.id).order_by(m.Tenant.id)).first()
    marker = f"cdc-check-{uuid.uuid4().hex[:8]}"
    with factory() as s:  # a change made while the connector is streaming, not part of its first snapshot
        bind_tenant(s, tenant)
        s.add(m.CaseEvent(case_id=marker, actor="cdc-test", actor_kind="system", kind="note.added", payload={"marker": marker}))
        s.commit()
    with factory() as s:
        s.info["platform"] = True
        rows = list(s.scalars(select(m.OutboxEvent).order_by(m.OutboxEvent.id)))
    assert rows and rows[-1].key == marker

    bus = connect(KAFKA)
    got = everything(bus.subscribe(f"cdc-{uuid.uuid4().hex}", r"^caseforge\.[^.]+\.(case-events|alerts)$"), {r.event_id for r in rows})
    bus.close()
    missing = [r.event_id for r in rows if r.event_id not in got]
    assert not missing, f"{len(missing)} of {len(rows)} outbox rows never reached Kafka"

    order: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rows:
        want = relay.message(r)
        x = got[r.event_id][0]
        assert (x.topic, x.key, x.value) == (want.topic, want.key, want.value)  # the same bytes on the same topic under the same key
        assert {k: x.headers.get(k) for k in want.headers} == want.headers
        order[(r.topic, r.key)].append(r.event_id)
    arrived: dict[tuple[str, str], list[tuple[int, int, str]]] = defaultdict(list)
    for copies in got.values():
        for x in copies:
            arrived[(x.topic, x.key)].append((x.partition, x.offset, x.headers["id"]))
    for k, ids in order.items():  # everything about one case or customer is in the order it was written
        seen = [i for _, _, i in sorted(arrived[k])]
        assert list(dict.fromkeys(seen)) == ids
