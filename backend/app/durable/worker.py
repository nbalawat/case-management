"""The durable worker: runs case workflows and their activities, one Temporal worker per client namespace.

    TEMPORAL_URL=temporal://localhost:7233 python -m app.durable.worker

Run one or several; Temporal shares the work between them. Every few seconds each worker process also makes sure that
every live client has its namespace, its schedules (the sweep, due pipelines) and a workflow for each open case, so
cases opened while Temporal was away, or before it was switched on, are picked up.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

from ..config import settings
from . import namespaces, temporal

log = logging.getLogger("caseforge.durable")


class Fleet:
    """The Temporal workers of this process, one per namespace, added as clients go live."""

    def __init__(self, base, pool: ThreadPoolExecutor) -> None:
        self.base, self.pool = base, pool
        self.workers: dict[str, tuple[object, asyncio.Task]] = {}
        self.scheduled: set[str] = set()

    async def cover(self, tenant_ids: list[str]) -> None:
        from temporalio.worker import Worker

        from .workflows import WORKFLOWS

        for tenant_id in tenant_ids:
            ns = namespaces.namespace_for(tenant_id)
            client = await temporal.client_for(self.base, tenant_id)
            if ns in self.workers and self.workers[ns][1].done():  # it stopped (the namespace was briefly unknown, say): start it again
                failed = self.workers.pop(ns)[1]
                log.warning("Worker for namespace %s stopped (%s); starting it again", ns, failed.exception() if not failed.cancelled() else "cancelled")
            if ns not in self.workers:
                w = Worker(client, task_queue=settings.temporal_task_queue, workflows=WORKFLOWS, activities=temporal.activities(), activity_executor=self.pool)
                self.workers[ns] = (w, asyncio.create_task(w.run(), name=f"worker:{ns}"))
                log.info("Durable worker polling namespace %s, queue %s", ns, settings.temporal_task_queue)
            if tenant_id not in self.scheduled and settings.temporal_schedules:
                await temporal.ensure_schedules(client, tenant_id)
                self.scheduled.add(tenant_id)

    async def shutdown(self) -> None:
        for w, task in self.workers.values():
            await w.shutdown()
            await asyncio.gather(task, return_exceptions=True)
        self.workers.clear()


def live_clients() -> list[str]:
    from ..tenants import registry

    return [rt.id for rt in registry.all_runtimes() if rt.status == "live"]


async def keep_in_step(fleet: Fleet, stop: asyncio.Event, refresh=None) -> None:
    """Every few seconds: workers, schedules and case workflows for every live client. A failure is logged and tried again."""
    while not stop.is_set():
        try:
            if refresh is not None:
                await asyncio.to_thread(refresh)
            wanted = await asyncio.to_thread(temporal.open_by_client)
            await fleet.cover(list(wanted))
            done = await temporal.sync(fleet.base, wanted)
            sent = await temporal.sync_filings(fleet.base)
            log.debug("Case workflows in step: %s; filing workflows: %s", done, sent)
        except Exception:  # noqa: BLE001
            log.exception("Could not bring case workflows into step; trying again")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.temporal_sync_seconds)
        except asyncio.TimeoutError:  # noqa: UP041
            pass


async def run(stop: asyncio.Event | None = None, refresh=None, client=None) -> None:
    from .. import workflow  # noqa: F401  cases opened by activities (QA samples, KYC triggers, pipelines) are adopted after commit

    stop = stop or asyncio.Event()
    base = client or await temporal.connect()
    with ThreadPoolExecutor(max_workers=16) as pool:
        fleet = Fleet(base, pool)
        try:
            await keep_in_step(fleet, stop, refresh)
        finally:
            await fleet.shutdown()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not settings.temporal_url:
        print("Set TEMPORAL_URL to a Temporal server (temporal://host:7233).")
        return 2
    from ..backbone.worker import reload_clients
    from ..db import ensure_schema

    ensure_schema()
    reload_clients()
    try:
        asyncio.run(run(refresh=reload_clients))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
