"""Calling Temporal from ordinary (synchronous) application code.

The API's routes and the database's commit hooks are synchronous; the Temporal client is asyncio. One background
thread owns an event loop and the connections, and synchronous code hands it coroutines. Connections are made once
per namespace and kept.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import TypeVar

R = TypeVar("R")


class Bridge:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="caseforge-temporal", daemon=True).start()
                self._loop = loop
            return self._loop

    def call(self, make: Callable[[], Awaitable[R]], timeout: float) -> R:
        """Run `make()` on the bridge's loop and wait for it. Raises TimeoutError after `timeout` seconds."""
        fut = asyncio.run_coroutine_threadsafe(_wrap(make), self._ensure())
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            fut.cancel()
            raise

    def submit(self, make: Callable[[], Awaitable[object]]) -> None:
        """Run `make()` on the bridge's loop without waiting. Failures are the caller's to log inside `make`."""
        asyncio.run_coroutine_threadsafe(_wrap(make), self._ensure())

    def stop(self) -> None:
        with self._lock:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop = None


async def _wrap(make: Callable[[], Awaitable[R]]) -> R:
    return await make()


bridge = Bridge()
