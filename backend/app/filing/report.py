"""The report in neutral form. Built once from the case; every format renders from this and nothing else."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Case, Party, Transaction
from ..tenants.registry import runtime

SUBMIT = "submit_filings"
CTR_THRESHOLD = 10_000.0
# What the client must tell us about itself before anything can be sent in its name.
INSTITUTION_FIELDS = ("name", "tax_id", "address", "city", "country", "contact_office", "contact_phone")
DEFAULT_PROFILE = {"regime": "fincen", "institution": {}, "channel": "outbox"}
# Detection scenarios in the words of the report. Regulators' own code lists change; formats map these keys to codes.
CATEGORIES = {
    "structuring": ("structuring", "Transactions kept below the cash reporting threshold"),
    "rapid_movement": ("money_laundering", "Funds moved in and straight out again with no evident purpose"),
    "high_risk_geo": ("money_laundering", "Wire transfers involving a high-risk jurisdiction"),
    "sanctions_name": ("sanctions", "Payment involving a party on a watchlist"),
}


def profile(tenant_id: str) -> dict:
    """The client's regulatory profile: which regime it reports under, who it is, and how reports leave."""
    own = (runtime(tenant_id).settings or {}).get("filing") or {}
    return DEFAULT_PROFILE | own | {"institution": dict(own.get("institution") or {})}


def _txn(t: Transaction) -> dict:
    return {"id": t.id, "ts": t.ts.isoformat(), "amount": round(t.amount, 2), "currency": t.currency, "direction": t.direction,
            "channel": t.channel, "counterparty": t.counterparty, "counterparty_country": t.counterparty_country, "account_id": t.account_id}


def _subject(p: Party) -> dict:
    return {"id": p.id, "kind": p.kind, "name": p.name, "country": p.country, "occupation": p.occupation,
            "accounts": [{"id": a.id, "product": a.product, "status": a.status} for a in p.accounts]}


def suspicious_activity(s: Session, case: Case, kind: str, prepared_at: datetime, prior_reference: str = "") -> dict:
    """A SAR or STR: the subject, the activity the alerts point at, and the narrative a person wrote."""
    alerts = list(s.scalars(select(Alert).where(Alert.case_id == case.id).order_by(Alert.created)))
    ids = sorted({i for a in alerts for i in a.txn_ids})
    txns = list(s.scalars(select(Transaction).where(Transaction.id.in_(ids)).order_by(Transaction.ts))) if ids else []
    seen, categories = set(), []
    for a in alerts:
        group, label = CATEGORIES.get(a.scenario, ("other", a.scenario.replace("_", " ").capitalize()))
        if a.scenario not in seen:
            seen.add(a.scenario)
            categories.append({"key": a.scenario, "group": group, "label": label})
    return {
        "kind": kind, "case_id": case.id, "prepared_at": prepared_at.isoformat(), "institution": profile(case.tenant_id)["institution"],
        "subject": _subject(case.party),
        "activity": {"from": txns[0].ts.date().isoformat() if txns else None, "to": txns[-1].ts.date().isoformat() if txns else None,
                     "total": round(sum(t.amount for t in txns), 2), "currency": txns[0].currency if txns else "USD", "count": len(txns),
                     "categories": categories, "channels": sorted({t.channel for t in txns})},
        "transactions": [_txn(t) for t in txns[:500]], "transactions_omitted": max(0, len(txns) - 500),
        "narrative": case.narrative or "", "prior_reference": prior_reference,
    }


def cash_days(s: Session, days: int = 90, threshold: float = CTR_THRESHOLD) -> list[dict]:
    """Each customer and day where cash in, or cash out, came to more than the threshold: the days a CTR is owed for."""
    day = func.date(Transaction.ts)
    since = s.scalar(select(func.max(Transaction.ts)))
    if since is None:
        return []
    rows = s.execute(select(Transaction.party_id, day, Transaction.direction, func.sum(Transaction.amount), func.count())
                     .where(Transaction.channel == "cash", Transaction.ts >= since - timedelta(days=days))
                     .group_by(Transaction.party_id, day, Transaction.direction).having(func.sum(Transaction.amount) > threshold)).all()
    names = {p.id: p.name for p in s.scalars(select(Party).where(Party.id.in_({r[0] for r in rows})))} if rows else {}
    return sorted(({"party_id": pid, "name": names.get(pid, pid), "day": str(d), "direction": direction, "total": round(total, 2), "count": n}
                   for pid, d, direction, total, n in rows), key=lambda r: (r["day"], r["party_id"]), reverse=True)


def currency_transaction(s: Session, tenant_id: str, party_id: str, day: str, prepared_at: datetime, prior_reference: str = "") -> dict | None:
    """A CTR: one customer's cash for one day, in and out."""
    party = s.scalars(select(Party).where(Party.id == party_id)).first()
    txns = [t for t in s.scalars(select(Transaction).where(Transaction.party_id == party_id, Transaction.channel == "cash").order_by(Transaction.ts))
            if t.ts.date().isoformat() == day]
    if party is None or not txns:
        return None
    cash_in, cash_out = sum(t.amount for t in txns if t.direction == "in"), sum(t.amount for t in txns if t.direction == "out")
    return {
        "kind": "CTR", "case_id": None, "prepared_at": prepared_at.isoformat(), "institution": profile(tenant_id)["institution"],
        "subject": _subject(party),
        "activity": {"from": day, "to": day, "total": round(cash_in + cash_out, 2), "cash_in": round(cash_in, 2), "cash_out": round(cash_out, 2),
                     "currency": txns[0].currency, "count": len(txns), "categories": [], "channels": ["cash"]},
        "transactions": [_txn(t) for t in txns], "transactions_omitted": 0, "narrative": "", "prior_reference": prior_reference,
    }
