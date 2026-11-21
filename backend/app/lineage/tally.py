"""Lineage for jobs that never finish: the stream consumers and the lake writer. Rows are summed per client and
recorded as one completed run a minute, which is what a catalog needs (this job reads that topic and writes
those tables, this many rows) without an event for every micro-batch."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone

from .events import Dataset


class Tally:
    def __init__(self, job: str, every_seconds: float = 60.0) -> None:
        self.job, self.every = job, every_seconds
        self._lock = threading.Lock()
        self._counts: dict[str, dict[tuple[Dataset, bool], int]] = defaultdict(lambda: defaultdict(int))
        self._since = time.monotonic()
        self._started = datetime.now(timezone.utc)

    def add(self, tenant_id: str, inputs: list[Dataset], outputs: list[tuple[Dataset, int]]) -> None:
        with self._lock:
            c = self._counts[tenant_id]
            for d in inputs:
                c[(d, False)] += 0
            for d, n in outputs:
                if n:
                    c[(d, True)] += n

    def due(self) -> bool:
        return time.monotonic() - self._since >= self.every

    def flush(self, open_session: Callable[[str], object] | None = None) -> int:
        """Record what was tallied, one event per client. Returns how many were recorded."""
        from ..db import session_scope
        from .events import record

        with self._lock:
            counts, self._counts = self._counts, defaultdict(lambda: defaultdict(int))
            started, self._started, self._since = self._started, datetime.now(timezone.utc), time.monotonic()
        n = 0
        for tenant_id, c in counts.items():
            if not any(rows for (_, out), rows in c.items() if out):
                continue
            inputs = [d for (d, out) in c if not out]
            outputs = [Dataset(d.namespace, d.name, d.kind, d.fields, rows) for (d, out), rows in c.items() if out]
            with (open_session or (lambda t: session_scope(t)))(tenant_id) as s:
                record(s, self.job, inputs=inputs, outputs=outputs, started=started, job_type="STREAMING")
            n += 1
        return n
