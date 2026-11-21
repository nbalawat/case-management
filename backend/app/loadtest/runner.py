"""Runs the stages in order against whatever this process is configured for (database, bus, lake)."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from ..backbone.bus import connect as connect_bus
from ..config import settings
from ..domain.models import now
from . import stages
from .generate import Population

log = logging.getLogger("caseforge.loadtest")


@dataclass
class Plan:
    transactions: int = 20_000
    customers: int | None = None  # default: one customer for every 50 transactions
    tenant: str = "loadtest"
    seed: int = 7
    inline: bool = False  # run the stream consumer and the lake writer in this process
    api_url: str | None = None
    api_seconds: float = 30.0
    api_concurrency: int = 8
    persona: str = "maya"
    stall_seconds: float = 120.0
    run: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def population(self) -> Population:
        return Population(self.customers or max(100, self.transactions // 50), seed=self.seed)


@dataclass
class Outcome:
    plan: Plan
    started: datetime
    stages: list[stages.Stage]
    environment: dict

    def stage(self, name: str) -> stages.Stage | None:
        return next((s for s in self.stages if s.name == name), None)


def _environment(bus, store) -> dict:
    import os
    import platform

    mem = None
    try:
        mem = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (ValueError, OSError, AttributeError):
        pass
    return {"machine": platform.machine(), "system": f"{platform.system()} {platform.release()}", "cpus": os.cpu_count(), "memory_gb": mem,
            "python": platform.python_version(), "database": settings.database_url.split(":", 1)[0].split("+")[0],
            "bus": bus.url.split("://", 1)[0] if bus is not None else None, "lake": store is not None}


class _Pump:
    """The worker's consumers, run in this process: one stream consumer for transactions and the lake writer."""

    def __init__(self, bus, store) -> None:
        from ..backbone import topics
        from ..backbone.streams import StreamConsumer

        self.stream = StreamConsumer(bus, topics.TRANSACTIONS)
        self.sink = None
        if store is not None:
            from ..lake.sink import LakeSink

            self.sink = LakeSink(bus, store)

    def __call__(self) -> None:
        self.stream.step(max_messages=1_000, timeout=0.2)
        if self.sink is not None:
            self.sink.step(max_messages=5_000, timeout=0.05)

    def close(self) -> None:
        self.stream.close()
        if self.sink is not None:
            self.sink.close()


def run(plan: Plan, *, bus=None, store=None, query_engine=None) -> Outcome:
    if bus is None:
        bus = connect_bus()
    if bus is None:
        raise SystemExit("No event bus is configured (EVENT_BUS_URL). Use memory:// with --inline, or kafka://host:port.")
    if store is None:
        from ..lake.iceberg import connect as connect_lake

        store = connect_lake()
    if query_engine is None and store is not None:
        from ..lake.query import DuckDbEngine

        query_engine = DuckDbEngine(store)
    started = now()
    pop, out = plan.population, []
    remote = stages.onboard_through_api(plan.api_url, plan.tenant) if plan.api_url else None
    created = stages.ensure_client(plan.tenant, remote)
    from ..db import session_scope
    from ..tenants.onboarding import hydrate

    with session_scope(platform=True) as s:  # this process's own list of clients, for the in-process consumer
        hydrate(s)
    log.info("Client %s %s", plan.tenant, "onboarded" if created else "already there")
    out.append(stages.load_customers(plan.tenant, pop))
    log.info("Customers: %s", out[-1].as_dict())

    before = stages.stream_totals(plan.tenant)
    lake_before = stages.lake_rows(store, plan.tenant)
    pump = _Pump(bus, store) if plan.inline else None

    t_publish = time.perf_counter()
    if pump is not None:
        # One process: publish first, then consume. With a worker running, the two overlap, as they would in production.
        out.append(stages.publish(bus, plan.tenant, pop, plan.transactions, run=plan.run, start=started))
    else:
        result: list[stages.Stage] = []
        sender = threading.Thread(target=lambda: result.append(stages.publish(bus, plan.tenant, pop, plan.transactions, run=plan.run, start=started)))
        sender.start()
    t_consume = time.perf_counter() if pump is not None else t_publish

    def received() -> int:
        return stages.stream_totals(plan.tenant)["received"] - before["received"]

    try:
        consumed = stages.wait_for("stream", plan.transactions, received, pump=pump, started=t_consume, stall_seconds=plan.stall_seconds)
        if pump is None:
            sender.join()
            out.append(result[0])
        shape = stages.bus_shape(bus, plan.tenant)  # asked while the consumers are still in the group
        after = stages.stream_totals(plan.tenant)
        consumed.detail.update({k: after[k] - before[k] for k in ("loaded", "duplicates", "rejected", "alerts")})
        consumed.detail |= shape
        consumed.detail["includes"] = "parse, load into the operational store, detection, alert routing and case creation, commit"
        out.append(consumed)
        log.info("Stream: %s", {k: v for k, v in consumed.as_dict().items() if k != "timeline"})
        if store is not None and bus.url.startswith("memory") and lake_before:
            # An in-process bus starts its offsets again at zero in every process, and the lake keeps the offsets it has
            # written, so it would take this run's messages for ones it already has. Kafka keeps offsets; this is a
            # limit of the laptop bus only.
            out.append(stages.Stage("lake", 0, 0.0, complete=False, detail={
                "skipped": "the lake already holds rows from an earlier run on the in-process bus; use Kafka or a fresh lake"}))
        elif store is not None:
            lake = stages.wait_for("lake", plan.transactions, lambda: (stages.lake_rows(store, plan.tenant) or 0) - (lake_before or 0),
                                   pump=pump, started=t_publish, stall_seconds=plan.stall_seconds)
            lake.detail["rows_in_lake"] = stages.lake_rows(store, plan.tenant)
            out.append(lake)
            log.info("Lake: %s", {k: v for k, v in lake.as_dict().items() if k != "timeline"})
    finally:
        if pump is not None:
            pump.close()

    if query_engine is not None:
        out.append(stages.lake_queries(query_engine, plan.tenant))
        log.info("Lake queries: %s", out[-1].detail["queries"])
    if plan.api_url:
        out.append(stages.api_load(plan.api_url, plan.tenant, plan.persona, seconds=plan.api_seconds, concurrency=plan.api_concurrency))
        log.info("API: %s", {k: v for k, v in out[-1].as_dict().items() if k != "pages"})
    env = _environment(bus, store) | {"rows_in_store": stages.count_rows(plan.tenant)}
    return Outcome(plan, started, out, env)
