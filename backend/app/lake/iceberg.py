"""The history store on Apache Iceberg.

The catalog is configuration (LAKE_CATALOG, a JSON object of catalog properties): a SQL catalog and a local
warehouse on a laptop, a REST catalog such as Polaris or Nessie over object storage in a deployment. Each client has
its own namespace, so a catalog can grant, encrypt and place each client's tables separately.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .base import LakeError, TableInfo
from .tables import DOUBLE, STRING, TABLES, TIMESTAMP, LakeTable

try:
    import pyarrow as pa
    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NoSuchNamespaceError, NoSuchTableError
    from pyiceberg.expressions import AlwaysTrue, And, EqualTo
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import Schema
    from pyiceberg.transforms import DayTransform
    from pyiceberg.types import DoubleType, NestedField, StringType, TimestampType
except ImportError:  # pragma: no cover - only without the optional dependency
    load_catalog = None

MARKS = "caseforge.marks"


def _schema(t: LakeTable) -> "Schema":
    kinds = {STRING: StringType, TIMESTAMP: TimestampType, DOUBLE: DoubleType}
    return Schema(*(NestedField(i, name, kinds[kind](), required=required) for i, (name, kind, required) in enumerate(t.columns, start=1)))


class IcebergStore:
    def __init__(self, properties: dict, namespace_prefix: str = "caseforge") -> None:
        if load_catalog is None:
            raise LakeError("Iceberg support is not installed. Install the 'lake' dependency group (uv sync --group lake).")
        if not isinstance(properties, dict) or not properties.get("type"):
            raise LakeError('LAKE_CATALOG must be a JSON object naming the catalog, for example {"type": "rest", "uri": "https://..."}')
        self._prefix = namespace_prefix
        self.url = f"{properties['type']} catalog"  # where it lives is the operator's business, and may carry credentials
        try:
            self._catalog = load_catalog("caseforge", **{k: str(v) for k, v in properties.items()})
        except Exception as e:  # noqa: BLE001  every catalog kind fails in its own way
            raise LakeError(f"The lake catalog cannot be opened: {e}") from None
        self._open: dict[tuple[str, str], object] = {}

    def namespace(self, tenant_id: str) -> str:
        return f"{self._prefix}_{tenant_id.replace('-', '_')}"

    def _table(self, tenant_id: str, name: str, create: bool = False):
        if name not in TABLES:
            raise LakeError(f"Unknown lake table '{name}'")
        key = (tenant_id, name)
        if key in self._open:
            return self._open[key]
        ident = f"{self.namespace(tenant_id)}.{name}"
        try:
            table = self._catalog.load_table(ident)
        except (NoSuchTableError, NoSuchNamespaceError):
            if not create:
                return None
            spec = TABLES[name]
            schema = _schema(spec)
            day = PartitionSpec(PartitionField(source_id=schema.find_field(spec.time_column).field_id, field_id=1000, transform=DayTransform(), name="day"))
            self._catalog.create_namespace_if_not_exists(self.namespace(tenant_id))
            table = self._catalog.create_table_if_not_exists(ident, schema=schema, partition_spec=day)
        self._open[key] = table
        return table

    def append(self, tenant_id: str, table: str, rows: list[dict], marks: dict[str, int]) -> int:
        if not rows:
            return 0
        t = self._table(tenant_id, table, create=True)
        t.refresh()
        merged = self._marks_of(t) | marks  # positions for partitions this batch did not touch carry forward
        t.append(pa.Table.from_pylist(rows, schema=t.schema().as_arrow()), snapshot_properties={MARKS: json.dumps(merged, sort_keys=True)})
        return len(rows)

    @staticmethod
    def _marks_of(t) -> dict[str, int]:
        snap = t.current_snapshot()
        raw = snap.summary.additional_properties.get(MARKS) if snap is not None and snap.summary is not None else None
        return json.loads(raw) if raw else {}

    def marks(self, tenant_id: str, table: str) -> dict[str, int]:
        t = self._table(tenant_id, table)
        if t is None:
            return {}
        t.refresh()
        return self._marks_of(t)

    def scan(self, tenant_id: str, table: str, *, equals: dict | None = None, as_of: datetime | None = None, limit: int | None = None) -> list[dict]:
        t = self._table(tenant_id, table)
        if t is None:
            return []
        t.refresh()
        snapshot_id = None
        if as_of is not None:
            at = int((as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)).timestamp() * 1000)
            before = [h for h in t.history() if h.timestamp_ms <= at]
            if not before:
                return []  # the table did not exist yet
            snapshot_id = before[-1].snapshot_id
        elif t.current_snapshot() is None:
            return []
        where = AlwaysTrue()
        for column, value in (equals or {}).items():
            where = And(where, EqualTo(column, value))
        return t.scan(row_filter=where, snapshot_id=snapshot_id, limit=limit).to_arrow().to_pylist()

    def tables(self, tenant_id: str) -> list[TableInfo]:
        out = []
        for name in TABLES:
            t = self._table(tenant_id, name)
            if t is None:
                continue
            t.refresh()
            snap = t.current_snapshot()
            total = int(snap.summary.additional_properties.get("total-records", 0)) if snap is not None and snap.summary is not None else 0
            out.append(TableInfo(name, total, len(t.history()), datetime.fromtimestamp(snap.timestamp_ms / 1000, timezone.utc).replace(tzinfo=None) if snap else None, t.location()))
        return out


_store: IcebergStore | None = None
_for: str | None = None


def connect(config: str | None = None) -> IcebergStore | None:
    """The lake this deployment is configured for, or None when it has none."""
    from ..config import settings

    global _store, _for
    config = settings.lake_catalog if config is None else config
    if not config:
        return None
    if _store is None or _for != config:
        try:
            properties = json.loads(config)
        except ValueError as e:
            raise LakeError(f"LAKE_CATALOG is not valid JSON: {e}") from None
        _store, _for = IcebergStore(properties, settings.event_topic_prefix), config
    return _store
