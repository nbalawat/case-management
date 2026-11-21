"""The durable worker: runs the workflows and activities, and keeps Temporal's clocks in step with the database.

    TEMPORAL_URL=temporal://localhost:7233 python -m app.durable.worker

Run one or several; Temporal shares the work between them.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

from ..config import settings
from . import temporal

log = logging.getLogger("caseforge.durable")


async def keep_in_step(client, stop: asyncio.Event, refresh=None) -> None:
    """Every few seconds, make sure each running deadline has its clock. A failure is logged and tried again."""
    while not stop.is_set():
        try:
            if refresh is not None:
                await asyncio.to_thread(refresh)
            done = await temporal.sync(client)
            log.debug("Clocks in step: %s", done)
        except Exception:  # noqa: BLE001
            log.exception("Could not bring the clocks into step; trying again")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.temporal_sync_seconds)
        except asyncio.TimeoutError:  # noqa: UP041
            pass


async def run(stop: asyncio.Event | None = None, refresh=None, client=None) -> None:
    from temporalio.worker import Worker

    from .workflows import StageClock, Sweep

    stop = stop or asyncio.Event()
    client = client or await temporal.connect()
    await temporal.ensure_sweep_schedule(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        worker = Worker(client, task_queue=settings.temporal_task_queue, workflows=[StageClock, Sweep], activities=temporal.activities(), activity_executor=pool)
        async with worker:
            log.info("Durable worker started on %s, queue %s", temporal.target(), settings.temporal_task_queue)
            await keep_in_step(client, stop, refresh)


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
