"""Making, checking and restoring backups, and the drill that proves a restore works."""
from __future__ import annotations

import gzip
import hashlib
import io
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.sql.schema import Table

from ..config import settings
from ..storage import ObjectStore, StorageError, open_store
from .codec import Digest, decoder, encode, surrogate
from .manifest import BackupError, Manifest, TableCopy, TablePart

log = logging.getLogger("caseforge.backup")
PART_ROWS = 50_000
SKIP = {"alembic_version"}


def _tables() -> list[Table]:
    from ..db import Base
    from ..domain import models  # noqa: F401  (register every table)

    return [t for t in Base.metadata.sorted_tables if t.name not in SKIP]


def _schema(engine: Engine) -> str:
    from .. import migrations

    with engine.connect() as conn:
        return migrations.current(conn) or ""


def _app_version() -> str:
    try:
        from ..main import app

        return app.version
    except Exception:  # noqa: BLE001
        return "unknown"


def target(url: str | None = None) -> ObjectStore:
    url = url or settings.backup_url
    if not url:
        raise BackupError("No backup location: set BACKUP_URL (a directory on a laptop, a bucket or container in a deployment)")
    try:
        if url.startswith("file://"):
            Path(url.removeprefix("file://")).mkdir(parents=True, exist_ok=True)
        return open_store(url)
    except StorageError as e:
        raise BackupError(str(e)) from None


def _scoped(table: Table, scope: str):
    """The rows of one client, or every row."""
    q = select(table)
    if scope == "platform":
        return q
    if table.name == "tenant":
        return q.where(table.c.id == scope)
    if "tenant_id" in table.c:
        return q.where(table.c.tenant_id == scope)
    return None  # a table that belongs to no client is not part of a client's backup


def _ordered(table: Table, q):
    return q.order_by(*table.primary_key.columns)


def _read_all(engine: Engine, scope: str, store: ObjectStore, prefix: str) -> dict[str, TableCopy]:
    """Every table, read inside one transaction so the backup is one moment in time."""
    out: dict[str, TableCopy] = {}
    opts = {"isolation_level": "REPEATABLE READ"} if engine.dialect.name == "postgresql" else {}
    with engine.connect().execution_options(**opts) as conn, conn.begin():
        if engine.dialect.name == "sqlite":
            conn.execute(text("SELECT 1"))  # opens the read transaction before the first table
        for table in _tables():
            q = _scoped(table, scope)
            if q is None:
                continue
            copy, digest, n, buf, part = TableCopy(), Digest(table), 0, io.BytesIO(), 0
            gz = gzip.GzipFile(fileobj=buf, mode="wb", mtime=0)

            def flush():
                nonlocal buf, gz, part, n
                gz.close()
                data = buf.getvalue()
                key = f"{prefix}/db/{table.name}/part-{part:05d}.jsonl.gz"
                store.write(key, data)
                copy.parts.append(TablePart(key, n, hashlib.sha256(data).hexdigest()))
                part, n = part + 1, 0
                buf = io.BytesIO()
                gz = gzip.GzipFile(fileobj=buf, mode="wb", mtime=0)

            for row in conn.execute(_ordered(table, q)).mappings():
                gz.write(encode(dict(row)).encode() + b"\n")
                digest.add(dict(row))
                n += 1
                copy.rows += 1
                if n >= PART_ROWS:
                    flush()
            if n or not copy.parts:
                flush()
            copy.digest = digest.hex()
            out[table.name] = copy
    return out


def _evidence(scope: str) -> ObjectStore | None:
    try:
        return open_store(settings.evidence_url)
    except StorageError:
        return None


def create(*, scope: str = "platform", store: ObjectStore | None = None, engine: Engine | None = None,
           evidence: ObjectStore | None = None, with_documents: bool = True) -> Manifest:
    from ..db import engine as default_engine

    engine = engine or default_engine
    store = store or target()
    if scope != "platform":
        with engine.connect() as conn:
            from ..domain.models import Tenant

            if conn.execute(select(Tenant.__table__.c.id).where(Tenant.__table__.c.id == scope)).first() is None:
                raise BackupError(f"No client '{scope}' to back up")
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    bid = f"{now:%Y%m%dT%H%M%SZ}-{scope}"
    if store.exists(f"{bid}/manifest.json"):
        raise BackupError(f"A backup called {bid} already exists")
    m = Manifest(id=bid, created=now.isoformat(timespec="seconds"), scope=scope, schema=_schema(engine), app_version=_app_version(),
                 database=engine.dialect.name)
    m.tables = _read_all(engine, scope, store, bid)
    docs = evidence if evidence is not None else (_evidence(scope) if with_documents else None)
    if docs is not None:
        for obj in docs.list("" if scope == "platform" else f"{scope}/"):
            with docs.open(obj.key) as f:
                data = f.read()
            store.write(f"{bid}/evidence/{obj.key}", data)
            m.evidence["objects"] += 1
            m.evidence["bytes"] += len(data)
            m.evidence["parts"].append({"key": obj.key, "sha256": hashlib.sha256(data).hexdigest()})
    m.seconds = round(time.perf_counter() - t0, 3)
    store.write(f"{bid}/manifest.json", m.to_json())  # last: until it exists, the backup does not
    log.info("Backup %s: %d rows in %d tables, %d documents, %.1f s", bid, m.rows, len(m.tables), m.evidence["objects"], m.seconds)
    return m


def list_backups(store: ObjectStore | None = None) -> list[Manifest]:
    store = store or target()
    out = []
    for obj in store.list():
        if obj.key.count("/") == 1 and obj.key.endswith("/manifest.json"):
            with store.open(obj.key) as f:
                try:
                    out.append(Manifest.from_json(f.read()))
                except BackupError as e:
                    log.warning("Skipped %s: %s", obj.key, e)
    return sorted(out, key=lambda m: m.created, reverse=True)


def load(store: ObjectStore, backup_id: str) -> Manifest:
    key = f"{backup_id}/manifest.json"
    if "/" in backup_id or not store.exists(key):
        raise BackupError(f"No complete backup '{backup_id}' (it has no manifest)")
    with store.open(key) as f:
        return Manifest.from_json(f.read())


def _read(store: ObjectStore, key: str, want: str) -> bytes:
    with store.open(key) as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != want:
        raise BackupError(f"{key} does not match its checksum: the backup is damaged")
    return data


def verify(backup_id: str, store: ObjectStore | None = None) -> dict:
    """Read every file back and check it against the manifest. Changes nothing."""
    store = store or target()
    m = load(store, backup_id)
    rows = 0
    for name, copy in m.tables.items():
        for p in copy.parts:
            n = sum(1 for _ in gzip.decompress(_read(store, p.key, p.sha256)).splitlines())
            if n != p.rows:
                raise BackupError(f"{p.key} holds {n} rows, the manifest says {p.rows}")
            rows += n
    for d in m.evidence["parts"]:
        _read(store, f"{m.id}/evidence/{d['key']}", d["sha256"])
    return {"id": m.id, "ok": True, "rows": rows, "documents": len(m.evidence["parts"])}


def _check_target(engine: Engine, m: Manifest) -> None:
    from ..domain.models import Tenant

    have = _schema(engine)
    if have != m.schema:
        raise BackupError(f"The target database is at schema {have or 'none'}; this backup was taken at {m.schema}. "
                          "Restore with the application version that matches, or migrate the target to that revision first.")
    with engine.connect() as conn:
        if m.scope == "platform":
            busy = [t.name for t in _tables() if conn.execute(select(func.count()).select_from(t)).scalar()]
            if busy:
                raise BackupError(f"The target database already holds data ({', '.join(busy[:5])}{'...' if len(busy) > 5 else ''}). "
                                  "A whole-platform restore goes into an empty database; nothing is overwritten.")
        elif conn.execute(select(Tenant.__table__.c.id).where(Tenant.__table__.c.id == m.scope)).first() is not None:
            raise BackupError(f"Client '{m.scope}' already exists in the target. Restoring never overwrites a client.")


def restore(backup_id: str, *, engine: Engine, store: ObjectStore | None = None, evidence: ObjectStore | None = None) -> dict:
    """Load a verified backup into a database that does not hold its data yet. Whole platform: ids kept as they were.
    One client: the database numbers that client's rows afresh, since another database may already use those numbers."""
    store = store or target()
    m = load(store, backup_id)
    verify(backup_id, store)
    _check_target(engine, m)
    t0 = time.perf_counter()
    by_name = {t.name: t for t in _tables()}
    restored: dict[str, int] = {}
    unknown = sorted(set(m.tables) - set(by_name))
    if unknown:
        raise BackupError(f"This version has no table {', '.join(unknown)} that the backup holds")
    with engine.begin() as conn:
        for table in by_name.values():  # parents before children
            name, copy = table.name, m.tables.get(table.name)
            if copy is None:
                continue
            decode, skip = decoder(table), (surrogate(table) if m.scope != "platform" else None)
            n = 0
            for p in copy.parts:
                batch = []
                for line in gzip.decompress(_read(store, p.key, p.sha256)).decode().splitlines():
                    row = decode(line)
                    if skip:
                        row.pop(skip, None)
                    batch.append(row)
                    if len(batch) >= 5_000:
                        conn.execute(insert(table), batch)
                        n += len(batch)
                        batch = []
                if batch:
                    conn.execute(insert(table), batch)
                    n += len(batch)
            restored[name] = n
        if engine.dialect.name == "postgresql":  # carry on numbering after the restored rows
            for table in by_name.values():
                key = surrogate(table)
                if key and restored.get(table.name):
                    conn.execute(text(f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', '{key}'), "
                                      f"COALESCE((SELECT MAX(\"{key}\") FROM \"{table.name}\"), 1))"))
    docs = 0
    if evidence is not None:
        for d in m.evidence["parts"]:
            if evidence.exists(d["key"]):
                continue  # never overwrite a document
            evidence.write(d["key"], _read(store, f"{m.id}/evidence/{d['key']}", d["sha256"]))
            docs += 1
    return {"id": m.id, "scope": m.scope, "rows": sum(restored.values()), "tables": len(restored), "documents": docs,
            "seconds": round(time.perf_counter() - t0, 3)}


def digests(engine: Engine, scope: str = "platform") -> dict[str, str]:
    """Each table's content digest as the database holds it now."""
    out = {}
    with engine.connect() as conn:
        for table in _tables():
            q = _scoped(table, scope)
            if q is None:
                continue
            d = Digest(table)
            for row in conn.execute(q).mappings():
                d.add(dict(row))
            out[table.name] = d.hex()
    return out


def drill(*, store: ObjectStore | None = None, engine: Engine | None = None, scratch_url: str | None = None) -> dict:
    """Back up, restore into a scratch database, and compare every table. The proof a restore works, with timings."""
    import tempfile

    from .. import migrations
    from ..db import engine as default_engine
    from ..db import make_engine

    engine = engine or default_engine
    store = store or target()
    started = datetime.now(timezone.utc)
    m = create(store=store, engine=engine, with_documents=True)
    t_verify = time.perf_counter()
    verify(m.id, store)
    verify_s = time.perf_counter() - t_verify
    tmp = None
    if not scratch_url:
        tmp = tempfile.TemporaryDirectory(prefix="caseforge-drill-")
        scratch_url = f"sqlite:///{Path(tmp.name) / 'drill.db'}"
    scratch = make_engine(scratch_url)
    try:
        migrations.upgrade(scratch, m.schema or "head")
        r = restore(m.id, engine=scratch, store=store)
        source = {n: c.digest for n, c in m.tables.items()}
        restored = digests(scratch)
        mismatched = sorted(n for n in source if source[n] != restored.get(n))
    finally:
        scratch.dispose()
        if tmp is not None:
            tmp.cleanup()
    report = {"backup": m.id, "started": started.isoformat(timespec="seconds"), "ok": not mismatched, "tables": len(source),
              "rows": m.rows, "documents": m.evidence["objects"], "mismatched": mismatched,
              "backup_seconds": m.seconds, "verify_seconds": round(verify_s, 3), "restore_seconds": r["seconds"],
              "into": scratch_url.split(":", 1)[0].split("+")[0], "source": engine.dialect.name}
    import json

    store.write(f"drills/{started:%Y%m%dT%H%M%SZ}.json", json.dumps(report, indent=1).encode())
    log.info("Drill %s: %s, %d rows, backup %.1f s, restore %.1f s", m.id, "ok" if report["ok"] else f"MISMATCH {mismatched}",
             m.rows, m.seconds, r["seconds"])
    return report


def drills(store: ObjectStore | None = None, limit: int = 10) -> list[dict]:
    import json

    store = store or target()
    keys = sorted((o.key for o in store.list("drills/")), reverse=True)[:limit]
    out = []
    for k in keys:
        with store.open(k) as f:
            out.append(json.loads(f.read()))
    return out


__all__ = ["create", "list_backups", "verify", "restore", "drill", "drills", "digests", "target", "BackupError"]
