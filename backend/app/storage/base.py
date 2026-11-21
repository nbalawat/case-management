"""The object storage contract every pipeline is written against.

A store is rooted at one location (a bucket and prefix, a container and path, a directory). Keys are relative
to that root and can never climb out of it. Pipelines list, read and write; they do not know which cloud is
underneath.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Protocol


class StorageError(Exception):
    """A storage location could not be used. The message is safe to show the person configuring it."""


@dataclass(frozen=True)
class ObjectInfo:
    key: str  # relative to the store's root, forward slashes
    size: int
    fingerprint: str  # changes when the content changes: the provider's ETag where there is one
    modified: datetime | None = None


class ObjectStore(Protocol):
    url: str  # the root, for display and lineage

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        """Every object under the prefix, at any depth, in key order."""
        ...

    def open(self, key: str) -> AbstractContextManager[BinaryIO]:
        """Read one object as a binary stream."""
        ...

    def write(self, key: str, data: bytes) -> ObjectInfo: ...

    def exists(self, key: str) -> bool: ...


def safe_key(key: str) -> str:
    """Normalise a key and refuse anything that could address outside the store's root."""
    if not isinstance(key, str) or "\x00" in key or "\\" in key:
        raise StorageError(f"Invalid object key {key!r}")
    parts = [p for p in key.split("/") if p not in ("", ".")]
    if key.startswith("/") or any(p == ".." for p in parts):
        raise StorageError(f"Object key {key!r} points outside the storage location")
    return "/".join(parts)
