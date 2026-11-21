"""Runs each client's due pipelines on a timer, inside the application process.

This is the laptop stand-in for an orchestrator. It is off unless PIPELINE_SCHEDULER=1, and it must run in
exactly one process: with several API workers, leave it off and call `POST /api/data/run` with due_only from
whatever scheduler the deployment already has.
"""
from __future__ import annotations

import asyncio
import logging

from ..db import session_scope
from ..tenants import registry
from . import pipeline

log = logging.getLogger("caseforge.pipelines")
INTERVAL_SECONDS = 60


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
