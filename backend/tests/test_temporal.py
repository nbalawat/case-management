"""Stage clocks on a real Temporal server.

Runs when TEST_TEMPORAL_URL names a disposable server, for example TEST_TEMPORAL_URL=temporal://127.0.0.1:7233
(`temporal server start-dev`, or the temporalio/temporal image with `server start-dev --ip 0.0.0.0`). Every test
uses its own id prefix and task queue, so runs never see each other's workflows.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

import app.db as appdb
from app.config import settings
from app.db import Base, make_engine
from app.domain import models as m
from app.durable import clocks, temporal
from app.tenants import registry
from conftest import Db
from support import T
from test_durable import breaches, case_due_in

URL = os.getenv("TEST_TEMPORAL_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("temporal://"), reason="set TEST_TEMPORAL_URL to a disposable Temporal server to run")
OTHER = "kestrel"


@pytest.fixture
def world(tmp_path):
    """Two clients in a database on disk (activities run on threads), with the application's sessions pointed at it."""
    saved = registry.swap({})
    db = Db.__new__(Db)
    db.engine = make_engine(f"sqlite:///{tmp_path}/durable.db")
    Base.metadata.create_all(db.engine)
    db.factory = sessionmaker(bind=db.engine, expire_on_commit=False)
    old = appdb.engine, appdb.SessionLocal, {k: getattr(settings, k) for k in ("temporal_url", "event_topic_prefix", "temporal_task_queue", "temporal_sync_seconds")}
    appdb.engine, appdb.SessionLocal = db.engine, db.factory
    run = uuid.uuid4().hex[:10]
    for k, v in {"temporal_url": URL, "event_topic_prefix": f"cft{run}", "temporal_task_queue": f"q-{run}", "temporal_sync_seconds": 0.5}.items():
        object.__setattr__(settings, k, v)
    db.onboard(T), db.onboard(OTHER)
    yield db
    for k, v in old[2].items():
        object.__setattr__(settings, k, v)
    appdb.engine, appdb.SessionLocal = old[0], old[1]
    db.engine.dispose()
    registry.swap(saved)


@asynccontextmanager
async def working(client):
    """A worker for this test's queue, for as long as the block runs."""
    from temporalio.worker import Worker

    from app.durable.workflows import StageClock, Sweep

    with ThreadPoolExecutor(max_workers=4) as pool:
        async with Worker(client, task_queue=settings.temporal_task_queue, workflows=[StageClock, Sweep], activities=temporal.activities(), activity_executor=pool):
            yield


def new_case(world, pid, seconds, tenant=T):
    with world.tenant(tenant) as s:
        cid = case_due_in(s, pid, seconds).id
        s.commit()
    return cid


def breach_count(world, cid, tenant=T):
    with world.tenant(tenant) as s:
        return len(breaches(s, cid))


async def result(client, tenant, cid, seconds=40):
    return await asyncio.wait_for(client.get_workflow_handle(temporal.clock_id(tenant, cid)).result(), timeout=seconds)


def test_a_deadline_is_acted_on_when_it_passes_with_nobody_looking(world):
    cid = new_case(world, "A", 3)

    async def go():
        client = await temporal.connect()
        async with working(client):
            assert await temporal.sync(client) == {T: 1, OTHER: 0}
            assert breach_count(world, cid) == 0 and await temporal.running(client, T) == 1
            state = await client.get_workflow_handle(temporal.clock_id(T, cid)).query("state")
            assert state["fired"] == [] and state["due"]
            assert await result(client, T, cid) == "breached"
            assert await temporal.running(client, T) == 0
    asyncio.run(go())
    assert breach_count(world, cid) == 1
    with world.tenant(T) as s:
        assert clocks.due_clocks(s) == []  # so the next sync starts nothing for this stage


def test_bringing_clocks_into_step_twice_starts_one_clock_per_case(world):
    a, b = new_case(world, "A", 600), new_case(world, "B", 600)

    async def go():
        client = await temporal.connect()
        for _ in range(3):
            assert await temporal.sync(client) == {T: 2, OTHER: 0}
        assert await temporal.running(client, T) == 2 and await temporal.running(client, OTHER) == 0
        for cid in (a, b):
            await client.get_workflow_handle(temporal.clock_id(T, cid)).terminate("test over")
    asyncio.run(go())


def test_a_case_that_moves_stage_is_timed_on_its_new_deadline(world):
    cid = new_case(world, "A", 4)

    async def go():
        client = await temporal.connect()
        async with working(client):
            await temporal.sync(client)
            with world.tenant(T) as s:  # the case enters a new stage, whose deadline is eight seconds after the old one
                c = s.get(m.Case, cid)
                c.state_entered = c.state_entered + timedelta(seconds=8)
                s.commit()
            await temporal.sync(client)
            await asyncio.sleep(6)  # the first deadline has passed
            assert breach_count(world, cid) == 0
            assert await result(client, T, cid) == "breached"
            state_entered = None
            with world.tenant(T) as s:
                state_entered = s.get(m.Case, cid).state_entered.isoformat()
                assert s.get(m.Case, cid).data["stage_breach"] == state_entered
    asyncio.run(go())
    assert breach_count(world, cid) == 1


def test_a_case_closed_before_its_deadline_is_left_alone(world):
    cid = new_case(world, "A", 3)

    async def go():
        client = await temporal.connect()
        async with working(client):
            await temporal.sync(client)
            with world.tenant(T) as s:
                s.get(m.Case, cid).closed = m.now()
                s.commit()
            assert await result(client, T, cid) == "closed"
    asyncio.run(go())
    assert breach_count(world, cid) == 0


def test_when_the_sweep_gets_there_first_the_clock_adds_nothing(world):
    cid = new_case(world, "A", 3)

    async def go():
        client = await temporal.connect()
        await temporal.sync(client)  # no worker yet: the timer is Temporal's, not ours
        await asyncio.sleep(4)
        assert clocks.sweep_all()[T]["stage_breaches"] == 1
        async with working(client):
            assert await result(client, T, cid) == "already"
    asyncio.run(go())
    assert breach_count(world, cid) == 1


def test_a_deadline_that_passes_while_no_worker_is_running_is_acted_on_when_one_starts(world):
    cid = new_case(world, "A", 3)

    async def go():
        client = await temporal.connect()
        async with working(client):
            await temporal.sync(client)
        await asyncio.sleep(5)  # every worker is gone, and the deadline passes
        assert breach_count(world, cid) == 0
        async with working(client):
            assert await result(client, T, cid) == "breached"
    asyncio.run(go())
    assert breach_count(world, cid) == 1


def test_two_clients_clocks_are_separate(world):
    ours, theirs = new_case(world, "A", 3), new_case(world, "A", 600, tenant=OTHER)

    async def go():
        client = await temporal.connect()
        async with working(client):
            assert await temporal.sync(client) == {T: 1, OTHER: 1}
            assert await result(client, T, ours) == "breached"
            assert await temporal.running(client, T) == 0 and await temporal.running(client, OTHER) == 1
            await client.get_workflow_handle(temporal.clock_id(OTHER, theirs)).terminate("test over")
    asyncio.run(go())
    assert breach_count(world, ours) == 1 and breach_count(world, theirs, OTHER) == 0
    assert temporal.status(T) | {"note": ""} == {"engine": "temporal", "connected": True, "clocks": 0, "note": ""}


def test_the_periodic_sweep_runs_from_a_schedule(world):
    cid = new_case(world, "A", -5)

    async def go():
        client = await temporal.connect()
        assert await temporal.ensure_sweep_schedule(client, every=timedelta(seconds=2)) is True
        assert await temporal.ensure_sweep_schedule(client, every=timedelta(seconds=2)) is False
        try:
            async with working(client):
                for _ in range(40):
                    if breach_count(world, cid):
                        break
                    await asyncio.sleep(0.5)
        finally:
            await client.get_schedule_handle(temporal.sweep_id()).delete()
    asyncio.run(go())
    assert breach_count(world, cid) == 1


def test_the_worker_keeps_clocks_in_step_on_its_own(world):
    from app.durable import worker

    async def go():
        client = await temporal.connect()
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop, client=client))
        await asyncio.sleep(1)
        cid = await asyncio.to_thread(new_case, world, "A", 3)  # a case opened after the worker started
        try:
            for _ in range(40):  # the worker notices the case at its next look, half a second or so later
                if await temporal.running(client, T):
                    break
                await asyncio.sleep(0.25)
            assert await temporal.running(client, T) == 1
            assert await result(client, T, cid) == "breached"
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=30)
            await client.get_schedule_handle(temporal.sweep_id()).delete()
        return cid
    cid = asyncio.run(go())
    assert breach_count(world, cid) == 1
