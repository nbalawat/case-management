"""Runs the backbone's moving parts: the outbox relay and one consumer per inbound stream.

    python -m app.backbone.worker            everything
    python -m app.backbone.worker relay      only the relay
    python -m app.backbone.worker streams    only the consumers
    python -m app.backbone.worker lake       only the writer that keeps history on the lake (needs LAKE_CATALOG)

Any number of these can run; the bus shares the work between consumers in a group and Postgres shares the outbox
between relays. The API process can run the same loop itself with BACKBONE_WORKERS=1, which suits a laptop.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

from ..config import settings
from . import relay, topics
from .bus import BusError, EventBus, connect
from .streams import StreamConsumer

log = logging.getLogger("caseforge.backbone")
IDLE_SECONDS = 0.5
PRUNE_EVERY_SECONDS = 3600


def ensure_client_topics(bus: EventBus) -> None:
    from ..tenants import registry

    bus.ensure_topics(topics.topic(rt.id, name) for rt in registry.all_runtimes() for name in topics.ALL)


def reload_clients() -> None:
    from ..db import session_scope
    from ..tenants.onboarding import hydrate

    with session_scope(platform=True) as s:
        hydrate(s)


PARTS = ("relay", "streams", "lake")


def run(bus: EventBus, stop: threading.Event, parts: tuple[str, ...] = PARTS, refresh=None) -> None:
    """`refresh` reloads the list of clients; a process that is not the API passes it, the API has no need."""
    consumers = [StreamConsumer(bus, name, refresh=refresh) for name in topics.STREAMS] if "streams" in parts else []
    sink = None
    if "lake" in parts:
        from ..lake.iceberg import connect as connect_lake
        from ..lake.sink import LakeSink

        store = connect_lake()
        sink = LakeSink(bus, store, flush_rows=settings.lake_flush_rows, flush_seconds=settings.lake_flush_seconds) if store is not None else None
    pruned = topics_at = 0.0
    while not stop.is_set():
        busy = False
        try:
            if time.monotonic() - topics_at > 60:  # clients onboarded since the last look get their topics
                if refresh is not None and topics_at:
                    refresh()
                ensure_client_topics(bus)
                topics_at = time.monotonic()
            if "relay" in parts:
                busy = relay.drain(bus, limit=5000) > 0
                if time.monotonic() - pruned > PRUNE_EVERY_SECONDS:
                    relay.prune()
                    pruned = time.monotonic()
            for c in consumers:
                busy = bool(c.step(timeout=0.2)) or busy
            if sink is not None:
                busy = bool(sink.step(timeout=0.2)) or busy
        except BusError as e:
            log.warning("Event bus unavailable, retrying: %s", e)
            stop.wait(5)
        except Exception:  # noqa: BLE001  a bad batch must not end the worker; it is retried from the last commit
            log.exception("Backbone worker step failed, retrying")
            stop.wait(5)
        if not busy:
            stop.wait(IDLE_SECONDS)
    for c in consumers + ([sink] if sink is not None else []):
        c.close()


def start_in_process() -> tuple[threading.Thread, threading.Event] | None:
    """For the API process. Returns None when no bus is configured."""
    bus = connect()
    if bus is None:
        return None
    stop = threading.Event()
    t = threading.Thread(target=run, args=(bus, stop), name="backbone", daemon=True)
    t.start()
    return t, stop


def main(argv: list[str]) -> int:
    import os

    from ..observability import logs, metrics, tracing

    logs.configure()
    tracing.configure("caseforge-worker")
    if os.getenv("METRICS_PORT"):  # Prometheus scrapes the worker here; the API serves its own on /metrics
        metrics.serve(int(os.environ["METRICS_PORT"]))
    parts = tuple(argv) or PARTS
    if set(parts) - set(PARTS):
        print(__doc__)
        return 2
    bus = connect()
    if bus is None or settings.event_bus_url.startswith("memory://"):
        print("Set EVENT_BUS_URL to a broker (kafka://host:9092). A separate worker cannot share an in-process bus.")
        return 2
    from ..db import ensure_schema

    ensure_schema()
    reload_clients()
    stop = threading.Event()
    log.info("Backbone worker started: %s on %s", ", ".join(parts), bus.url)
    try:
        run(bus, stop, parts, refresh=reload_clients)
    except KeyboardInterrupt:
        stop.set()
    bus.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
