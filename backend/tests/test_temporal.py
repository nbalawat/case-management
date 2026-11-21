"""Case workflows on a real Temporal server.

Runs when TEST_TEMPORAL_URL names a disposable server, for example TEST_TEMPORAL_URL=temporal://127.0.0.1:7233
(`temporal server start-dev`, or the temporalio/temporal image with `server start-dev --ip 0.0.0.0`). Every test
uses its own id prefix, task queue and namespaces, so runs never see each other's workflows.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import app.db as appdb
from app.cases.service import TransitionError
from app.config import settings
from app.db import Base, make_engine
from app.domain import models as m
from app.domain.personas import get_persona
from app.durable import namespaces, steps, temporal
from app.durable.engine import TemporalEngine
from app.tenants import registry
from app.workflow import Action, use
from conftest import Db
from support import PERSONAS as PERSONAS_
from support import T
from test_durable import breaches, case_due_in

URL = os.getenv("TEST_TEMPORAL_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("temporal://"), reason="set TEST_TEMPORAL_URL to a disposable Temporal server to run")
OTHER = "kestrel"
KEYS = ("temporal_url", "workflow_engine", "event_topic_prefix", "temporal_task_queue", "temporal_sync_seconds", "temporal_namespace_mode",
        "temporal_namespace_prefix", "temporal_step_seconds")


@pytest.fixture(params=["shared", "per_client"])
def world(request, tmp_path):
    """Two clients in a database on disk (activities run on threads), the application's sessions pointed at it, and the Temporal engine."""
    saved = registry.swap({})
    db = Db.__new__(Db)
    db.engine = make_engine(f"sqlite:///{tmp_path}/durable.db")
    Base.metadata.create_all(db.engine)
    db.factory = sessionmaker(bind=db.engine, expire_on_commit=False)
    old_db = appdb.engine, appdb.SessionLocal
    old = {k: getattr(settings, k) for k in KEYS}
    appdb.engine, appdb.SessionLocal = db.engine, db.factory
    run = uuid.uuid4().hex[:10]
    for k, v in {"temporal_url": URL, "workflow_engine": "temporal", "event_topic_prefix": f"cft{run}", "temporal_task_queue": f"q-{run}",
                 "temporal_sync_seconds": 0.5, "temporal_namespace_mode": request.param, "temporal_namespace_prefix": f"cft{run}-",
                 "temporal_step_seconds": 30.0}.items():
        object.__setattr__(settings, k, v)
    db.onboard(T), db.onboard(OTHER)
    db.mode = request.param
    db.engine_ = TemporalEngine()
    previous = use(db.engine_)
    yield db
    use(previous)
    for k, v in old.items():
        object.__setattr__(settings, k, v)
    appdb.engine, appdb.SessionLocal = old_db
    db.engine.dispose()
    registry.swap(saved)


async def clients(*tenants):
    base = await temporal.connect()
    return base, [await temporal.client_for(base, t) for t in tenants]


@asynccontextmanager
async def working(base, *tenants):
    """Workers for these clients' namespaces, for as long as the block runs."""
    from temporalio.worker import Worker

    from app.durable.workflows import WORKFLOWS

    nss = sorted({namespaces.namespace_for(t) for t in tenants or (T, OTHER)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        workers = [Worker(temporal.for_namespace(base, ns), task_queue=settings.temporal_task_queue, workflows=WORKFLOWS, activities=temporal.activities(),
                          activity_executor=pool) for ns in nss]
        tasks = [asyncio.create_task(w.run()) for w in workers]
        try:
            yield
        finally:
            for w in workers:
                await w.shutdown()
            await asyncio.gather(*tasks, return_exceptions=True)


def new_case(world, pid, seconds, tenant=T):
    with world.tenant(tenant) as s:
        cid = case_due_in(s, pid, seconds).id
        s.commit()
    return cid


def breach_count(world, cid, tenant=T):
    with world.tenant(tenant) as s:
        return len(breaches(s, cid))


def events(world, cid, kind, tenant=T):
    with world.tenant(tenant) as s:
        return [e for e in s.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == cid).order_by(m.CaseEvent.id)) if e.kind == kind]


def handle(client, tenant, cid):
    return client.get_workflow_handle(temporal.case_id(tenant, cid))


async def until(check, seconds=40.0):
    """Wait for `check()` to be true. It may be a plain function (run on a thread), a coroutine function, or return an awaitable."""
    for _ in range(int(seconds / 0.25)):
        got = await check() if asyncio.iscoroutinefunction(check) else await asyncio.to_thread(check)
        if asyncio.iscoroutine(got):
            try:
                got = await got
            except Exception:  # noqa: BLE001  not there yet
                got = None
        if got:
            return got
        await asyncio.sleep(0.25)
    raise AssertionError("gave up waiting")


def step(world, cid, action, tenant=T, persona="ana"):
    """A person's step through the engine, as the API takes it: in a request's own session."""
    with world.tenant(tenant) as s:
        c = s.get(m.Case, cid)
        view = world.engine_.perform(s, c, get_persona(tenant, persona), action)
        s.commit()
        return view


def escalate(cid):
    return Action("transition", "ana", to="investigation", tick_tasks=True)


# ---- people's steps

def test_a_persons_step_is_applied_by_the_cases_workflow_and_kept_in_its_history(world):
    cid = new_case(world, "A", 600)

    async def go():
        base, (client,) = await clients(T)
        async with working(base, T):
            view = await asyncio.to_thread(step, world, cid, escalate(cid))
            assert view.state == "investigation" and not view.closed
            state = await handle(client, T, cid).query("state")
            assert state["steps"] == 1 and state["view"]["state"] == "investigation" and state["log"][-1]["outcome"] == "done"
            history = await handle(client, T, cid).fetch_history()
            assert any(e.HasField("workflow_execution_update_accepted_event_attributes") for e in history.events)
            await handle(client, T, cid).terminate("test over")
    asyncio.run(go())
    assert [e.payload["to"] for e in events(world, cid, "case.state_changed")] == ["investigation"]


def test_a_refused_step_changes_nothing_and_says_why_in_the_case_types_words(world):
    cid = new_case(world, "A", 600)

    async def go():
        base, (client,) = await clients(T)
        async with working(base, T):
            with pytest.raises(TransitionError, match="Finish required tasks"):
                await asyncio.to_thread(step, world, cid, Action("transition", "ana", to="investigation"))
            with pytest.raises(TransitionError, match="No transition from triage to sar_drafting"):
                await asyncio.to_thread(step, world, cid, Action("transition", "ana", to="sar_drafting"))
            state = await handle(client, T, cid).query("state")
            assert state["steps"] == 0 and [x["outcome"] for x in state["log"]] == ["refused", "refused"]
            await handle(client, T, cid).terminate("test over")
    asyncio.run(go())
    assert events(world, cid, "case.state_changed") == [] and events(world, cid, "task.completed") == []


def test_two_people_taking_the_same_step_at_once_cannot_both_succeed(world):
    cid = new_case(world, "A", 600)
    outcomes: list[str] = []

    def take():
        try:
            step(world, cid, escalate(cid))
            outcomes.append("done")
        except TransitionError as e:
            outcomes.append(str(e))

    async def go():
        base, (client,) = await clients(T)
        async with working(base, T):
            await asyncio.gather(*(asyncio.to_thread(take) for _ in range(4)))
            await handle(client, T, cid).terminate("test over")
    asyncio.run(go())
    assert outcomes.count("done") == 1 and len(outcomes) == 4
    assert len(events(world, cid, "case.state_changed")) == 1


def test_a_step_retried_after_it_was_committed_is_not_taken_twice(world):
    cid = new_case(world, "A", 600)
    act = Action("transition", "ana", to="investigation", tick_tasks=True, step_id="step-1").as_dict()
    first = steps.perform(T, cid, act)
    again = steps.perform(T, cid, act)  # what Temporal does when the worker died after committing
    assert first == again and first["state"] == "investigation"
    assert len(events(world, cid, "case.state_changed")) == 1


def test_one_client_cannot_take_a_step_on_anothers_case(world):
    theirs = new_case(world, "A", 600, tenant=OTHER)
    with pytest.raises(TransitionError, match="Case not found"):
        steps.perform(T, theirs, escalate(theirs).as_dict())
    assert events(world, theirs, "case.state_changed", tenant=OTHER) == []


# ---- deadlines

def test_a_deadline_is_acted_on_when_it_passes_with_nobody_looking(world):
    cid = new_case(world, "A", 3)

    async def go():
        base, (client,) = await clients(T)
        async with working(base, T, OTHER):
            assert await temporal.sync(base) == {T: 1, OTHER: 0}
            assert breach_count(world, cid) == 0 and await temporal.running(client, T) == 1
            await until(lambda: breach_count(world, cid))
            state = await handle(client, T, cid).query("state")
            assert state["view"]["breached"] is True and state["log"][-1] == state["log"][-1] | {"what": "deadline", "outcome": "breached"}
            assert await temporal.running(client, T) == 1  # the case is still open: its workflow waits for the next step
            await handle(client, T, cid).terminate("test over")
    asyncio.run(go())
    assert breach_count(world, cid) == 1


def test_a_case_moved_outside_its_workflow_is_timed_on_its_new_deadline(world):
    cid = new_case(world, "A", 4)

    async def go():
        base, (client,) = await clients(T)
        async with working(base, T):
            await temporal.sync(base, {T: [cid]})
            await until(lambda: handle(client, T, cid).query("state"))  # the workflow has read the case
            with world.tenant(T) as s:  # the case enters a new visit to its stage, due eight seconds after the old deadline
                c = s.get(m.Case, cid)
                c.state_entered = c.state_entered + timedelta(seconds=8)
                s.commit()  # the commit hook tells the case's workflow to look again
            await asyncio.sleep(6)  # the first deadline has passed
            assert breach_count(world, cid) == 0
            await until(lambda: breach_count(world, cid))
            await handle(client, T, cid).terminate("test over")
    asyncio.run(go())
    assert breach_count(world, cid) == 1


def test_a_case_that_closes_ends_its_workflow(world):
    cid = new_case(world, "A", 3)

    async def go():
        base, (client,) = await clients(T)
        async with working(base, T):
            await temporal.sync(base, {T: [cid]})
            with world.tenant(T) as s:
                s.get(m.Case, cid).closed = m.now()
                s.commit()
            out = await asyncio.wait_for(handle(client, T, cid).result(), timeout=30)
            assert out["case_id"] == cid and out["steps"] == 0
    asyncio.run(go())
    assert breach_count(world, cid) == 0


def test_a_deadline_that_passes_while_no_worker_is_running_is_acted_on_when_one_starts(world):
    cid = new_case(world, "A", 3)

    async def go():
        base, _ = await clients(T)
        async with working(base, T):
            await temporal.sync(base, {T: [cid]})
            await asyncio.sleep(1)
        await asyncio.sleep(4)  # every worker is gone, and the deadline passes
        assert breach_count(world, cid) == 0
        async with working(base, T):
            await until(lambda: breach_count(world, cid))
    asyncio.run(go())
    assert breach_count(world, cid) == 1


# ---- clients kept apart

def test_two_clients_case_workflows_are_separate(world):
    ours, theirs = new_case(world, "A", 3), new_case(world, "A", 600, tenant=OTHER)

    async def go():
        base, (mine, other) = await clients(T, OTHER)
        async with working(base, T, OTHER):
            assert await temporal.sync(base) == {T: 1, OTHER: 1}
            await until(lambda: breach_count(world, ours))
            assert await temporal.running(mine, T) == 1 and await temporal.running(other, OTHER) == 1
            if world.mode == "per_client":  # a namespace of its own: the other client's workflows are not even listed there
                assert mine.namespace != other.namespace
                assert await temporal.running(mine, OTHER) == 0 and await temporal.running(other, T) == 0
            for c, t, cid in ((mine, T, ours), (other, OTHER, theirs)):
                await handle(c, t, cid).terminate("test over")
    asyncio.run(go())
    assert breach_count(world, ours) == 1 and breach_count(world, theirs, OTHER) == 0
    got = world.engine_.status(T)
    assert got["engine"] == "temporal" and got["connected"] is True and got["namespace"] == namespaces.namespace_for(T)


# ---- reports to a regulator

def test_a_sent_report_is_chased_when_the_regulator_is_late_and_then_waits_for_its_review(world):
    from app.filing import followups
    from app.filing import service as filing
    from test_filing import _filed, _profile

    _profile()
    with world.tenant(T) as s:
        _, f = _filed(s)
        f.submitted_at = m.now() - timedelta(days=followups.ANSWER_DAYS) + timedelta(seconds=3)  # the answer falls due in three seconds
        s.commit()  # the commit hook starts the report's workflow
        fid = f.id

    def flagged() -> list[str]:
        with world.tenant(T) as s:
            return [x["what"] for x in s.get(m.Filing, fid).history if x["what"] in (followups.OVERDUE, followups.CONTINUING)]

    async def go():
        base, (client,) = await clients(T)
        h = client.get_workflow_handle(temporal.filing_id(T, fid))
        async with working(base, T):
            assert await until(flagged) == [followups.OVERDUE]
            with world.tenant(T) as s:
                filing.acknowledge(s, fid, "BSA-42", PERSONAS_["maya"])
                s.commit()

            async def waiting_for_review() -> bool:
                return (await h.query("state"))["view"].get("next") == followups.CONTINUING
            await until(waiting_for_review)
            assert await temporal.sync_filings(base, {T: [fid]}) == {T: 1}  # adopting it again changes nothing
            await h.terminate("test over")
    asyncio.run(go())
    assert flagged() == [followups.OVERDUE]


# ---- schedules

def test_each_clients_sweep_and_pipelines_run_from_schedules(world):
    cid = new_case(world, "A", -5)

    async def go():
        base, (client,) = await clients(T)
        assert await temporal.ensure_schedules(client, T, sweep_every=timedelta(seconds=2), pipelines_every=timedelta(seconds=2)) == {"sweep": True, "pipelines": True}
        assert await temporal.ensure_schedules(client, T) == {"sweep": False, "pipelines": False}
        try:
            async with working(base, T):
                await until(lambda: breach_count(world, cid))

                async def pipelines_started() -> bool:
                    return (await client.get_schedule_handle(temporal.pipelines_id(T)).describe()).info.num_actions >= 1
                await until(pipelines_started)
        finally:
            for sid in (temporal.sweep_id(T), temporal.pipelines_id(T)):
                await client.get_schedule_handle(sid).delete()
    asyncio.run(go())
    assert breach_count(world, cid) == 1


# ---- the worker on its own

def test_the_worker_adopts_cases_opened_anywhere_and_runs_steps_on_them(world):
    from app.durable import worker

    async def go():
        base, (client,) = await clients(T)
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop, client=base))
        cid = await asyncio.to_thread(new_case, world, "A", 3)  # a case opened while the worker runs
        try:
            await until(lambda: breach_count(world, cid))
            view = await asyncio.to_thread(step, world, cid, escalate(cid))
            assert view.state == "investigation"
            sched = [s.id async for s in await client.list_schedules()]
            assert temporal.sweep_id(T) in sched and temporal.pipelines_id(T) in sched
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=30)
            for t in (T, OTHER):
                c = await temporal.client_for(base, t)
                for sid in (temporal.sweep_id(t), temporal.pipelines_id(t)):
                    try:
                        await c.get_schedule_handle(sid).delete()
                    except Exception:  # noqa: BLE001
                        pass
        return cid
    cid = asyncio.run(go())
    assert breach_count(world, cid) == 1 and len(events(world, cid, "case.state_changed")) == 1


def test_a_step_when_no_worker_is_running_gives_up_and_changes_nothing(world):
    from app.workflow import EngineUnavailable

    object.__setattr__(settings, "temporal_step_seconds", 3.0)
    cid = new_case(world, "A", 600)
    with pytest.raises(EngineUnavailable):
        step(world, cid, escalate(cid))
    assert events(world, cid, "case.state_changed") == []

    async def go():  # the update stays queued in the workflow; a worker would apply it, so the test removes it
        base, (client,) = await clients(T)
        await handle(client, T, cid).terminate("test over")
    asyncio.run(go())

