"""Computing features, point in time: each value is what could have been known at `as_of`, never after it.

The same code serves training (many customers, each at the moment of an alert) and scoring (one customer, now), so a
model is scored on exactly the features it was trained on. Rows are read once per batch of customers and aggregated
here, which works the same on SQLite and Postgres.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..detection.scenarios import HIGH_RISK_COUNTRIES
from ..domain.models import Alert, Case, Party, Transaction
from .model import Feature, FeatureSet

POSITIVE = {"sar_filed", "fraud_confirmed", "true_match", "exit", "declined"}  # outcomes that mean the concern was real
RISK = {"low": 0, "medium": 1, "high": 2}
BATCH = 500

Key = tuple[str, datetime]


def _txn_match(f: Feature, t) -> bool:
    w = f.where
    if "direction" in w and t.direction != w["direction"]:
        return False
    if "channel" in w and t.channel not in ([w["channel"]] if isinstance(w["channel"], str) else w["channel"]):
        return False
    if w.get("counterparty_country") == "high_risk" and t.counterparty_country not in HIGH_RISK_COUNTRIES:
        return False
    if "amount_from" in w and t.amount < w["amount_from"]:
        return False
    if "amount_below" in w and t.amount >= w["amount_below"]:
        return False
    return True


def _aggregate(f: Feature, rows: list) -> float:
    rows = [r for r in rows if _txn_match(f, r)] if f.source == "transactions" else rows
    if f.agg == "count":
        return float(len(rows))
    values = [getattr(r, f.field) for r in rows]
    if f.agg == "sum":
        return round(float(sum(values)), 2)
    if f.agg == "max":
        return float(max(values, default=0.0))
    return float(len(set(values)))


def _window(rows: list, times: list[datetime], as_of: datetime, days: int) -> list:
    return rows[bisect_left(times, as_of - timedelta(days=days)):bisect_left(times, as_of)]


def compute(s: Session, fs: FeatureSet, keys: Iterable[Key]) -> dict[Key, dict[str, float]]:
    """Every feature in the set for each (customer, moment)."""
    keys = list(dict.fromkeys(keys))
    out: dict[Key, dict[str, float]] = {}
    for i in range(0, len(keys), BATCH):
        out |= _batch(s, fs, keys[i:i + BATCH])
    return out


def _batch(s: Session, fs: FeatureSet, keys: list[Key]) -> dict[Key, dict[str, float]]:
    parties = sorted({p for p, _ in keys})
    earliest, latest = min(t for _, t in keys), max(t for _, t in keys)
    longest = max((f.window_days or 0) for f in fs.features)
    sources = {f.source for f in fs.features}
    txns: dict[str, list] = defaultdict(list)
    if "transactions" in sources:
        for t in s.execute(select(Transaction.party_id, Transaction.ts, Transaction.amount, Transaction.direction, Transaction.channel,
                                  Transaction.counterparty, Transaction.counterparty_country)
                           .where(Transaction.party_id.in_(parties), Transaction.ts >= earliest - timedelta(days=longest), Transaction.ts < latest)
                           .order_by(Transaction.ts)):
            txns[t.party_id].append(t)
    alerts: dict[str, list] = defaultdict(list)
    if "alerts" in sources:
        for a in s.execute(select(Alert.party_id, Alert.created.label("ts")).where(Alert.party_id.in_(parties), Alert.created < latest).order_by(Alert.created)):
            alerts[a.party_id].append(a)
    cases: dict[str, list] = defaultdict(list)
    if "cases" in sources:
        for c in s.execute(select(Case.party_id, Case.closed.label("ts"), Case.disposition).where(Case.party_id.in_(parties), Case.closed.is_not(None),
                                                                                                    Case.closed < latest).order_by(Case.closed)):
            cases[c.party_id].append(c)
    info = {p.id: p for p in s.scalars(select(Party).where(Party.id.in_(parties)))} if "party" in sources else {}
    times = {"transactions": {p: [r.ts for r in v] for p, v in txns.items()}, "alerts": {p: [r.ts for r in v] for p, v in alerts.items()},
             "cases": {p: [r.ts for r in v] for p, v in cases.items()}}
    rows_of = {"transactions": txns, "alerts": alerts, "cases": cases}
    out: dict[Key, dict[str, float]] = {}
    for party_id, as_of in keys:
        values: dict[str, float] = {}
        for f in fs.features:
            if f.source == "party":
                values[f.name] = _party(f, info.get(party_id), as_of)
                continue
            rows, ts = rows_of[f.source].get(party_id, []), times[f.source].get(party_id, [])
            if f.source == "cases":
                rows = rows[:bisect_left(ts, as_of)]
                if f.where.get("outcome") == "positive":
                    rows = [r for r in rows if r.disposition in POSITIVE]
            else:
                rows = _window(rows, ts, as_of, f.window_days)
            values[f.name] = _aggregate(f, rows)
        out[(party_id, as_of)] = values
    return out


def _party(f: Feature, p: Party | None, as_of: datetime) -> float:
    if p is None:
        return 0.0
    if f.field == "risk_rating":
        return float(RISK.get(p.risk_rating, 1))
    if f.field == "pep":
        return 1.0 if p.pep else 0.0
    return float(max(0, (as_of - p.onboarded).days)) if p.onboarded else 0.0
