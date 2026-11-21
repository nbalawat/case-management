"""Customers: search, profile, accounts, KYC, transactions, cases and network."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ...api.deps import case_row
from ...domain.models import Account, Case, Party
from ...entities import graph, profile, search
from ..conventions import ApiError, Page, decode_cursor, limit_of, page
from ..guard import need, session
from ..principal import Principal
from . import schemas as sc
from .transactions import query as txn_query

router = APIRouter(prefix="/customers", tags=["Customers"])


def customer_out(c: Party) -> dict:
    return {"id": c.id, "kind": c.kind, "name": c.name, "country": c.country, "occupation": c.occupation, "risk_rating": c.risk_rating,
            "pep": c.pep, "onboarded": c.onboarded}


def get_party(s: Session, party_id: str) -> Party:
    c = s.scalars(select(Party).where(Party.id == party_id)).first()
    if c is None:
        raise ApiError(404, "No customer with that id", "customer_not_found")
    return c


@router.get("", response_model=Page[sc.Customer], summary="List customers")
def list_customers(risk_rating: Literal["low", "medium", "high"] | None = None, pep: bool | None = None,
                   country: str | None = Query(None, min_length=2, max_length=2), kind: str | None = Query(None, max_length=20),
                   name: str | None = Query(None, min_length=2, max_length=120, description="Case-insensitive part of the name"),
                   limit: int = 50, cursor: str | None = None, p: Principal = Depends(need("customers:read")), s: Session = Depends(session)):
    n = limit_of(limit)
    q = select(Party)
    for col, val in ((Party.risk_rating, risk_rating), (Party.pep, pep), (Party.country, country.upper() if country else None), (Party.kind, kind)):
        if val is not None:
            q = q.where(col == val)
    if name:
        q = q.where(func.lower(Party.name).contains(name.lower()))
    at = decode_cursor(cursor, (datetime, str))
    if at:
        q = q.where(or_(Party.onboarded < at[0], and_(Party.onboarded == at[0], Party.id < at[1])))
    rows = list(s.scalars(q.order_by(Party.onboarded.desc(), Party.id.desc()).limit(n + 1)))
    out = page(rows, n, lambda c: (c.onboarded, c.id))
    return {"data": [customer_out(c) for c in out["rows"]], "next_cursor": out["next_cursor"], "has_more": out["has_more"]}


@router.get("/search", summary="Search customers and counterparties by name")
def search_names(q: str = Query(min_length=2, max_length=120), limit: int = 10,
                 p: Principal = Depends(need("customers:read")), s: Session = Depends(session)) -> dict:
    """Fuzzy, spelling-tolerant name search (OpenSearch when configured, else the database)."""
    return {"results": search.search(s, q, limit_of(limit, 25))}


@router.get("/{party_id}", response_model=sc.Customer, summary="Read a customer")
def read_customer(party_id: str, p: Principal = Depends(need("customers:read")), s: Session = Depends(session)):
    return customer_out(get_party(s, party_id))


@router.get("/{party_id}/profile", summary="The customer 360: activity, counterparties, network, media and cases")
def customer_profile(party_id: str, p: Principal = Depends(need("customers:read")), s: Session = Depends(session)) -> dict:
    get_party(s, party_id)
    out = profile.customer(s, party_id)
    if not p.scopes >= {"cases:read"}:  # the cases in a profile are only for callers who may read cases
        out = out | {"cases": []}
    return out


@router.get("/{party_id}/accounts", response_model=list[sc.Account], summary="A customer's accounts")
def customer_accounts(party_id: str, p: Principal = Depends(need("customers:read")), s: Session = Depends(session)):
    get_party(s, party_id)
    return [{"id": a.id, "product": a.product, "status": a.status} for a in s.scalars(select(Account).where(Account.party_id == party_id).order_by(Account.id))]


@router.get("/{party_id}/kyc", summary="KYC: risk rating and its history, ownership, documents and review triggers")
def customer_kyc(party_id: str, p: Principal = Depends(need("customers:read")), s: Session = Depends(session)) -> dict:
    from ...kyc import evidence, ownership, risk, triggers

    get_party(s, party_id)
    return {"risk": risk.view(s, party_id, p.persona), "ownership": ownership.view(s, party_id),
            "evidence": evidence.for_party(s, party_id, p.persona), "triggers": triggers.for_party(s, party_id)}


@router.get("/{party_id}/transactions", response_model=Page[sc.TransactionOut], summary="A customer's transactions")
def customer_transactions(party_id: str, after: datetime | None = None, before: datetime | None = None, limit: int = 100,
                          cursor: str | None = None, p: Principal = Depends(need("transactions:read")), s: Session = Depends(session)):
    get_party(s, party_id)
    return txn_query(s, party_id=party_id, after_ts=after, before_ts=before, limit=limit, cursor=cursor)


@router.get("/{party_id}/cases", response_model=list[sc.CaseSummary], summary="A customer's cases the caller can see")
def customer_cases(party_id: str, p: Principal = Depends(need("cases:read")), s: Session = Depends(session)):
    get_party(s, party_id)
    return [case_row(c) for c in s.scalars(select(Case).where(Case.party_id == party_id).order_by(Case.created.desc()))]


@router.get("/{party_id}/network", summary="Who the customer pays and is paid by, and how they connect to other customers")
def customer_network(party_id: str, p: Principal = Depends(need("customers:read")), s: Session = Depends(session)) -> dict:
    get_party(s, party_id)
    return {"network": graph.neighbourhood(s, party_id), "related": graph.related_customers(s, party_id)}


@router.get("/{party_id}/paths/{other_id}", summary="How two customers are connected through payments")
def customer_path(party_id: str, other_id: str, p: Principal = Depends(need("customers:read")), s: Session = Depends(session)) -> dict:
    get_party(s, party_id)
    get_party(s, other_id)
    return {"path": graph.path(s, party_id, other_id)}
