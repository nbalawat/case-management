"""The stages of a load test. Each one does real work through the platform's own code paths and returns what it
measured. None of them estimates: a stage that could not finish says how far it got."""
from __future__ import annotations

import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select

from ..backbone import topics
from ..backbone.bus import EventBus, Message
from ..db import session_scope
from ..domain.models import Account, Party, StreamStat, Tenant
from .generate import Population, encode, transactions


@dataclass
class Stage:
    name: str
    items: int = 0
    seconds: float = 0.0
    complete: bool = True
    detail: dict = field(default_factory=dict)

    @property
    def rate(self) -> float:
        return self.items / self.seconds if self.seconds > 0 else 0.0

    def as_dict(self) -> dict:
        return {"name": self.name, "items": self.items, "seconds": round(self.seconds, 3), "per_second": round(self.rate, 1),
                "complete": self.complete, **self.detail}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


# ---- set-up -------------------------------------------------------------------------------------------------------

def ensure_client(tenant_id: str, onboard_remote: Callable[[], None] | None = None, template: str = "full_service_bank") -> bool:
    """Onboard the load-test client if it is not there yet. True when it was created now.

    Through the API when one is given, so the running API knows the client at once; otherwise directly.
    """
    with session_scope(platform=True) as s:
        if s.get(Tenant, tenant_id) is not None:
            return False
    if onboard_remote is not None:
        onboard_remote()
        return True
    from ..tenants.onboarding import onboard

    with session_scope(platform=True) as s:
        onboard(s, tenant_id=tenant_id, name="Load test", template=template, by="load test")
    return True


def load_customers(tenant_id: str, pop: Population, chunk: int = 5_000) -> Stage:
    """Add the customers and accounts that are not there yet, through the tenant session so each row is stamped."""
    t0 = time.perf_counter()
    added = 0
    with session_scope(tenant_id) as s:
        have = set(s.scalars(select(Party.id).where(Party.id.like(f"{pop.prefix}-C%"))))
        for party, account in pop.customer_rows():
            if party["id"] in have:
                continue
            s.add(Party(**party))
            s.add(Account(**account))
            added += 1
            if added % chunk == 0:
                s.flush()
                s.expunge_all()
    return Stage("customers", added, time.perf_counter() - t0, detail={"already_there": len(have), "total": pop.customers})


# ---- publish ------------------------------------------------------------------------------------------------------

def publish(bus: EventBus, tenant_id: str, pop: Population, count: int, *, run: str, start: datetime, batch: int = 2_000) -> Stage:
    """Publish `count` transactions to the client's transactions topic. Times generation and sending apart."""
    topic = topics.topic(tenant_id, topics.TRANSACTIONS)
    bus.ensure_topics(topics.topic(tenant_id, name) for name in topics.ALL)
    sent, generating, sending = 0, 0.0, 0.0
    t0 = time.perf_counter()
    stream = transactions(pop, count, run=run, start=start)
    while sent < count:
        g0 = time.perf_counter()
        msgs = []
        for t in stream:
            key, value = encode(t)
            msgs.append(Message(topic, key, value))
            if len(msgs) >= batch:
                break
        g1 = time.perf_counter()
        if not msgs:
            break
        bus.publish(msgs)
        sending += time.perf_counter() - g1
        generating += g1 - g0
        sent += len(msgs)
    return Stage("publish", sent, time.perf_counter() - t0,
                 detail={"generate_seconds": round(generating, 3), "send_seconds": round(sending, 3),
                         "send_per_second": round(sent / sending, 1) if sending else 0.0, "topic": topic})


def bus_shape(bus: EventBus, tenant_id: str) -> dict:
    """How the client's transactions topic is spread, and how many consumers share it: what a run's throughput is per."""
    from ..config import settings

    out: dict = {}
    if hasattr(bus, "partitions"):
        out["partitions"] = bus.partitions(topics.topic(tenant_id, topics.TRANSACTIONS))
    if hasattr(bus, "group_members"):
        out["stream_consumers"] = bus.group_members(f"{settings.event_topic_prefix}-{topics.TRANSACTIONS}")
    return out


# ---- consume ------------------------------------------------------------------------------------------------------

def stream_totals(tenant_id: str) -> dict:
    with session_scope(tenant_id) as s:
        stat = s.scalars(select(StreamStat).where(StreamStat.stream == topics.TRANSACTIONS)).first()
        if stat is None:
            return {"received": 0, "loaded": 0, "duplicates": 0, "rejected": 0, "alerts": 0}
        return {"received": stat.received, "loaded": stat.loaded, "duplicates": stat.duplicates, "rejected": stat.rejected, "alerts": stat.alerts}


def lake_rows(store, tenant_id: str) -> int | None:
    if store is None:
        return None
    return next((t.rows for t in store.tables(tenant_id) if t.name == "transactions"), 0)


def wait_for(name: str, target: int, progress: Callable[[], int], *, pump: Callable[[], None] | None = None,
             started: float | None = None, stall_seconds: float = 120.0, poll: float = 1.0) -> Stage:
    """Wait until `progress()` reaches `target`, pumping in-process consumers if there are any.

    Gives up after `stall_seconds` without movement and reports how far it got. The timeline (seconds, count) is
    kept so the report can show whether throughput held steady or fell away as the tables grew.
    """
    t0 = started if started is not None else time.perf_counter()
    first_moved: float | None = None
    from_count = 0
    last, last_change, looked = 0, time.perf_counter(), False
    timeline: list[tuple[float, int]] = []
    while True:
        if pump is not None:
            pump()
        else:
            time.sleep(poll)
        done = progress()
        now = time.perf_counter()
        if done != last:
            if first_moved is None and looked:  # moved while we watched, not already done before the first look
                first_moved, from_count = now, last
            last, last_change = done, now
            if not timeline or now - t0 - timeline[-1][0] >= max(poll, 1.0):
                timeline.append((round(now - t0, 2), done))
        looked = True
        if done >= target:
            break
        if now - last_change > stall_seconds:
            return Stage(name, done, last_change - t0, complete=False,
                         detail={"target": target, "stalled_after_seconds": stall_seconds, "timeline": timeline})
    seconds = time.perf_counter() - t0
    timeline.append((round(seconds, 2), last))
    detail: dict = {"target": target, "timeline": timeline}
    if first_moved is not None:  # throughput from the first item seen to the last, so start-up and queueing are left out
        busy = time.perf_counter() - first_moved
        detail |= {"busy_seconds": round(busy, 3), "busy_per_second": round((last - from_count) / busy, 1) if busy else None}
    return Stage(name, last, seconds, detail=detail)


# ---- lake queries -------------------------------------------------------------------------------------------------

LAKE_QUERIES = (
    ("count every transaction", "SELECT count(*) AS n FROM transactions"),
    ("volume by channel", "SELECT channel, count(*) AS n, round(sum(amount), 2) AS total FROM transactions GROUP BY channel ORDER BY n DESC"),
    ("daily volume", "SELECT date_trunc('day', ts) AS day, count(*) AS n, round(sum(amount), 2) AS total FROM transactions GROUP BY ALL ORDER BY 1"),
    ("top 20 customers by cash in", "SELECT party_id, count(*) AS n, round(sum(amount), 2) AS total FROM transactions "
                                     "WHERE channel = 'cash' AND direction = 'in' GROUP BY party_id ORDER BY total DESC LIMIT 20"),
    ("wires to higher-risk countries", "SELECT counterparty_country, count(*) AS n FROM transactions WHERE channel = 'wire' "
                                        "AND counterparty_country IN ('IR', 'KP', 'SY', 'MM', 'AF') GROUP BY 1 ORDER BY 2 DESC"),
)


def lake_queries(engine, tenant_id: str, repeats: int = 3) -> Stage:
    """Each query `repeats` times; the median is reported, with the rows the scan covered."""
    out = []
    t0 = time.perf_counter()
    for label, sql in LAKE_QUERIES:
        times, scanned = [], {}
        for _ in range(repeats):
            r = engine.query(tenant_id, sql)
            times.append(r.seconds)
            scanned = r.scanned.get("transactions", {})
        out.append({"query": label, "median_seconds": round(statistics.median(times), 4), "rows_scanned": scanned.get("rows"),
                    "files": scanned.get("files")})
    return Stage("lake queries", len(LAKE_QUERIES) * repeats, time.perf_counter() - t0, detail={"engine": engine.name, "queries": out})


# ---- API ----------------------------------------------------------------------------------------------------------

API_PATHS = ("/api/case-list", "/api/dashboard", "/api/work", "/api/mi", "/api/search?q=load", "/api/overview")


def _get(url: str, headers: dict, timeout: float) -> tuple[float, int]:
    req = urllib.request.Request(url, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        code = 0
    return time.perf_counter() - t0, code


def api_load(base_url: str, tenant_id: str, persona: str, *, seconds: float = 30.0, concurrency: int = 8,
             paths: Iterable[str] = API_PATHS, timeout: float = 30.0) -> Stage:
    """`concurrency` callers each asking for the pages in turn for `seconds`. Latency is measured per request."""
    headers = {"X-Tenant": tenant_id, "X-Persona": persona, "Accept": "application/json"}
    paths = tuple(paths)
    stop = time.perf_counter() + seconds
    lock = threading.Lock()
    by_path: dict[str, list[float]] = {p: [] for p in paths}
    codes: dict[str, int] = {}

    def caller(i: int) -> None:
        n = i
        while time.perf_counter() < stop:
            p = paths[n % len(paths)]
            n += 1
            took, code = _get(base_url.rstrip("/") + p, headers, timeout)
            with lock:
                by_path[p].append(took)
                codes[str(code)] = codes.get(str(code), 0) + 1

    t0 = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        list(pool.map(caller, range(concurrency)))
    took = time.perf_counter() - t0
    every = [x for v in by_path.values() for x in v]
    ms = lambda v: round(v * 1000, 1)
    pages = [{"path": p, "requests": len(v), "p50_ms": ms(percentile(v, 50)), "p95_ms": ms(percentile(v, 95)), "p99_ms": ms(percentile(v, 99))}
             for p, v in by_path.items()]
    errors = sum(n for c, n in codes.items() if not c.startswith("2"))
    return Stage("api", len(every), took, complete=errors == 0,
                 detail={"concurrency": concurrency, "p50_ms": ms(percentile(every, 50)), "p95_ms": ms(percentile(every, 95)),
                         "p99_ms": ms(percentile(every, 99)), "status_codes": codes, "errors": errors, "pages": pages})


def onboard_through_api(base_url: str, tenant_id: str, template: str = "full_service_bank") -> Callable[[], None]:
    def go() -> None:
        body = json.dumps({"id": tenant_id, "name": "Load test", "template": template}).encode()
        req = urllib.request.Request(base_url.rstrip("/") + "/api/platform/tenants", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Operator": "olivia"})
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    return go


def count_rows(tenant_id: str) -> int:
    from ..domain.models import Transaction

    with session_scope(tenant_id) as s:
        return s.scalar(select(func.count()).select_from(Transaction)) or 0
