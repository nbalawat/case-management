"""An object store whose objects are sealed under one client's key (app/keys).

Wraps any store: what is written is sealed before it leaves the process, and what is read is opened after it
arrives, so the storage provider, its backups and anyone who copies the bucket hold only ciphertext. Objects written
before sealing was turned on are read as they are.
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager

from .. import keys
from ..keys import KeyProvider
from .base import ObjectInfo, ObjectStore, safe_key


class SealedStore:
    def __init__(self, inner: ObjectStore, tenant: str, provider: KeyProvider):
        self.inner, self.tenant, self.keys = inner, tenant, provider
        self.url = inner.url

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        return self.inner.list(prefix)

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def write(self, key: str, data: bytes) -> ObjectInfo:
        k = safe_key(key)
        return self.inner.write(k, keys.seal(self.keys, self.tenant, k, data))

    @contextmanager
    def open(self, key: str):
        k = safe_key(key)
        with self.inner.open(k) as f:
            blob = f.read()
        yield io.BytesIO(keys.open_sealed(self.keys, self.tenant, k, blob))

    def raw(self, key: str) -> bytes:
        """The stored bytes, sealed: for rewrapping after a rotation, and for proving they are not plaintext."""
        with self.inner.open(safe_key(key)) as f:
            return f.read()

    def is_sealed(self, key: str) -> bool:
        with self.inner.open(safe_key(key)) as f:
            return keys.is_sealed(f.read(4))

    def rewrap(self, key: str) -> bool:
        """Re-wrap one object's data key under the client's current key version. False if it was never sealed."""
        blob = self.raw(key)
        if not keys.is_sealed(blob):
            return False
        self.inner.write(safe_key(key), keys.rewrap(self.keys, self.tenant, blob))
        return True


_ready: set[tuple[str, str]] = set()


def sealed(inner: ObjectStore, tenant: str) -> ObjectStore:
    """The store, sealed under the client's key when a key provider is configured. The key is made on first use, so a
    client onboarded before keys existed gets one; a client whose key was destroyed cannot write again."""
    provider = keys.provider()
    if provider is None:
        return inner
    if (provider.name, tenant) not in _ready:
        provider.create(tenant)
        _ready.add((provider.name, tenant))
    return SealedStore(inner, tenant, provider)


def forget(tenant: str) -> None:
    """Drop what this process remembers about a client's key, after it is destroyed or rotated elsewhere."""
    for item in [x for x in _ready if x[1] == tenant]:
        _ready.discard(item)
