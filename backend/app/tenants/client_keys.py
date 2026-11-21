"""Operating a client's key: what it is, rotating it, and destroying it at offboarding.

Rotation makes a new key version and then re-wraps the data key of every sealed document under it, without
re-encrypting the documents themselves, so the old version protects nothing afterwards. Destruction removes every
version: every document sealed under the key becomes unreadable for good, wherever a copy of it sits.

Each of these is written to the client's own access record, which the provider cannot edit.
"""
from __future__ import annotations

from .. import keys
from ..db import session_scope
from ..domain.models import AccessEvent
from ..kyc import evidence
from ..storage.sealed import SealedStore, forget


def _record(tenant: str, actor: str, kind: str, **detail) -> None:
    with session_scope(tenant) as s:
        s.add(AccessEvent(actor=actor, org="provider", kind=kind, subject="client key", detail=detail))


def status(tenant: str) -> dict:
    p = keys.provider()
    if p is None:
        return {"tenant": tenant, "provider": "none", "sealed": False, "summary": "Documents are stored as given (KEY_PROVIDER=none)"}
    try:
        info = p.info(tenant)
    except keys.KeyServiceError as e:
        return {"tenant": tenant, "provider": p.name, "sealed": True, "error": str(e), "summary": f"Key service unavailable: {e}"}
    return {"tenant": tenant, "provider": p.name, "sealed": True, "version": info.version, "destroyed": info.destroyed,
            "created": info.created.isoformat() if info.created else None, "rotated": info.rotated.isoformat() if info.rotated else None,
            "reference": info.reference, "summary": info.describe()}


def documents(tenant: str) -> tuple[int, int]:
    """(sealed, not sealed) among the client's stored documents."""
    store = evidence.store(tenant)
    if not isinstance(store, SealedStore):
        return 0, sum(1 for _ in store.list())
    sealed = plain = 0
    for o in store.list():
        if store.is_sealed(o.key):
            sealed += 1
        else:
            plain += 1
    return sealed, plain


def rotate(tenant: str, actor: str) -> dict:
    p = keys.provider()
    if p is None:
        raise keys.KeyServiceError("No key service is configured (KEY_PROVIDER=none)")
    info = p.rotate(tenant)
    forget(tenant)
    store = evidence.store(tenant)
    rewrapped = sum(1 for o in store.list() if isinstance(store, SealedStore) and store.rewrap(o.key))
    _record(tenant, actor, "key.rotated", version=info.version, rewrapped=rewrapped)
    return status(tenant) | {"rewrapped": rewrapped}


def destroy(tenant: str, actor: str) -> dict:
    """Crypto-shred: irreversible. Callers (offboarding) decide when; this only does it and records it."""
    p = keys.provider()
    if p is None:
        return status(tenant)
    sealed_count, _ = documents(tenant)
    _record(tenant, actor, "key.destroyed", documents=sealed_count)  # before: once the key is gone the record still stands
    p.destroy(tenant)
    forget(tenant)
    return status(tenant) | {"documents": sealed_count}
