"""The history store contract.

`append` takes the rows and the reader's new positions on the bus and commits them as one change. That is what makes
the sink exact: after a crash it asks the table how far it got, and skips anything at or before that.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class LakeError(Exception):
    """The lake could not be used. The message is safe to show an operator."""


@dataclass(frozen=True)
class TableInfo:
    name: str
    rows: int
    commits: int
    last_commit: datetime | None
    location: str


class HistoryStore(Protocol):
    url: str  # for display: the catalog, never its credentials

    def append(self, tenant_id: str, table: str, rows: list[dict], marks: dict[str, int]) -> int:
        """Add rows and record the positions reached, in one commit. Returns the number of rows written."""
        ...

    def marks(self, tenant_id: str, table: str) -> dict[str, int]:
        """The positions recorded by the latest commit: 'topic/partition' -> next offset to read."""
        ...

    def scan(self, tenant_id: str, table: str, *, equals: dict | None = None, as_of: datetime | None = None, limit: int | None = None) -> list[dict]:
        """Rows of one client's table, optionally as the table stood at a past moment."""
        ...

    def tables(self, tenant_id: str) -> list[TableInfo]: ...
