"""Fills the lake from the bus: one consumer group, every client, each message written exactly once.

A batch is appended together with the positions it reached, in one table commit. The bus position is committed
afterwards. If the process dies in between, the messages come again and are recognised by their offsets: anything
before the position the table itself recorded is skipped. A message that is not a row (bad JSON, a malformed
transaction) is skipped too, and its position still advances.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from ..backbone import topics
from ..backbone.bus import EventBus, Message, Subscription
from ..config import settings
from .base import HistoryStore
from .tables import BY_TOPIC, TABLES

log = logging.getLogger("caseforge.lake")


@dataclass
class Written:
    rows: int = 0
    repeats: int = 0  # redelivered messages already in the table
    skipped: int = 0  # messages that are not rows


class LakeSink:
    def __init__(self, bus: EventBus, store: HistoryStore, group: str | None = None) -> None:
        self.bus, self.store = bus, store
        self.group = group or f"{settings.event_topic_prefix}-lake"
        self._sub: Subscription | None = None

    def step(self, max_messages: int = 2000, timeout: float = 1.0) -> dict[tuple[str, str], Written]:
        if self._sub is None:
            self._sub = self.bus.subscribe(self.group, topics.pattern_any([t.topic for t in TABLES.values()]))
        try:
            done = self._write(self._sub.poll(max_messages, timeout))
            if done:
                self._sub.commit()
            return done
        except Exception:
            sub, self._sub = self._sub, None  # start again from the last commit; the table's own marks prevent repeats
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
            done[(tenant_id, name)] = out
        return done

    def close(self) -> None:
        if self._sub is not None:
            self._sub.close()
            self._sub = None
