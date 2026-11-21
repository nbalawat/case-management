"""Where backtests and lookbacks read past transactions from.

  operational   the case database (the default, and the only choice without a lake)
  lake          the client's Iceberg transactions table through the query engine, so a year of history is scanned from
                Parquet files instead of the database that serves casework

DETECTION_HISTORY chooses; `lake` needs LAKE_CATALOG and falls back to the operational store when the lake has no
transactions for the client yet (run `python -m app.lake.backfill <client>` once to copy them across).
"""
from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Transaction
from ..tenancy import tenant_of

FIELDS = ("ts", "amount", "direction", "channel", "counterparty", "counterparty_country")


def source() -> str:
    return os.getenv("DETECTION_HISTORY", "operational")


def _operational(s: Session, since, until) -> Iterator[tuple[str, list]]:
    q = select(Transaction).order_by(Transaction.party_id, Transaction.ts)
    if since is not None:
        q = q.where(Transaction.ts >= since)
    if until is not None:
        q = q.where(Transaction.ts <= until)
    by_party: dict[str, list] = defaultdict(list)
    for t in s.scalars(q):
        by_party[t.party_id].append(t)
    yield from by_party.items()


def _sql_time(t: datetime) -> str:
    return t.replace(tzinfo=None).isoformat(sep=" ")


def _lake(tenant_id: str, since, until) -> Iterator[tuple[str, list]] | None:
    from ..lake.query import engine

    eng = engine()
    if eng is None:
        return None
    where = []
    if since is not None:
        where.append(f"ts >= TIMESTAMP '{_sql_time(since)}'")
    if until is not None:
        where.append(f"ts <= TIMESTAMP '{_sql_time(until)}'")
    sql = (f"SELECT txn_id, party_id, {', '.join(FIELDS)} FROM transactions" + (f" WHERE {' AND '.join(where)}" if where else "")
           + " ORDER BY party_id, ts")
    batches = eng.stream(tenant_id, sql)
    first = next(batches, None)
    if first is None:
        return None

    def walk():
        current, txns = None, []
        for rows in [first, *batches]:
            for r in rows:
                if r["party_id"] != current:
                    if current is not None:
                        yield current, txns
                    current, txns = r["party_id"], []
                d = {k: r[k] for k in FIELDS}
                d["counterparty_country"] = d["counterparty_country"] or "US"
                txns.append(SimpleNamespace(id=r["txn_id"], party_id=r["party_id"], **d))
        if current is not None:
            yield current, txns

    return walk()


def by_party(s: Session, since: datetime | None = None, until: datetime | None = None, party_ids: set[str] | None = None) -> Iterator[tuple[str, list]]:
    """Each customer's transactions in the period, oldest first."""
    stream = _lake(tenant_of(s), since, until) if source() == "lake" else None
    for pid, txns in stream if stream is not None else _operational(s, since, until):
        if party_ids is None or pid in party_ids:
            yield pid, txns
