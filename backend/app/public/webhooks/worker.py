"""The webhook worker: `python -m app.public.webhooks.worker`, or inside the API with WEBHOOK_WORKERS=1."""
from __future__ import annotations

import asyncio
import logging
import time

from starlette.concurrency import run_in_threadpool

from . import service

INTERVAL = 2.0


async def run_forever(interval: float = INTERVAL) -> None:
    while True:
        try:
            await run_in_threadpool(service.run_once)
        except Exception:  # noqa: BLE001
            logging.getLogger("caseforge.webhooks").exception("Webhook pass failed")
        await asyncio.sleep(interval)


def main() -> None:  # pragma: no cover - a process entry point
    from ...db import ensure_schema, session_scope
    from ...tenants.onboarding import hydrate

    logging.basicConfig(level=logging.INFO)
    ensure_schema()
    with session_scope(platform=True) as s:
        hydrate(s)
    while True:
        stats = service.run_once()
        if any(stats.values()):
            logging.info("webhooks: %s", stats)
        time.sleep(INTERVAL)


if __name__ == "__main__":  # pragma: no cover
    main()
