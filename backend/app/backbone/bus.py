"""The event bus contract, and the in-process bus used by tests and by a laptop with no broker.

Publishers hand over messages and get control back only when the bus has them. Consumers belong to a group, read
topics by pattern, and commit after their own work is safely stored: a crash between the two means the messages
come again, never that they are lost. Handlers must therefore be safe to run twice.
"""
from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol


class BusError(Exception):
    """The bus could not be used. The message is safe to show an operator."""


@dataclass(frozen=True)
class Message:
    topic: str
    key: str
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)
    partition: int = 0
    offset: int = -1


class Subscription(Protocol):
    def poll(self, max_messages: int = 500, timeout: float = 1.0) -> list[Message]:
        """The next messages, possibly none. Messages with the same key arrive in the order they were published."""
        ...

    def commit(self) -> None:
        """Everything returned by poll so far has been dealt with."""
        ...

    def rewind(self) -> None:
        """Go back to the last commit, keeping this member in its group: the next poll returns what was read since
        then again. Leaving and rejoining would do the same, but makes every member of the group rebalance."""
        ...

    def close(self) -> None: ...


class EventBus(Protocol):
    url: str

    def publish(self, messages: Iterable[Message]) -> int:
        """Send, and return only when every message is acknowledged. Raises BusError if any is not."""
        ...

    def subscribe(self, group: str, pattern: str) -> Subscription:
        """Read every topic whose name matches the regular expression, as one member of the group."""
        ...

    def ensure_topics(self, names: Iterable[str]) -> None: ...

    def topics(self) -> list[str]: ...

    def close(self) -> None: ...


class MemoryBus:
    """A bus inside one process: an append-only log per topic and a committed position per group."""

    url = "memory://"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: dict[str, list[Message]] = {}
        self._committed: dict[tuple[str, str], int] = {}
        self.fail_next = 0  # tests: refuse this many publishes

    def publish(self, messages: Iterable[Message]) -> int:
        batch = list(messages)
        with self._lock:
            if self.fail_next > 0:
                self.fail_next -= 1
                raise BusError("The bus refused the messages")
            for m in batch:
                log = self._logs.setdefault(m.topic, [])
                log.append(Message(m.topic, m.key, m.value, dict(m.headers), 0, len(log)))
        return len(batch)

    def subscribe(self, group: str, pattern: str) -> "MemorySubscription":
        return MemorySubscription(self, group, re.compile(pattern))

    def ensure_topics(self, names: Iterable[str]) -> None:
        with self._lock:
            for n in names:
                self._logs.setdefault(n, [])

    def topics(self) -> list[str]:
        with self._lock:
            return sorted(self._logs)

    def read(self, topic: str) -> list[Message]:
        with self._lock:
            return list(self._logs.get(topic, []))

    def close(self) -> None:
        pass


class MemorySubscription:
    def __init__(self, bus: MemoryBus, group: str, pattern: re.Pattern) -> None:
        self._bus, self._group, self._pattern = bus, group, pattern
        self._position: dict[str, int] = {}  # read but not yet committed; a new subscription starts from the committed one

    def poll(self, max_messages: int = 500, timeout: float = 1.0) -> list[Message]:
        out: list[Message] = []
        with self._bus._lock:
            for name in sorted(self._bus._logs):
                if not self._pattern.match(name):
                    continue
                at = self._position.get(name, self._bus._committed.get((self._group, name), 0))
                take = self._bus._logs[name][at:at + max_messages - len(out)]
                out.extend(take)
                self._position[name] = at + len(take)
                if len(out) >= max_messages:
                    break
        return out

    def commit(self) -> None:
        with self._bus._lock:
            for name, at in self._position.items():
                self._bus._committed[(self._group, name)] = at

    def rewind(self) -> None:
        self._position.clear()

    def close(self) -> None:
        self._position.clear()


_memory: MemoryBus | None = None
_lock = threading.Lock()


def connect(url: str | None = None) -> EventBus | None:
    """The bus this deployment is configured for, or None when it has none."""
    from ..config import settings

    url = settings.event_bus_url if url is None else url
    if not url:
        return None
    if url.startswith("memory://"):
        global _memory
        with _lock:
            _memory = _memory or MemoryBus()
            return _memory
    if url.startswith("kafka://"):
        from .kafka import KafkaBus

        return KafkaBus(url)
    raise BusError(f"Unknown event bus {url.split('://')[0]!r}. Use memory:// or kafka://host:port.")


def reset_memory() -> None:
    global _memory
    with _lock:
        _memory = None
