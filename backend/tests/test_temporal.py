"""Case workflows on a real Temporal server.

Runs when TEST_TEMPORAL_URL names a disposable server, for example TEST_TEMPORAL_URL=temporal://127.0.0.1:7233
(`temporal server start-dev`, or the temporalio/temporal image with `server start-dev --ip 0.0.0.0`). Every test
uses its own id prefix, task queue and namespaces, so runs never see each other's workflows.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from conftest import Db
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from support import PERSONAS as PERSONAS_
from support import T
from test_durable import breaches, case_due_in

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

URL = os.getenv("TEST_TEMPORAL_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("temporal://"), reason="set TEST_TEMPORAL_URL to a disposable Temporal server to run")
OTHER = "kestrel"
KEYS = ("temporal_url", "workflow_engine", "temporal_id_prefix", "temporal_task_queue", "temporal_sync_seconds", "temporal_namespace_mode",
        "temporal_namespace_prefix", "temporal_step_seconds", "temporal_encryption", "temporal_encryption_key", "temporal_key_version")
MASTER = "Y2FzZWZvcmdlLXRlbXBvcmFsLXRlc3QtbWFzdGVyLWtleS0zMmJ5dGVz"  # every test here runs with payloads encrypted


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
    for k, v in {"temporal_url": URL, "workflow_engine": "temporal", "temporal_id_prefix": f"cft{run}", "temporal_task_queue": f"q-{run}",
                 "temporal_sync_seconds": 0.5, "temporal_namespace_mode": request.param, "temporal_namespace_prefix": f"cft{run}-",
                 "temporal_step_seconds": 30.0, "temporal_encryption": "required", "temporal_encryption_key": MASTER,
                 "temporal_key_version": 1}.items():
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


async def running(client, tenant, n):
    """Wait until Temporal's list of running case workflows shows `n` (its visibility store catches up a moment after a start)."""
    async def check() -> bool:
        return await temporal.running(client, tenant) == n
    return await until(check, seconds=20)


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


# ---- payload encryption

def _payloads(node):
    """Every payload in a history, wherever the event keeps it (inputs, results, update arguments, failure details)."""
    if isinstance(node, dict):
        if "metadata" in node and "encoding" in node.get("metadata", {}):
            yield node
            return
        for v in node.values():
            yield from _payloads(v)
    elif isinstance(node, list):
        for v in node:
            yield from _payloads(v)


def test_what_temporal_stores_is_sealed_and_only_the_clients_own_key_opens_it(world):
    """Temporal's history holds ciphertext; the application reads it through the codec; another scope's codec cannot."""
    from temporalio.client import Client
    from temporalio.converter import DataConverter

    from app.durable import encryption

    cid = new_case(world, "A", 600)

    async def go():
        base, (client,) = await clients(T)
        async with working(base, T):
            await asyncio.to_thread(step, world, cid, Action("transition", "ana", to="investigation", tick_tasks=True, note="spoke to the customer"))
            with pytest.raises(TransitionError, match="No transition from investigation to nowhere"):  # a refusal's words survive the sealing
                await asyncio.to_thread(step, world, cid, Action("transition", "ana", to="nowhere"))
            assert (await handle(client, T, cid).query("state"))["view"]["state"] == "investigation"  # read through the codec
            raw = Client(base.service_client, namespace=client.namespace, data_converter=DataConverter.default)
            history = await raw.get_workflow_handle(temporal.case_id(T, cid)).fetch_history()
            every = list(_payloads(json.loads(history.to_json())))
            assert len(every) > 5  # the start, the step, its activity's input and result, the refusal's failure details
            marker = base64.b64encode(encryption.ENCODING).decode()
            assert all(p["metadata"]["encoding"] == marker for p in every), [base64.b64decode(p["metadata"]["encoding"]) for p in every]
            assert "No transition" not in history.to_json()  # failure messages are sealed too, not left as plain text
            assert not any(b"spoke to the customer" in base64.b64decode(p.get("data", "")) for p in every)
            sealed = [p for e in history.events if e.HasField("workflow_execution_started_event_attributes")
                      for p in e.workflow_execution_started_event_attributes.input.payloads]
            assert sealed and all(p.metadata["encoding"] == encryption.ENCODING for p in sealed)
            kid = sealed[0].metadata[encryption.KEY_ID].decode()
            assert kid == encryption.key_id(client.namespace, 1)
            if world.mode == "per_client":  # a key per client: the other client's codec refuses before decrypting
                other = encryption.converter_for(namespaces.namespace_for(OTHER)).payload_codec
                with pytest.raises(encryption.EncryptionError, match="sealed for"):
                    await other.decode(sealed)
            object.__setattr__(settings, "temporal_key_version", 2)  # rotate: new payloads use v2, v1 history stays readable
            try:
                rotated = await temporal.client_for(base, T)
                assert (await rotated.get_workflow_handle(temporal.case_id(T, cid)).query("state"))["view"]["state"] == "investigation"
            finally:
                object.__setattr__(settings, "temporal_key_version", 1)
            await handle(client, T, cid).terminate("test over")
    asyncio.run(go())


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
            assert breach_count(world, cid) == 0 and await running(client, T, 1)
            await until(lambda: breach_count(world, cid))
            state = await handle(client, T, cid).query("state")
            assert state["view"]["breached"] is True and state["log"][-1] == state["log"][-1] | {"what": "deadline", "outcome": "breached"}
            assert await running(client, T, 1)  # the case is still open: its workflow waits for the next step
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
            assert await running(mine, T, 1) and await running(other, OTHER, 1)
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
    from test_filing import _filed, _profile

    from app.filing import followups
    from app.filing import service as filing

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


# ---- durable jobs

CALLS: dict[str, int] = {}


def _job_target(s, persona, key: str, fail_times: int = 0, refuse: bool = False, pause: float = 0.0) -> dict:
    """A registered job for these tests: counts its runs, can stumble a few times, refuse, or take a while."""
    import time

    from app.detection.lookbacks import LookbackError

    CALLS[key] = CALLS.get(key, 0) + 1
    if refuse:
        raise LookbackError("Lookback not found")
    if CALLS[key] <= fail_times:
        raise RuntimeError("the model gateway timed out")
    time.sleep(pause)
    return {"key": key, "runs": CALLS[key]}


@pytest.fixture
def test_job(monkeypatch):
    from app.workflow import jobs

    job = jobs.Job("test.job", "test_temporal:_job_target", ("app.detection.lookbacks:LookbackError",), key=lambda p: p["key"], attempts=4,
                   timeout_seconds=20)
    monkeypatch.setitem(jobs.JOBS, "test.job", job)
    CALLS.clear()
    return job


def job(world, params, tenant=T, persona="tara"):
    with world.tenant(tenant) as s:
        return world.engine_.run_job(s, "test.job", params, get_persona(tenant, persona))


def test_a_job_runs_as_a_workflow_and_is_retried_until_it_succeeds(world, test_job):
    async def go():
        base, _ = await clients(T)
        async with working(base, T):
            return await asyncio.to_thread(job, world, {"key": "k1", "fail_times": 2})
    assert asyncio.run(go()) == {"key": "k1", "runs": 3} and CALLS["k1"] == 3


def test_a_refused_job_is_not_retried_and_raises_what_the_rules_raised(world, test_job):
    from app.detection.lookbacks import LookbackError

    async def go():
        base, _ = await clients(T)
        async with working(base, T):
            with pytest.raises(LookbackError, match="Lookback not found"):
                await asyncio.to_thread(job, world, {"key": "k2", "refuse": True})
    asyncio.run(go())
    assert CALLS["k2"] == 1


def test_two_requests_for_the_same_run_share_it(world, test_job):
    async def go():
        base, _ = await clients(T)
        async with working(base, T):
            return await asyncio.gather(*(asyncio.to_thread(job, world, {"key": "k3", "pause": 2.0}) for _ in range(3)))
    assert asyncio.run(go()) == [{"key": "k3", "runs": 1}] * 3 and CALLS["k3"] == 1


def test_a_new_cases_briefing_runs_after_commit_on_a_worker(world):
    cid = new_case(world, "A", 600)

    def runs() -> int:
        with world.tenant(T) as s:
            return len(list(s.scalars(select(m.AgentRun).where(m.AgentRun.case_id == cid))))

    before = runs()

    async def go():
        base, _ = await clients(T)
        async with working(base, T):
            with world.tenant(T) as s:
                world.engine_.brief(s, s.get(m.Case, cid))
                assert runs() == before  # nothing yet: the briefing starts once this transaction commits
                s.commit()
            await until(lambda: runs() > before)
    asyncio.run(go())
    assert runs() == before + 1


# ---- schedules

def test_each_clients_sweep_pipelines_and_regulator_answers_run_from_schedules(world):
    cid = new_case(world, "A", -5)
    every = timedelta(seconds=2)

    async def go():
        base, (client,) = await clients(T)
        made = await temporal.ensure_schedules(client, T, sweep_every=every, pipelines_every=every, answers_every=every)
        assert made == {"sweep": True, "pipelines": True, "answers": True}
        assert await temporal.ensure_schedules(client, T) == {"sweep": False, "pipelines": False, "answers": False}
        try:
            async with working(base, T):
                await until(lambda: breach_count(world, cid))

                async def ran(sid) -> bool:
                    return (await client.get_schedule_handle(sid).describe()).info.num_actions >= 1
                await until(lambda: ran(temporal.pipelines_id(T)))
                await until(lambda: ran(temporal.answers_id(T)))
                last = (await client.get_schedule_handle(temporal.answers_id(T)).describe()).info.recent_actions[-1].action
                answered = await client.get_workflow_handle(last.workflow_id, run_id=last.first_execution_run_id).result()
                assert "files" in answered or "skipped" in answered  # the inbox was read, or the reason it could not be
        finally:
            for sid in (temporal.sweep_id(T), temporal.pipelines_id(T), temporal.answers_id(T)):
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
            assert {temporal.sweep_id(T), temporal.pipelines_id(T), temporal.answers_id(T)} <= set(sched)
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=30)
            for t in (T, OTHER):
                c = await temporal.client_for(base, t)
                for sid in (temporal.sweep_id(t), temporal.pipelines_id(t), temporal.answers_id(t)):
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

