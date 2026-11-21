"""Transactions, newest first, filterable by customer, account, channel, direction, amount and time."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ...domain.models import Transaction
from ..conventions import ApiError, Page, decode_cursor, limit_of, page
from ..guard import need, session
from ..principal import Principal
from . import schemas as sc

router = APIRouter(prefix="/transactions", tags=["Transactions"])


def txn_out(t: Transaction) -> dict:
    return {"id": t.id, "account_id": t.account_id, "party_id": t.party_id, "ts": t.ts, "amount": t.amount, "currency": t.currency,
            "direction": t.direction, "channel": t.channel, "counterparty": t.counterparty, "counterparty_country": t.counterparty_country}


def query(s: Session, *, party_id=None, account_id=None, channel=None, direction=None, min_amount=None, max_amount=None,
          after_ts=None, before_ts=None, limit=100, cursor=None) -> dict:
    n = limit_of(limit, 1000)
    q = select(Transaction)
    for col, val in ((Transaction.party_id, party_id), (Transaction.account_id, account_id), (Transaction.channel, channel),
                     (Transaction.direction, direction)):
        if val is not None:
            q = q.where(col == val)
    if min_amount is not None:
        q = q.where(Transaction.amount >= min_amount)
    if max_amount is not None:
        q = q.where(Transaction.amount <= max_amount)
    if after_ts:
        q = q.where(Transaction.ts >= after_ts.replace(tzinfo=None))
    if before_ts:
        q = q.where(Transaction.ts < before_ts.replace(tzinfo=None))
    at = decode_cursor(cursor, (datetime, str))
    if at:
        q = q.where(or_(Transaction.ts < at[0], and_(Transaction.ts == at[0], Transaction.id < at[1])))
    rows = list(s.scalars(q.order_by(Transaction.ts.desc(), Transaction.id.desc()).limit(n + 1)))
    out = page(rows, n, lambda t: (t.ts, t.id))
    return {"data": [txn_out(t) for t in out["rows"]], "next_cursor": out["next_cursor"], "has_more": out["has_more"]}


@router.get("", response_model=Page[sc.TransactionOut], summary="List transactions")
def list_transactions(party_id: str | None = Query(None, max_length=40), account_id: str | None = Query(None, max_length=40),
                      channel: str | None = Query(None, max_length=20), direction: Literal["in", "out"] | None = None,
                      min_amount: float | None = None, max_amount: float | None = None, after: datetime | None = None,
                      before: datetime | None = None, limit: int = 100, cursor: str | None = None,
                      p: Principal = Depends(need("transactions:read")), s: Session = Depends(session)):
    return query(s, party_id=party_id, account_id=account_id, channel=channel, direction=direction, min_amount=min_amount,
                 max_amount=max_amount, after_ts=after, before_ts=before, limit=limit, cursor=cursor)


@router.get("/{txn_id}", response_model=sc.TransactionOut, summary="Read a transaction")
def read_transaction(txn_id: str, p: Principal = Depends(need("transactions:read")), s: Session = Depends(session)):
    t = s.get(Transaction, {"tenant_id": p.tenant_id, "id": txn_id})
    if t is None:
        raise ApiError(404, "No transaction with that id", "transaction_not_found")
    return txn_out(t)


@router.get("/{txn_id}/lineage", summary="Where a transaction came from")
def transaction_lineage(txn_id: str, p: Principal = Depends(need("transactions:read")), s: Session = Depends(session)) -> dict:
    """The object in storage, the source, the pipeline run and the mapping version that loaded it."""
    from ...ingest import pipeline

    t = s.get(Transaction, {"tenant_id": p.tenant_id, "id": txn_id})
    if t is None:
        raise ApiError(404, "No transaction with that id", "transaction_not_found")
    return {"transaction": t.id, "origin": pipeline.lineage(s, t.feed_id) or {"object": None, "note": f"Loaded outside a pipeline ({t.feed_id})"}}
