"""Rate limits for the public API, as token buckets.

The limiter is an interface so the counting can move to a shared store when the API runs on many replicas; the
in-process implementation is exact for one process and is what laptops, tests and a single pod use. A shared
implementation (Redis, Valkey, or a database) would implement `take` the same way.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int  # requests per window
    remaining: int
    reset: int  # seconds until the bucket is full again
    retry_after: int = 0


class RateLimiter(Protocol):
    def take(self, key: str, per_minute: int, cost: int = 1) -> Decision: ...


class MemoryRateLimiter:
    """Buckets of `per_minute` tokens refilled continuously. Bursts up to a full bucket are allowed."""

    def __init__(self, clock=time.monotonic):
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def take(self, key: str, per_minute: int, cost: int = 1) -> Decision:
        rate = per_minute / 60.0
        with self._lock:
            t = self._clock()
            level, last = self._buckets.get(key, (float(per_minute), t))
            level = min(float(per_minute), level + (t - last) * rate)
            ok = level >= cost
            if ok:
                level -= cost
            self._buckets[key] = (level, t)
            if len(self._buckets) > 100_000:  # forget the idlest buckets rather than grow without end
                for k in list(self._buckets)[:10_000]:
                    self._buckets.pop(k, None)
        reset = int((per_minute - level) / rate) if rate else 0
        retry = 0 if ok else max(1, int((cost - level) / rate + 0.999))
        return Decision(ok, per_minute, int(level), reset, retry)


limiter: RateLimiter = MemoryRateLimiter()


def swap(new: RateLimiter) -> RateLimiter:
    global limiter
    old, limiter = limiter, new
    return old
