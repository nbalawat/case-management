"""Copy a client's transactions from the operational store into the lake.

The lake fills itself from the bus, so transactions loaded from files before the lake existed (or by a pipeline that
does not publish) are only in the operational store. A backfill appends those, in batches, skipping any id the lake
already holds, so it can be run again safely and never duplicates what the stream wrote.

    python -m app.lake.backfill meridian [--batch 50000]
"""
from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Transaction
from ..visibility import system_work
from .base import HistoryStore
from .query import DuckDbEngine

TABLE = "transactions"


def _row(t: Transaction) -> dict:
    return {"txn_id": t.id, "account_id": t.account_id, "party_id": t.party_id, "ts": t.ts, "amount": float(t.amount), "currency": t.currency or "USD",
            "direction": t.direction, "channel": t.channel, "counterparty": t.counterparty or "", "counterparty_country": t.counterparty_country or ""}


@system_work
def backfill(s: Session, store: HistoryStore, tenant_id: str, batch: int = 50_000) -> dict:
    started = time.perf_counter()
    have: set[str] = set()
    for rows in DuckDbEngine(store).stream(tenant_id, f"SELECT txn_id FROM {TABLE}"):
        have |= {r["txn_id"] for r in rows}
    written = skipped = 0
    pending: list[dict] = []
    for t in s.scalars(select(Transaction).order_by(Transaction.ts).execution_options(yield_per=batch)):
        if t.id in have:
            skipped += 1
            continue
        pending.append(_row(t))
        if len(pending) >= batch:
            written += store.append(tenant_id, TABLE, pending, {})
            pending = []
    if pending:
        written += store.append(tenant_id, TABLE, pending, {})
    return {"tenant": tenant_id, "written": written, "already_there": skipped, "seconds": round(time.perf_counter() - started, 2)}


def main(argv: list[str]) -> int:
    from ..db import session_scope
    from .iceberg import connect

    p = argparse.ArgumentParser(prog="python -m app.lake.backfill", description=__doc__.splitlines()[0])
    p.add_argument("tenant")
    p.add_argument("--batch", type=int, default=50_000)
    a = p.parse_args(argv)
    store = connect()
    if store is None:
        print("LAKE_CATALOG is not set, so there is no lake to fill.", file=sys.stderr)
        return 1
    with session_scope(a.tenant) as s:
        print(backfill(s, store, a.tenant, a.batch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
