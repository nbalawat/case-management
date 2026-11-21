"""The Temporal side: connections, workflow ids, adopting cases, schedules, the worker's activities, and status.

TEMPORAL_URL=temporal://host:7233. Each client's workflows live in that client's namespace (app.durable.namespaces);
workflow ids carry the client as well, so a shared namespace is never ambiguous.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from ..config import settings
from . import clocks, steps
from .namespaces import TENANT, namespace_for

log = logging.getLogger("caseforge.durable")


class DurableError(RuntimeError):
    pass


def target(url: str | None = None) -> str:
    url = settings.temporal_url if url is None else url
    if not url.startswith("temporal://") or not url.removeprefix("temporal://"):
        raise DurableError("TEMPORAL_URL must look like temporal://host:7233")
    return url.removeprefix("temporal://").rstrip("/")


def _prefix(tenant_id: str) -> str:
    if not TENANT.fullmatch(tenant_id or ""):
        raise DurableError("That is not a client id.")
    return f"{settings.temporal_id_prefix or settings.event_topic_prefix}.{tenant_id}"


def case_id(tenant_id: str, case: str) -> str:
    """A case's workflow. One per case, so starting it twice is the same as starting it once."""
    return f"{_prefix(tenant_id)}.case.{case}"


def clock_id(tenant_id: str, case: str) -> str:
    """The id stage clocks had before case workflows held them (still used to finish old ones)."""
    return f"{_prefix(tenant_id)}.clock.{case}"


def filing_id(tenant_id: str, filing: str) -> str:
    return f"{_prefix(tenant_id)}.filing.{filing}"


def sweep_id(tenant_id: str) -> str:
    return f"{_prefix(tenant_id)}.sweep"


def pipelines_id(tenant_id: str) -> str:
    return f"{_prefix(tenant_id)}.pipelines"


async def connect(url: str | None = None, namespace: str | None = None):
    """A client for one namespace (by default the shared one)."""
    from temporalio.client import Client

    try:
        return await Client.connect(target(url), namespace=namespace or settings.temporal_namespace)
    except DurableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise DurableError("Temporal did not answer.") from e


def for_namespace(client, namespace: str):
    """The same connection, speaking for another namespace."""
    from temporalio.client import Client

    if client.namespace == namespace:
        return client
    return Client(client.service_client, namespace=namespace, data_converter=client.data_converter)


async def client_for(base, tenant_id: str, ensure: bool = True):
    """A client for this client's namespace, registering the namespace first if it is new."""
    from . import namespaces

    ns = namespace_for(tenant_id)
    if ensure and namespaces.per_client():
        await namespaces.ensure(base, ns)
    return for_namespace(base, ns)


async def adopt(client, tenant_id: str, case_ids: list[str], tell: bool = True) -> int:
    """Make sure each of these cases has a running workflow.

    tell=True (signal-with-start): a case with no workflow gets one, a running one is told to read the case again. This
    is how changes made outside a workflow reach it. tell=False starts only the missing ones and leaves running
    workflows alone, which is cheap enough to do for every open case on every pass. Either can run as often as you like.
    """
    from temporalio.common import WorkflowIDConflictPolicy

    from .workflows import CaseWorkflow

    for cid in case_ids:
        if tell:
            await client.start_workflow(CaseWorkflow.run, args=[tenant_id, cid], id=case_id(tenant_id, cid), task_queue=settings.temporal_task_queue,
                                        start_signal="observed")
        else:
            await client.start_workflow(CaseWorkflow.run, args=[tenant_id, cid], id=case_id(tenant_id, cid), task_queue=settings.temporal_task_queue,
                                        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)
    return len(case_ids)


async def adopt_filings(client, tenant_id: str, filing_ids: list[str], tell: bool = True) -> int:
    """Make sure each of these sent reports has its filing workflow (and, with tell, that it reads the report again)."""
    from temporalio.common import WorkflowIDConflictPolicy

    from .workflows import FilingWorkflow

    for fid in filing_ids:
        kw = {"start_signal": "observed"} if tell else {"id_conflict_policy": WorkflowIDConflictPolicy.USE_EXISTING}
        await client.start_workflow(FilingWorkflow.run, args=[tenant_id, fid], id=filing_id(tenant_id, fid), task_queue=settings.temporal_task_queue, **kw)
    return len(filing_ids)


async def sync(base, wanted: dict[str, list[str]] | None = None, tell: bool = False) -> dict[str, int]:
    """Every open case of every live client has its case workflow. Returns how many open cases each client has."""
    if wanted is None:
        wanted = await asyncio.to_thread(open_by_client)
    done = {}
    for tenant_id, ids in wanted.items():
        done[tenant_id] = await adopt(await client_for(base, tenant_id), tenant_id, ids, tell=tell)
    return done


async def sync_filings(base, wanted: dict[str, list[str]] | None = None) -> dict[str, int]:
    """Every sent report that still owes a follow-up has its filing workflow."""
    if wanted is None:
        wanted = await asyncio.to_thread(sent_by_client)
    return {t: await adopt_filings(await client_for(base, t), t, ids, tell=False) for t, ids in wanted.items()}


def open_by_client() -> dict[str, list[str]]:
    from ..tenants import registry

    return {rt.id: steps.open_cases(rt.id) for rt in registry.all_runtimes() if rt.status == "live"}


def sent_by_client() -> dict[str, list[str]]:
    from ..tenants import registry

    return {rt.id: steps.sent_filings(rt.id) for rt in registry.all_runtimes() if rt.status == "live"}


async def _schedule(client, sid: str, workflow_run, tenant_id: str, every: timedelta) -> bool:
    from temporalio.client import (Schedule, ScheduleActionStartWorkflow, ScheduleAlreadyRunningError, ScheduleIntervalSpec,
                                   ScheduleOverlapPolicy, SchedulePolicy, ScheduleSpec)

    try:
        await client.create_schedule(sid, Schedule(
            action=ScheduleActionStartWorkflow(workflow_run, args=[tenant_id], id=sid, task_queue=settings.temporal_task_queue),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=every)]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP)))
    except ScheduleAlreadyRunningError:
        return False
    return True


async def ensure_schedules(client, tenant_id: str, sweep_every: timedelta | None = None, pipelines_every: timedelta | None = None) -> dict[str, bool]:
    """A client's periodic work, as Temporal schedules in its namespace: the sweep, and starting due data pipelines.

    Returns which were created now (False: already there). A schedule never overlaps itself.
    """
    from .workflows import PipelineRuns, Sweep

    return {"sweep": await _schedule(client, sweep_id(tenant_id), Sweep.run, tenant_id, sweep_every or timedelta(seconds=settings.temporal_sweep_seconds)),
            "pipelines": await _schedule(client, pipelines_id(tenant_id), PipelineRuns.run, tenant_id,
                                         pipelines_every or timedelta(seconds=settings.temporal_pipeline_seconds))}


def activities() -> list:
    """The activities the worker registers. Plain functions on threads: they use the application's ordinary sessions."""
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    from ..cases.service import TransitionError
    from ..ingest import scheduler
    from .workflows import FILING_DUE, FILING_LOOK, LOOK, PERFORM, PIPELINES, REFUSED, STAGE_DUE, SWEEP

    @activity.defn(name=LOOK)
    def case_look(tenant_id: str, case: str) -> dict:
        return steps.look(tenant_id, case)

    @activity.defn(name=PERFORM)
    def case_perform(tenant_id: str, case: str, action: dict) -> dict:
        try:
            return steps.perform(tenant_id, case, action)
        except TransitionError as e:
            raise ApplicationError(str(e), type=REFUSED, non_retryable=True) from None

    @activity.defn(name=STAGE_DUE)
    def stage_due(tenant_id: str, case: str, entered: str) -> str:
        return clocks.fire(tenant_id, case, entered)

    @activity.defn(name=SWEEP)
    def sweep(tenant_id: str | None = None) -> dict:
        return clocks.sweep_one(tenant_id) if tenant_id else clocks.sweep_all()

    @activity.defn(name=PIPELINES)
    def pipelines_due(tenant_id: str) -> dict:
        return {"started": scheduler.tick_one(tenant_id)}

    @activity.defn(name=FILING_LOOK)
    def filing_look(tenant_id: str, filing: str) -> dict:
        return steps.filing_look(tenant_id, filing)

    @activity.defn(name=FILING_DUE)
    def filing_follow_up(tenant_id: str, filing: str) -> list:
        return steps.filing_follow_up(tenant_id, filing)

    return [case_look, case_perform, stage_due, sweep, pipelines_due, filing_look, filing_follow_up]


async def running(client, tenant_id: str, limit: int = 5000) -> int:
    """How many of a client's case workflows are running now."""
    prefix, n = case_id(tenant_id, ""), 0
    async for wf in client.list_workflows("WorkflowType = 'CaseWorkflow' AND ExecutionStatus = 'Running'"):
        n += wf.id.startswith(prefix)
        limit -= 1
        if limit <= 0:
            break
    return n
