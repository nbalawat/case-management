"""Fills the lake from the bus: one consumer group, every client, each message written exactly once.

A batch is appended together with the positions it reached, in one table commit. The bus position is committed
afterwards. If the process dies in between, the messages come again and are recognised by their offsets: anything
before the position the table itself recorded is skipped. A message that is not a row (bad JSON, a malformed
transaction) is skipped too, and its position still advances.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from ..backbone import topics
from ..backbone.bus import EventBus, Message, Subscription
from ..config import settings
from ..lineage import jobs as lineage_jobs
from ..observability import metrics
from .base import HistoryStore
from .tables import BY_TOPIC, TABLES

log = logging.getLogger("caseforge.lake")


@dataclass
class Written:
    rows: int = 0
    repeats: int = 0  # redelivered messages already in the table
    skipped: int = 0  # messages that are not rows


class LakeSink:
    """`flush_rows` and `flush_seconds` gather messages across polls before writing, so a busy stream makes a few
    large Parquet files rather than many small ones (each write is one file per table and one table commit). With
    both at 0 every poll is written at once. Nothing is committed on the bus until it is in a table, so messages
    gathered but not yet written when a process stops are read again by the next one."""

    def __init__(self, bus: EventBus, store: HistoryStore, group: str | None = None, *, flush_rows: int = 0, flush_seconds: float = 0.0) -> None:
        self.bus, self.store = bus, store
        self.group = group or f"{settings.event_topic_prefix}-lake"
        self.flush_rows, self.flush_seconds = flush_rows, flush_seconds
        self._sub: Subscription | None = None
        self._pending: list[Message] = []
        self._since = 0.0
        self.lineage = lineage_jobs.Tally("lake.writer")

    def _due(self) -> bool:
        if not self._pending:
            return False
        if not self.flush_rows and not self.flush_seconds:
            return True
        return (bool(self.flush_rows) and len(self._pending) >= self.flush_rows) or \
            (bool(self.flush_seconds) and time.monotonic() - self._since >= self.flush_seconds)

    def step(self, max_messages: int = 2000, timeout: float = 1.0) -> dict[tuple[str, str], Written]:
        if self._sub is None:
            self._sub = self.bus.subscribe(self.group, topics.pattern_any([t.topic for t in TABLES.values()]))
        try:
            got = self._sub.poll(max_messages, timeout)
            if got and not self._pending:
                self._since = time.monotonic()
            self._pending += got
            if not self._due():
                return {}
            done = self._write(self._pending)
            self._sub.commit()
            self._pending = []
            lineage_jobs.flush_if_due(self.lineage)
            return done
        except Exception:
            self._pending = []  # start again from the last commit; the table's own marks prevent repeats
            try:
                self._sub.rewind()  # in place, so the group does not rebalance
            except Exception:  # noqa: BLE001
                sub, self._sub = self._sub, None
                sub.close()
            raise

    def _write(self, messages: list[Message]) -> dict[tuple[str, str], Written]:
        batches: dict[tuple[str, str], list[Message]] = defaultdict(list)
        for m in messages:
            parsed = topics.parse(m.topic)
            if parsed and parsed[1] in BY_TOPIC:
                batches[(parsed[0], BY_TOPIC[parsed[1]].name)].append(m)
        done: dict[tuple[str, str], Written] = {}
        for (tenant_id, name), batch in batches.items():
            table, out = TABLES[name], Written()
            have = self.store.marks(tenant_id, name)
            rows, marks = [], {}
            for m in batch:
                at = f"{m.topic}/{m.partition}"
                if m.offset < have.get(at, 0):
                    out.repeats += 1
                    continue
                marks[at] = max(marks.get(at, 0), m.offset + 1)
                try:
                    rows.append(table.row(m.value))
                except (ValueError, KeyError, TypeError) as e:
                    out.skipped += 1
                    log.info("Not kept in %s.%s: %s", tenant_id, name, e)
            out.rows = self.store.append(tenant_id, name, rows, marks)
            if out.rows:
                metrics.LAKE_ROWS.labels(tenant_id, name).inc(out.rows)
                lineage_jobs.lake_batch(self.lineage, tenant_id, name, table.topic, out.rows)
            done[(tenant_id, name)] = out
        return done

    def close(self) -> None:
        if self._sub is not None:
            self._sub.close()
            self._sub = None
