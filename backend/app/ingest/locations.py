"""Where a client's data may be read from.

A client cannot point a pipeline at an arbitrary address. The operator registers storage locations for each
client; a data source names one of them and a path inside it. That is what stops one client's pipeline from
reading another client's bucket, or a directory on the server.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from ..config import settings
from ..storage import ObjectStore, StorageError, open_store, provider_of
from ..tenants.registry import runtime

ROOT_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


def check_root(tenant_id: str, root: dict) -> dict:
    """Validate one storage location for a client. Returns the cleaned record or raises StorageError."""
    rid, url = str(root.get("id", "")), str(root.get("url", "")).strip()
    if not ROOT_ID.match(rid):
        raise StorageError(f"Location id '{rid}' must be lower-case letters, digits and hyphens")
    provider = provider_of(url)
    u = urlsplit(url)
    if u.scheme == "memory" and not settings.allow_memory_storage:
        raise StorageError("memory:// locations are for tests only")
    if u.scheme == "file":
        # A directory location must be the client's own folder under the lake directory, never anywhere else on the server.
        home = (settings.lake_dir / tenant_id).resolve()
        path = Path(u.path).resolve()
        if path != home and home not in path.parents:
            raise StorageError(f"A directory location for this client must be inside {home}")
    options = root.get("options") or {}
    open_store(url, options)  # refuses unknown options and reports a missing driver now, not at run time
    return {"id": rid, "label": str(root.get("label") or rid)[:80], "url": url if url.endswith("/") else url + "/",
            "options": options, "provider": provider}


def roots(tenant_id: str) -> list[dict]:
    return list(runtime(tenant_id).settings.get("storage", []))


def root_of(tenant_id: str, root_id: str) -> dict:
    for r in roots(tenant_id):
        if r["id"] == root_id:
            return r
    raise StorageError(f"'{root_id}' is not a storage location registered for this client")


def store_for(tenant_id: str, root_id: str) -> ObjectStore:
    r = root_of(tenant_id, root_id)
    return open_store(r["url"], r.get("options"))


def quarantine_store(tenant_id: str) -> ObjectStore:
    """Where records a pipeline held back are written, kept apart per client and outside the client's own bucket."""
    return open_store(f"{settings.quarantine_url.rstrip('/')}/{tenant_id}/")
