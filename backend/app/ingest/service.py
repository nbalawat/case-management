"""Feed onboarding: profile -> propose mapping -> apply -> reconcile."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Account, Feed, Party, Transaction, new_id
from .connectors import Connector
from .mapping import CANONICAL_FIELDS, VALUE_MAPS, HeuristicProposer, MappingProposer, proposals_as_dicts


def profile(conn: Connector, proposer: MappingProposer | None = None, sample: int = 200) -> dict:
    cols = conn.columns()
    rows = []
    for i, r in enumerate(conn.records()):
        if i >= sample:
            break
        rows.append(r)
    stats = []
    for c in cols:
        vals = [r.get(c, "") for r in rows]
        stats.append({
            "column": c,
            "null_rate": round(sum(1 for v in vals if v in ("", None)) / max(len(vals), 1), 3),
            "distinct": len(set(vals)),
            "examples": list(dict.fromkeys(v for v in vals if v))[:3],
        })
    props = (proposer or HeuristicProposer()).propose(cols, rows)
    return {"name": conn.name, "columns": stats, "proposals": proposals_as_dicts(props), "preview": rows[:5]}


def _parse_ts(v: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S", "%m/%d/%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp {v!r}")


def transform(row: dict, mapping: dict[str, str | None]) -> dict:
    """Map one raw record to the canonical transaction shape. Raises ValueError on bad data."""
    out: dict = {}
    for target, meta in CANONICAL_FIELDS.items():
        src = mapping.get(target)
        raw = (row.get(src) or "").strip() if src else ""
        if not raw:
            if meta["required"]:
                raise ValueError(f"missing {target}")
            continue
        if target in VALUE_MAPS:
            key = raw.upper()
            if key not in VALUE_MAPS[target] and raw.lower() not in VALUE_MAPS[target].values():
                raise ValueError(f"unknown {target} code {raw!r}")
            raw = VALUE_MAPS[target].get(key, raw.lower())
        out[target] = raw
    out["ts"] = _parse_ts(out["ts"])
    out["amount"] = abs(float(out["amount"].replace(",", "")))
    out.setdefault("currency", "USD")
    out.setdefault("counterparty", "")
    out.setdefault("counterparty_country", "US")
    return out


def run_feed(s: Session, conn: Connector, mapping: dict[str, str | None], source_system: str = "legacy-core") -> Feed:
    """Load a feed through the mapping and reconcile source against the canonical store."""
    feed = Feed(id=new_id("feed"), name=conn.name, source_system=source_system, mapping=mapping)
    known_parties = set(s.scalars(select(Party.id)))
    known_accounts = set(s.scalars(select(Account.id)))
    known_txns = set(s.scalars(select(Transaction.id)))
    read = loaded = dupes = 0
    src_total = loaded_total = 0.0
    rejects: list[dict] = []
    for row in conn.records():
        read += 1
        try:
            t = transform(row, mapping)
        except (ValueError, KeyError) as e:
            if len(rejects) < 20:
                rejects.append({"row": read, "reason": str(e)})
            else:
                rejects.append({})
            continue
        src_total += t["amount"]
        if t["txn_id"] in known_txns:
            dupes += 1
            continue
        if t["party_id"] not in known_parties:
            s.add(Party(id=t["party_id"], name=f"Customer {t['party_id']}", risk_rating="medium"))
            known_parties.add(t["party_id"])
        if t["account_id"] not in known_accounts:
            s.add(Account(id=t["account_id"], party_id=t["party_id"]))
            known_accounts.add(t["account_id"])
        s.add(Transaction(id=t["txn_id"], account_id=t["account_id"], party_id=t["party_id"], ts=t["ts"],
                          amount=t["amount"], currency=t["currency"], direction=t["direction"], channel=t["channel"],
                          counterparty=t["counterparty"], counterparty_country=t["counterparty_country"], feed_id=feed.id))
        known_txns.add(t["txn_id"])
        loaded += 1
        loaded_total += t["amount"]
    rejected = len(rejects)
    feed.stats = {
        "read": read, "loaded": loaded, "duplicates": dupes, "rejected": rejected,
        "source_amount": round(src_total, 2), "loaded_amount": round(loaded_total, 2),
        "reconciled": read == loaded + dupes + rejected,
        "reject_samples": [r for r in rejects if r][:20],
    }
    feed.status = "loaded" if rejected == 0 else "loaded_with_rejects"
    s.add(feed)
    s.flush()
    return feed
