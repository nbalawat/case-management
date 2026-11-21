"""What a backup holds, and the rules for reading one back."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

FORMAT = 1


class BackupError(Exception):
    """The backup could not be made, read or restored. The message is safe to show an operator."""


@dataclass
class TablePart:
    key: str
    rows: int
    sha256: str


@dataclass
class TableCopy:
    rows: int = 0
    parts: list[TablePart] = field(default_factory=list)
    digest: str = ""  # of the rows themselves, in key order: what a drill compares


@dataclass
class Manifest:
    id: str
    created: str
    scope: str  # "platform", or a client id
    schema: str  # the migration revision the rows were read at
    app_version: str
    database: str  # the dialect it was read from
    tables: dict[str, TableCopy] = field(default_factory=dict)
    evidence: dict = field(default_factory=lambda: {"objects": 0, "bytes": 0, "parts": []})
    seconds: float = 0.0
    format: int = FORMAT

    @property
    def rows(self) -> int:
        return sum(t.rows for t in self.tables.values())

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), indent=1, sort_keys=True).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> "Manifest":
        try:
            doc = json.loads(raw)
            if doc.get("format") != FORMAT:
                raise BackupError(f"Backup format {doc.get('format')} is not one this version reads ({FORMAT})")
            tables = {n: TableCopy(t["rows"], [TablePart(**p) for p in t["parts"]], t.get("digest", "")) for n, t in doc.pop("tables").items()}
            return cls(**doc, tables=tables)
        except (ValueError, KeyError, TypeError) as e:
            raise BackupError(f"The manifest cannot be read: {e}") from None

    def summary(self) -> dict:
        return {"id": self.id, "created": self.created, "scope": self.scope, "schema": self.schema, "app_version": self.app_version,
                "database": self.database, "tables": len(self.tables), "rows": self.rows, "documents": self.evidence["objects"],
                "document_bytes": self.evidence["bytes"], "seconds": self.seconds}
