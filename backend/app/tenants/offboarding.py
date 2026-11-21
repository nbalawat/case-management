"""A client leaving the platform: hand over everything it has here, then erase it everywhere the platform keeps it.

Three steps, each its own call, so a person checks between them:

1. `export`: the client must be suspended first, so nothing changes under the export. Every row of every client
   table, every document (decrypted), the landing zone, quarantined records and the client's lake history go into
   one zip with a manifest that lists each file's SHA-256 and each table's row count. The bundle is stored sealed
   under the client's own key, so it is as protected as the documents it holds.
2. `download`: the bundle is opened, its SHA-256 checked against the record, and the download recorded. This is
   the hand-over.
3. `offboard`: only after a hand-over, with the client's name typed as confirmation, and only if every table still
   has the rows the export counted. Then each eraser below runs. Any failure stops the run before the database rows
   and the key are touched, and is recorded, so the run can simply be repeated: every eraser is safe to run twice.

What the platform cannot remove is written down in the record as `remains`, not glossed over: backups taken
before the offboarding hold the client's database rows until the backup retention removes them, and storage the
client registered in its own cloud account is the client's to clear.

The record (`Offboarding`) is a platform table, so it outlives the client: it is the provider's proof.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import tempfile
import uuid
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..backup.codec import encode
from ..config import settings
from ..db import Base, session_scope
from ..domain.models import Offboarding, Tenant
from ..storage import ObjectStore, StorageError, open_store
from ..storage.sealed import sealed
from ..tenancy import SCOPED

log = logging.getLogger("caseforge.offboarding")
FORMAT = "caseforge-offboarding/1"
# The client's access record gains this module's own entries (the download, the key's destruction) after the export.
# They are not a change to the client's data, and the Offboarding record keeps them, so the change check skips them.
SELF_RECORDED = {"access_event"}


class OffboardingError(ValueError):
    """The step cannot be taken now. The message says why, in words an operator can act on."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---- what a client has here -----------------------------------------------------------------------------------------
def client_models() -> list[type]:
    """Every tenant-scoped model, parents before children (the order the database's foreign keys need)."""
    from ..domain import models  # noqa: F401  (register every table)

    by_table = {mp.local_table.name: mp.class_ for mp in Base.registry.mappers if issubclass(mp.class_, SCOPED)}
    return [by_table[t.name] for t in Base.metadata.sorted_tables if t.name in by_table]


def _row(obj) -> dict:
    out = {}
    for prop in obj.__mapper__.column_attrs:
        v = getattr(obj, prop.key)
        out[prop.columns[0].name] = {"$base64": base64.b64encode(v).decode()} if isinstance(v, (bytes, bytearray, memoryview)) else v
    return out


def counts(tenant_id: str) -> dict[str, int]:
    with session_scope(tenant_id) as s:
        return {m.__table__.name: int(s.scalar(select(func.count()).select_from(m)) or 0) for m in client_models()}


def documents_store(tenant_id: str) -> ObjectStore:
    from ..kyc import evidence

    return evidence.store(tenant_id)


def landing_store(tenant_id: str) -> ObjectStore:
    """The platform's own landing zone for the client (a folder under the lake directory)."""
    return open_store(f"file://{(settings.lake_dir / tenant_id).resolve()}/")


def quarantine_store(tenant_id: str) -> ObjectStore:
    from ..ingest.locations import quarantine_store as q

    return q(tenant_id)


def exports_store(tenant_id: str, *, as_stored: bool = False) -> ObjectStore:
    """Where bundles are kept, sealed under the client's key. `as_stored` gives the bytes as stored, for erasing:
    erasing must still work once the key is gone."""
    inner = open_store(f"{settings.exports_url.rstrip('/')}/{tenant_id}/")
    return inner if as_stored else sealed(inner, tenant_id)


def documents_as_stored(tenant_id: str) -> ObjectStore:
    return open_store(f"{settings.evidence_url.rstrip('/')}/{tenant_id}/")


def _objects(store: ObjectStore) -> Iterator[tuple[str, bytes]]:
    for o in store.list():
        with store.open(o.key) as f:
            yield o.key, f.read()


# ---- 1. export ------------------------------------------------------------------------------------------------------
class _Bundle:
    """A zip being written, with the SHA-256 and size of each file kept for the manifest."""

    def __init__(self, fileobj) -> None:
        self.zip = zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)
        self.files: dict[str, dict] = {}

    def add(self, path: str, data: bytes) -> None:
        self.zip.writestr(path, data)
        self.files[path] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def add_lines(self, path: str, lines: Iterator[str]) -> int:
        """Write JSON lines without holding the whole table, hashing as it goes."""
        h, size, n = hashlib.sha256(), 0, 0
        with self.zip.open(path, "w", force_zip64=True) as f:
            for line in lines:
                b = (line + "\n").encode()
                f.write(b)
                h.update(b)
                size += len(b)
                n += 1
        self.files[path] = {"sha256": h.hexdigest(), "bytes": size}
        return n


def _client_row(s: Session, tenant_id: str) -> Tenant:
    row = s.get(Tenant, tenant_id)
    if row is None:
        raise OffboardingError(f"No client '{tenant_id}'")
    return row


def export(tenant_id: str, actor: str) -> dict:
    """Write the hand-over bundle and record it. The client must be suspended."""
    with session_scope(platform=True) as s:
        t = _client_row(s, tenant_id)
        if t.status != "suspended":
            raise OffboardingError(f"Suspend {t.name} first, so nothing changes while its data is exported")
        about = {"id": t.id, "name": t.name, "template": t.template, "region": t.region, "deployment": t.deployment,
                 "created": t.created.isoformat() if t.created else None, "case_types": t.case_types, "settings": t.settings}
    oid, at = f"off-{uuid.uuid4().hex[:12]}", _now()
    tables: dict[str, int] = {}
    with tempfile.TemporaryFile() as tmp:
        b = _Bundle(tmp)
        with session_scope(tenant_id) as s:
            for model in client_models():
                rows = (encode(_row(o)) for o in s.scalars(select(model).execution_options(yield_per=5000)))
                tables[model.__table__.name] = b.add_lines(f"tables/{model.__table__.name}.jsonl", rows)
                s.expunge_all()
        sections = {"documents": documents_store, "landing": landing_store, "quarantine": quarantine_store}
        found = {name: 0 for name in sections}
        for name, store_of in sections.items():
            for key, data in _objects(store_of(tenant_id)):
                b.add(f"{name}/{key}", data)
                found[name] += 1
        lake_tables = _export_lake(b, tenant_id)
        manifest = {"format": FORMAT, "id": oid, "client": about, "exported_at": at.isoformat(), "exported_by": actor,
                    "tables": tables, "objects": found, "lake": lake_tables, "files": dict(sorted(b.files.items()))}
        b.zip.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True, default=str))
        b.zip.writestr("README.txt", _README)
        b.zip.close()
        tmp.seek(0)
        blob = tmp.read()
    digest, key = hashlib.sha256(blob).hexdigest(), f"{oid}.zip"
    exports_store(tenant_id).write(key, blob)
    record_counts = {"tables": tables, "objects": found, "lake": lake_tables}
    with session_scope(platform=True) as s:
        s.add(Offboarding(id=oid, tenant_id=tenant_id, name=about["name"], status="exported", exported_by=actor, exported_at=at,
                          export_key=key, export_sha256=digest, export_bytes=len(blob), counts=record_counts))
    _note(tenant_id, actor, "offboarding.exported", export=oid, sha256=digest, bytes=len(blob))
    return describe(oid)


def _export_lake(b: _Bundle, tenant_id: str) -> dict[str, int]:
    from ..lake.iceberg import connect

    store = connect()
    if store is None:
        return {}
    out = {}
    for info in store.tables(tenant_id):
        out[info.name] = b.add_lines(f"lake/{info.name}.jsonl", (encode(r) for r in store.scan(tenant_id, info.name)))
    return out


_README = """This archive holds everything the platform kept for your organisation, as it stood when it was exported.

manifest.json      what is here: each file's SHA-256 and size, each table's row count, and when and by whom it was made
tables/<t>.jsonl   every row of every table, one JSON object per line, columns by their database names
documents/         every document you uploaded, as uploaded (decrypted)
landing/           files delivered to your landing zone on the platform
quarantine/        records a data pipeline held back
lake/<t>.jsonl     your history on the lake, when the platform keeps one

Check it with any SHA-256 tool against manifest.json before confirming the hand-over.
"""


# ---- 2. hand-over ---------------------------------------------------------------------------------------------------
def _bundle(rec: Offboarding) -> bytes:
    with exports_store(rec.tenant_id).open(rec.export_key) as f:
        blob = f.read()
    if hashlib.sha256(blob).hexdigest() != rec.export_sha256:
        raise OffboardingError("The stored export does not match its recorded fingerprint. Export again.")
    return blob


def download(oid: str, actor: str) -> tuple[str, bytes]:
    """The bundle, checked against its fingerprint. Recording the download is what makes it handed over."""
    with session_scope(platform=True) as s:
        rec = s.get(Offboarding, oid)
        if rec is None or rec.status == "completed":
            raise OffboardingError("No export to download")
        blob = _bundle(rec)
        rec.downloaded_by, rec.downloaded_at, rec.status = actor, _now(), "handed_over"
        tenant_id, name = rec.tenant_id, f"{rec.tenant_id}-{rec.exported_at:%Y%m%d-%H%M%S}-export.zip"
    _note(tenant_id, actor, "offboarding.downloaded", export=oid)
    return name, blob


# ---- 3. erase -------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Eraser:
    id: str
    label: str
    run: Callable[[str], dict | None]  # None: not configured here, so nothing to erase


def _erase_store(store: ObjectStore) -> dict:
    n = 0
    for o in list(store.list()):
        store.delete(o.key)
        n += 1
    _prune(store.url)
    return {"objects": n}


def _prune(url: str) -> None:
    """On a local directory, remove the folders left empty too: a folder named for the client is itself a trace."""
    import os
    from pathlib import Path

    if not url.startswith("file://"):
        return  # object stores have no folders, only keys
    root = Path(url.removeprefix("file://"))
    if not root.is_dir():
        return
    for here, dirs, files in os.walk(root, topdown=False):
        if not files and not any((Path(here) / d).exists() for d in dirs):
            Path(here).rmdir()


def _erase_temporal(tenant_id: str) -> dict | None:
    if not settings.temporal_url:
        return None
    from ..durable import temporal
    from ..durable.bridge import bridge

    async def go():
        return await temporal.forget(await temporal.connect(), tenant_id)

    return bridge.call(go, timeout=120)


def _erase_bus(tenant_id: str) -> dict | None:
    from ..backbone.bus import connect

    bus = connect()
    if bus is None:
        return None
    prefix = f"{settings.event_topic_prefix}.{tenant_id}."
    return {"topics": bus.delete_topics([t for t in bus.topics() if t.startswith(prefix)])}


def _erase_search(tenant_id: str) -> dict | None:
    from ..entities import search

    engine = search.current()
    if not hasattr(engine, "drop"):  # the database index: its rows go with the client's tables
        return None
    return {"indices": engine.drop(tenant_id)}


def _erase_graph(tenant_id: str) -> dict | None:
    from ..entities import graphstore

    engine = graphstore.current()
    if engine is None:
        return None
    engine.drop(tenant_id)
    return {"graph": "dropped"}


def _erase_lake(tenant_id: str) -> dict | None:
    from ..lake.iceberg import connect

    store = connect()
    return None if store is None else {"tables": store.drop(tenant_id)}


def _erase_backups(tenant_id: str) -> dict:
    """Backups of this client alone. Whole-platform backups hold every client and stay (see `remains`)."""
    from ..backup import service as backups

    store, gone = backups.target(), 0
    for b in backups.list_backups(store):
        if b.scope == tenant_id:
            for o in list(store.list(f"{b.id}/")):
                store.delete(o.key)
            _prune(f"{store.url.rstrip('/')}/{b.id}/")
            gone += 1
    return {"backups": gone}


# Outside the database first; then the documents and the bundle; the key; and the rows last of all. A failure in any
# of these stops the run before the rows and the key, which cannot be brought back.
OUTSIDE: tuple[Eraser, ...] = (
    Eraser("workflows", "Workflow history (Temporal)", _erase_temporal),
    Eraser("events", "Event bus topics", _erase_bus),
    Eraser("search", "Search index", _erase_search),
    Eraser("graph", "Payment network graph", _erase_graph),
    Eraser("lake", "Lake history", _erase_lake),
    Eraser("landing", "Landing zone", lambda t: _erase_store(landing_store(t))),
    Eraser("quarantine", "Quarantined records", lambda t: _erase_store(quarantine_store(t))),
    Eraser("documents", "Documents", lambda t: _erase_store(documents_as_stored(t))),
    Eraser("backups", "Backups of this client alone", _erase_backups),
)


def _erase_rows(tenant_id: str) -> dict[str, int]:
    out = {}
    with session_scope(tenant_id) as s:
        for model in reversed(client_models()):
            n = s.execute(delete(model).where(model.tenant_id == tenant_id)).rowcount
            if n:
                out[model.__table__.name] = int(n)
    return out


def _remains(tenant_id: str, at: datetime) -> list[dict]:
    from ..tenants.registry import runtime

    out = [{"what": "Whole-platform backups", "where": settings.backup_url.split("?")[0],
            "why": f"Backups of the whole platform made before {at:%Y-%m-%d %H:%M} UTC hold every client, this one included, until "
                   "they are deleted under the backup retention. This client's documents in them are sealed under the key destroyed "
                   "today, so only its database rows remain readable there. Backups of this client alone are deleted."}]
    for r in runtime(tenant_id).settings.get("storage", []):
        if not str(r.get("url", "")).startswith("file://"):
            out.append({"what": f"Storage location '{r.get('label') or r.get('id')}'", "where": r.get("url"),
                        "why": "It is in the client's own cloud account, so the client clears it, not the platform."})
    return out


def offboard(tenant_id: str, actor: str, confirm: str) -> dict:
    """Erase the client everywhere the platform keeps it. Irreversible: callers confirm with the client's name."""
    from . import client_keys
    from .registry import remove

    with session_scope(platform=True) as s:
        t = _client_row(s, tenant_id)
        if confirm.strip() != t.name:
            raise OffboardingError(f"Type the client's name, {t.name}, to confirm")
        if t.status != "suspended":
            raise OffboardingError(f"{t.name} is not suspended")
        rec = s.scalars(select(Offboarding).where(Offboarding.tenant_id == tenant_id, Offboarding.status != "completed")
                        .order_by(Offboarding.exported_at.desc())).first()
        if rec is None or rec.status != "handed_over":
            raise OffboardingError("Export the client's data and download the bundle first: erasing comes only after the hand-over")
        oid, counted, at = rec.id, rec.counts.get("tables", {}), _now()
    now_counts = counts(tenant_id)
    changed = sorted(k for k in set(counted) | set(now_counts) if k not in SELF_RECORDED and counted.get(k, 0) != now_counts.get(k, 0))
    if changed:
        raise OffboardingError(f"Data changed after the export ({', '.join(changed[:5])}). Export again and hand it over.")
    remains = _remains(tenant_id, at)
    erased: dict[str, dict] = {}
    for e in OUTSIDE:
        try:
            result = e.run(tenant_id)
        except Exception as ex:  # noqa: BLE001  any failure stops before what cannot be undone; the message is kept
            log.warning("offboarding %s stopped at %s: %s", tenant_id, e.id, ex)
            _save(oid, erased=erased, error=f"{e.label}: {str(ex)[:300]}")
            raise OffboardingError(f"Stopped at {e.label.lower()}: {str(ex)[:200]}. Nothing in the database was removed; run it again once that is fixed.") from None
        erased[e.id] = result if result is not None else {"configured": False}
    try:
        erased["export"] = _erase_store(exports_store(tenant_id, as_stored=True))
        erased["key"] = {k: v for k, v in client_keys.destroy(tenant_id, actor).items() if k in ("provider", "destroyed", "documents", "summary")}
        erased["database"] = _erase_rows(tenant_id)
        with session_scope(platform=True) as s:
            s.delete(_client_row(s, tenant_id))
            rec = s.get(Offboarding, oid)
            rec.status, rec.completed_by, rec.completed_at, rec.erased, rec.remains, rec.error = "completed", actor, _now(), erased, remains, ""
    except Exception as ex:  # noqa: BLE001
        _save(oid, erased=erased, error=str(ex)[:300])
        raise
    remove(tenant_id)
    log.info("offboarded %s by %s", tenant_id, actor)
    return describe(oid)


def _save(oid: str, **values) -> None:
    with session_scope(platform=True) as s:
        rec = s.get(Offboarding, oid)
        for k, v in values.items():
            setattr(rec, k, v)


def _note(tenant_id: str, actor: str, kind: str, **detail) -> None:
    """In the client's own access record too, while it exists, so the client sees its own offboarding."""
    from ..domain.models import AccessEvent

    with session_scope(tenant_id) as s:
        s.add(AccessEvent(actor=actor, org="provider", kind=kind, subject="offboarding", detail=detail))


# ---- reading --------------------------------------------------------------------------------------------------------
def _view(r: Offboarding) -> dict:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": r.id, "tenant": r.tenant_id, "name": r.name, "status": r.status, "exported_by": r.exported_by,
            "exported_at": iso(r.exported_at), "export_bytes": r.export_bytes, "export_sha256": r.export_sha256, "counts": r.counts,
            "downloaded_by": r.downloaded_by, "downloaded_at": iso(r.downloaded_at), "completed_by": r.completed_by,
            "completed_at": iso(r.completed_at), "erased": r.erased, "remains": r.remains, "error": r.error}


def describe(oid: str) -> dict:
    with session_scope(platform=True) as s:
        r = s.get(Offboarding, oid)
        if r is None:
            raise OffboardingError("No such offboarding")
        return _view(r)


def listing(tenant_id: str | None = None) -> list[dict]:
    with session_scope(platform=True) as s:
        q = select(Offboarding).order_by(Offboarding.exported_at.desc())
        if tenant_id:
            q = q.where(Offboarding.tenant_id == tenant_id)
        return [_view(r) for r in s.scalars(q)]


def read_bundle(blob: bytes) -> tuple[dict, zipfile.ZipFile]:
    """Open a bundle and check every file against its manifest. For tests, and for anyone handed one."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    manifest = json.loads(z.read("manifest.json"))
    for path, f in manifest["files"].items():
        data = z.read(path)
        if hashlib.sha256(data).hexdigest() != f["sha256"] or len(data) != f["bytes"]:
            raise StorageError(f"{path} does not match the manifest")
    return manifest, z
