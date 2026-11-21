"""Runs each client's due pipelines on a timer, inside the application process.

This is the laptop stand-in for an orchestrator. It is off unless PIPELINE_SCHEDULER=1, and it must run in
exactly one process. On Temporal (WORKFLOW_ENGINE=temporal) leave it off: the durable worker keeps a schedule per
client that calls `tick_one`, once per interval however many workers and API replicas there are.
"""
from __future__ import annotations

import asyncio
import logging

from ..db import session_scope
from ..tenants import registry
from . import pipeline

log = logging.getLogger("caseforge.pipelines")
INTERVAL_SECONDS = 60


def tick_one(tenant_id: str) -> int:
    """Start one client's due pipelines. Temporal's pipeline schedule calls this, one client per workflow."""
    with session_scope(tenant_id) as s:
        runs = pipeline.run_due(s)
        s.commit()
    return len(runs)


def tick() -> dict[str, int]:
    """One pass over every live client. A failure at one client never stops the others."""
    started: dict[str, int] = {}
    for rt in registry.all_runtimes():
        if rt.status != "live":
            continue
        try:
            with session_scope(rt.id) as s:
                runs = pipeline.run_due(s)
                s.commit()
            if runs:
                started[rt.id] = len(runs)
        except Exception:  # noqa: BLE001  one client's broken source is that client's problem
            log.exception("Scheduled pipelines failed for %s", rt.id)
    return started


async def run_forever(interval: float = INTERVAL_SECONDS) -> None:
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(tick)
