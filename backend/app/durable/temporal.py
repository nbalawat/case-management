"""The Temporal side: connecting, starting and updating clocks, the worker's activities, and what to report.

TEMPORAL_URL=temporal://host:7233. One namespace and one task queue; the client is part of every workflow id.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta

from ..config import settings
from . import clocks

log = logging.getLogger("caseforge.durable")
TENANT = re.compile(r"[a-z][a-z0-9-]{2,30}")


class DurableError(RuntimeError):
    pass


def target(url: str | None = None) -> str:
    url = settings.temporal_url if url is None else url
    if not url.startswith("temporal://") or not url.removeprefix("temporal://"):
        raise DurableError("TEMPORAL_URL must look like temporal://host:7233")
    return url.removeprefix("temporal://").rstrip("/")


def clock_id(tenant_id: str, case_id: str) -> str:
    """A case's clock. One per case, so starting it twice is the same as starting it once."""
    if not TENANT.fullmatch(tenant_id or ""):
        raise DurableError("That is not a client id.")
    return f"{settings.event_topic_prefix}.{tenant_id}.clock.{case_id}"


def sweep_id() -> str:
    return f"{settings.event_topic_prefix}.sweep"


async def connect(url: str | None = None):
    from temporalio.client import Client

    try:
        return await Client.connect(target(url), namespace=settings.temporal_namespace)
    except DurableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise DurableError("Temporal did not answer.") from e


async def sync(client, wanted: dict[str, list[clocks.Clock]] | None = None) -> dict[str, int]:
    """Make Temporal hold a clock for every deadline the database says is running.

    Start-with-signal: a case with no clock gets one; a case whose clock is already running is told its current
    stage, which does nothing unless it has changed. So this can run as often as you like.
    """
    from .workflows import StageClock

    if wanted is None:
        wanted = await asyncio.to_thread(clocks.clocks_by_client)
    done = {}
    for tenant_id, items in wanted.items():
        for c in items:
            await client.start_workflow(StageClock.run, args=[tenant_id, c.case_id, c.entered, c.due], id=clock_id(tenant_id, c.case_id),
                                        task_queue=settings.temporal_task_queue, start_signal="stage", start_signal_args=[c.entered, c.due])
        done[tenant_id] = len(items)
    return done


async def ensure_sweep_schedule(client, every: timedelta | None = None) -> bool:
    """A schedule that runs the periodic sweep. Returns False when it was already there."""
    from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleAlreadyRunningError, ScheduleIntervalSpec, ScheduleSpec

    from .workflows import Sweep

    every = every or timedelta(seconds=settings.temporal_sweep_seconds)
    try:
        await client.create_schedule(sweep_id(), Schedule(
            action=ScheduleActionStartWorkflow(Sweep.run, id=sweep_id(), task_queue=settings.temporal_task_queue),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=every)])))
    except ScheduleAlreadyRunningError:
        return False
    return True


def activities() -> list:
    """The activities the worker registers. Plain functions on threads: they use the application's ordinary sessions."""
    from temporalio import activity

    from .workflows import STAGE_DUE, SWEEP

    @activity.defn(name=STAGE_DUE)
    def stage_due(tenant_id: str, case_id: str, entered: str) -> str:
        return clocks.fire(tenant_id, case_id, entered)

    @activity.defn(name=SWEEP)
    def sweep() -> dict:
        return clocks.sweep_all()

    return [stage_due, sweep]


async def running(client, tenant_id: str, limit: int = 5000) -> int:
    """How many of a client's clocks are running now."""
    prefix, n = clock_id(tenant_id, ""), 0
    async for wf in client.list_workflows("WorkflowType = 'StageClock' AND ExecutionStatus = 'Running'"):
        n += wf.id.startswith(prefix)
        limit -= 1
        if limit <= 0:
            break
    return n


def status(tenant_id: str) -> dict:
    """Where this client's stage clocks run. A Temporal that cannot be reached is reported, not raised."""
    if not settings.temporal_url:
        return {"engine": "sweep", "connected": False, "clocks": None,
                "note": "Stage clocks are checked by the sweep, when it is asked for and when work lists load."}

    async def ask() -> int:
        return await running(await connect(), tenant_id)

    try:
        n = asyncio.run(asyncio.wait_for(ask(), timeout=4))
    except Exception:  # noqa: BLE001
        return {"engine": "temporal", "connected": False, "clocks": None, "note": "Temporal did not answer. The sweep still checks stage clocks."}
    return {"engine": "temporal", "connected": True, "clocks": n, "note": "Temporal holds a durable timer for each open case with a stage deadline."}
