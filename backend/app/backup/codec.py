"""Rows to JSON lines and back, typed by the table's own columns, and a digest of a table's content."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time

from sqlalchemy import Column, Date, DateTime, Integer, Table, Time

_MOD = 2**256


def surrogate(table: Table) -> str | None:
    """The table's own integer key, which the database assigns and nothing else refers to."""
    pk = list(table.primary_key.columns)
    return pk[0].name if len(pk) == 1 and isinstance(pk[0].type, Integer) else None


def _out(v):
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    return v


def encode(row: dict) -> str:
    return json.dumps({k: _out(v) for k, v in row.items()}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=True)


def decoder(table: Table):
    # Date and Time are checked before DateTime is relevant: neither is a subclass of the other in SQLAlchemy.
    parse = {c.name: (datetime.fromisoformat if isinstance(c.type, DateTime) else date.fromisoformat if isinstance(c.type, Date)
                      else time.fromisoformat) for c in table.columns if isinstance(c.type, (DateTime, Date, Time))}

    def decode(line: str) -> dict:
        row = json.loads(line)
        for k in parse.keys() & row.keys():
            if row[k] is not None:
                row[k] = parse[k](row[k])
        return row
    return decode


class Digest:
    """Order-independent: the sum of each row's hash. The surrogate key is left out, so a client restored elsewhere,
    whose rows the new database numbers afresh, still digests the same as the original."""

    def __init__(self, table: Table) -> None:
        self.skip = surrogate(table)
        self.total = 0
        self.rows = 0

    def add(self, row: dict) -> None:
        content = encode({k: v for k, v in row.items() if k != self.skip})
        self.total = (self.total + int.from_bytes(hashlib.sha256(content.encode()).digest(), "big")) % _MOD
        self.rows += 1

    def hex(self) -> str:
        return f"{self.rows}:{self.total:064x}"


def columns(table: Table) -> list[Column]:
    return list(table.columns)
