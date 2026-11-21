"""SQL over one client's lake tables.

The contract (`QueryEngine`) is what the rest of the platform uses: run one read-only SELECT against a client's tables,
optionally as they stood at a past moment, and get columns and rows back. The engine on a laptop and in the default
deployment is DuckDB, embedded in the process: the Iceberg catalog plans the scan (which snapshot, which data files)
and DuckDB reads the Parquet files directly, so nothing is loaded into Python first. A deployment with a shared query
service (Trino, Athena, Synapse serverless, BigQuery over BigLake) puts it behind the same contract.

Isolation is enforced by the engine, not by the SQL: each query gets a fresh DuckDB connection that knows only this
client's tables, may read only the directories those tables' files live in, cannot reach the network, and has its
configuration locked before the caller's SQL runs. Statements other than one SELECT are refused before execution.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import unquote, urlparse

from .base import LakeError
from .tables import TABLES

MAX_ROWS = 10_000
TIMEOUT_SECONDS = 30.0


class QueryError(LakeError):
    """The query could not run. The message is safe to show the person who wrote it."""


@dataclass
class QueryResult:
    columns: list[str]
    types: list[str]
    rows: list[list]
    truncated: bool
    seconds: float
    scanned: dict[str, dict] = field(default_factory=dict)  # table -> {"files": n, "rows": n, "snapshot": id}

    def as_dict(self) -> dict:
        return {"columns": self.columns, "types": self.types, "rows": self.rows, "truncated": self.truncated,
                "seconds": round(self.seconds, 3), "scanned": self.scanned}


class QueryEngine(Protocol):
    name: str

    def query(self, tenant_id: str, sql: str, *, as_of: datetime | None = None, max_rows: int = MAX_ROWS) -> QueryResult: ...

    def schema(self, tenant_id: str) -> list[dict]: ...


def _local_path(location: str) -> str | None:
    """A file location as a local path, or None for object storage."""
    u = urlparse(location)
    if u.scheme in ("", "file"):
        return unquote(u.path)
    return None


def read_only_select(con, sql: str) -> str:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise QueryError("Write a SELECT query")
    try:
        statements = con.extract_statements(sql)
    except Exception as e:  # noqa: BLE001  duckdb raises its own parser errors
        raise QueryError(f"Cannot read the query: {e}") from None
    import duckdb

    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise QueryError("Only one SELECT (or WITH ... SELECT) query is allowed")
    return sql


class DuckDbEngine:
    """DuckDB over the Iceberg tables a HistoryStore keeps. Local warehouses are read as Parquet files in place; tables on
    object storage are read through the catalog into Arrow (DuckDB's httpfs reader is a deployment option, not needed here)."""

    name = "duckdb"

    def __init__(self, store, *, memory_limit: str = "1GB", threads: int | None = None, timeout: float = TIMEOUT_SECONDS) -> None:
        self.store = store
        self.memory_limit = memory_limit
        self.threads = threads or max(1, min(4, os.cpu_count() or 1))
        self.timeout = timeout

    def _scan(self, tenant_id: str, name: str, as_of: datetime | None):
        """The files (or Arrow table) of one table at a moment, and what that scan covers."""
        table = self.store.table(tenant_id, name)
        if table is None:
            return None, {"files": 0, "rows": 0, "snapshot": None}
        snapshot_id = None
        if as_of is not None:
            at = int((as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)).timestamp() * 1000)
            before = [h for h in table.history() if h.timestamp_ms <= at]
            if not before:
                return None, {"files": 0, "rows": 0, "snapshot": None}
            snapshot_id = before[-1].snapshot_id
        elif table.current_snapshot() is None:
            return None, {"files": 0, "rows": 0, "snapshot": None}
        scan = table.scan(snapshot_id=snapshot_id)
        tasks = list(scan.plan_files())
        info = {"files": len(tasks), "rows": sum(t.file.record_count for t in tasks), "snapshot": snapshot_id or table.current_snapshot().snapshot_id}
        if any(t.delete_files for t in tasks):  # row-level deletes: let the catalog apply them
            return ("arrow", scan.to_arrow()), info
        paths = [_local_path(t.file.file_path) for t in tasks]
        if tasks and all(paths):
            return ("files", paths), info
        return ("arrow", scan.to_arrow()), info

    def _connect(self, tenant_id: str, as_of: datetime | None):
        import duckdb

        con = duckdb.connect(":memory:", config={"memory_limit": self.memory_limit, "threads": self.threads})
        scanned, dirs = {}, set()
        for name, spec in TABLES.items():
            source, info = self._scan(tenant_id, name, as_of)
            scanned[name] = info
            if source is None:
                cols = ", ".join(f"NULL::{_duck(kind)} AS {col}" for col, kind, _ in spec.columns)
                con.execute(f"CREATE VIEW {name} AS SELECT {cols} WHERE false")
            elif source[0] == "files":
                files = source[1]
                dirs |= {os.path.dirname(p) for p in files}
                listed = ", ".join("'" + p.replace("'", "''") + "'" for p in files)
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet([{listed}], union_by_name=true)")
            else:
                con.register(f"_{name}_arrow", source[1])
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM _{name}_arrow")
        # From here on the connection can read only this client's table files and nothing else, and cannot be reconfigured.
        if dirs:
            con.execute("SET allowed_directories = [" + ", ".join("'" + d.replace("'", "''") + "/'" for d in sorted(dirs)) + "]")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        return con, scanned

    def query(self, tenant_id: str, sql: str, *, as_of: datetime | None = None, max_rows: int = MAX_ROWS) -> QueryResult:
        import duckdb

        con, scanned = self._connect(tenant_id, as_of)
        try:
            sql = read_only_select(con, sql)
            timer = threading.Timer(self.timeout, con.interrupt)
            started = time.perf_counter()
            timer.start()
            try:
                cur = con.execute(sql)
                rows = cur.fetchmany(max_rows + 1)
            except duckdb.InterruptException:
                raise QueryError(f"The query ran longer than {self.timeout:g} seconds and was stopped") from None
            except duckdb.Error as e:
                raise QueryError(str(e).split("\n\n")[0]) from None
            finally:
                timer.cancel()
            seconds = time.perf_counter() - started
            columns = [d[0] for d in cur.description]
            types = [str(d[1]) for d in cur.description]
            return QueryResult(columns, types, [[_plain(v) for v in r] for r in rows[:max_rows]], len(rows) > max_rows, seconds, scanned)
        finally:
            con.close()

    def stream(self, tenant_id: str, sql: str, *, batch: int = 50_000, as_of: datetime | None = None):
        """Rows of a large result in batches, for platform code (backtests, lookbacks) rather than people."""
        con, _ = self._connect(tenant_id, as_of)
        try:
            cur = con.execute(read_only_select(con, sql))
            columns = [d[0] for d in cur.description]
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    break
                yield [dict(zip(columns, r)) for r in rows]
        finally:
            con.close()

    def schema(self, tenant_id: str) -> list[dict]:
        out = []
        info = {t.name: t for t in self.store.tables(tenant_id)}
        for name, spec in TABLES.items():
            t = info.get(name)
            out.append({"table": name, "rows": t.rows if t else 0, "commits": t.commits if t else 0,
                        "last_commit": t.last_commit.isoformat() if t and t.last_commit else None,
                        "partitioned_by": f"day({spec.time_column})",
                        "columns": [{"name": c, "type": _duck(k), "required": r} for c, k, r in spec.columns]})
        return out


def _duck(kind: str) -> str:
    return {"string": "VARCHAR", "timestamp": "TIMESTAMP", "double": "DOUBLE"}[kind]


def _plain(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


_engine: DuckDbEngine | None = None
_for = None


def engine() -> DuckDbEngine | None:
    """The query engine over this deployment's lake, or None when it has no lake."""
    from .iceberg import connect

    global _engine, _for
    store = connect()
    if store is None:
        return None
    if _engine is None or _for is not store:
        _engine, _for = DuckDbEngine(store), store
    return _engine
