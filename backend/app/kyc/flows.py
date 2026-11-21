"""Flow of funds: where a customer's money came from and where it went, over a period.

Counterparties are shown under their resolved names, so two spellings of one firm are one source. The summary also
measures the patterns an investigator looks for first: how much left again within days, how much was cash, how much
crossed a border.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..detection.scenarios import HIGH_RISK_COUNTRIES
from ..domain.models import Entity, EntityEdge, Party, Transaction
from ..entities import names

TOP = 6
RAPID_DAYS = 3
LABELS = {"cash": "Cash", "card": "Card payments", "ach": "ACH (unnamed)", "wire": "Wire (unnamed)", "p2p": "Person-to-person (unnamed)"}


def _resolved(s: Session, party_id: str) -> dict[str, tuple[str, str, list]]:
    """Every spelling seen on this customer's payments, mapped to the firm it was resolved to."""
    out = {}
    ids = set(s.scalars(select(EntityEdge.entity_id).where(EntityEdge.party_id == party_id)))
    for e in s.scalars(select(Entity).where(Entity.id.in_(ids))) if ids else []:
        for alias in [{"name": e.name}, *e.aliases]:
            out[names.key(alias["name"])] = (e.id, e.name, e.flags)
    return out


def _rapid_share(txns: list[Transaction]) -> float:
    """Of the money that came in, the share that left within RAPID_DAYS, matching each outflow to the oldest unspent inflow."""
    inflows = [[t.ts, t.amount] for t in txns if t.direction == "in"]
    total = sum(a for _, a in inflows)
    if not total:
        return 0.0
    moved, i = 0.0, 0
    for t in (x for x in txns if x.direction == "out"):
        need = t.amount
        while need > 0 and i < len(inflows) and inflows[i][0] <= t.ts:
            take = min(need, inflows[i][1])
            if t.ts - inflows[i][0] <= timedelta(days=RAPID_DAYS):
                moved += take
            inflows[i][1] -= take
            need -= take
            if inflows[i][1] <= 0:
                i += 1
        # an outflow funded by money already there (before the window) matches nothing, and counts for nothing
    return round(moved / total, 4)


def flow(s: Session, party_id: str, since: datetime | None = None, until: datetime | None = None, txn_ids: list[str] | None = None) -> dict | None:
    party = s.scalars(select(Party).where(Party.id == party_id)).first()
    if party is None:
        return None
    q = select(Transaction).where(Transaction.party_id == party_id).order_by(Transaction.ts, Transaction.id)
    if txn_ids is not None:
        q = q.where(Transaction.id.in_(txn_ids))
    if since is not None:
        q = q.where(Transaction.ts >= since)
    if until is not None:
        q = q.where(Transaction.ts <= until)
    txns = list(s.scalars(q))
    resolved = _resolved(s, party_id)
    sides: dict[str, dict[str, dict]] = {"in": defaultdict(lambda: {"amount": 0.0, "txns": 0}), "out": defaultdict(lambda: {"amount": 0.0, "txns": 0})}
    home = party.country or "US"
    for t in txns:
        hit = resolved.get(names.key(t.counterparty)) if t.counterparty else None
        key = hit[0] if hit else f"name:{names.key(t.counterparty)}" if t.counterparty else f"channel:{t.channel}"
        node = sides[t.direction][key]
        node["amount"] += t.amount
        node["txns"] += 1
        node.setdefault("name", hit[1] if hit else t.counterparty or LABELS.get(t.channel, t.channel))
        node.setdefault("entity_id", hit[0] if hit else None)
        node.setdefault("flags", sorted(set(hit[2]) - {"common"}) if hit else [])
        node.setdefault("countries", set()).add(t.counterparty_country or home)

    def side(direction: str) -> list[dict]:
        rows = sorted(sides[direction].values(), key=lambda n: -n["amount"])
        # A flagged firm is never folded into "other": it is the one the reader is looking for.
        keep = [n for n in rows if n["flags"] or any(c in HIGH_RISK_COUNTRIES for c in n["countries"])]
        keep += [n for n in rows if n not in keep][:max(0, TOP - len(keep))]
        rest = [n for n in rows if n not in keep]
        out = [{"name": n["name"], "entity_id": n["entity_id"], "amount": round(n["amount"], 2), "txns": n["txns"], "flags": n["flags"],
                "countries": sorted(n["countries"]), "abroad": any(c != home for c in n["countries"])} for n in sorted(keep, key=lambda n: -n["amount"])]
        if rest:
            out.append({"name": f"{len(rest)} others", "entity_id": None, "amount": round(sum(n["amount"] for n in rest), 2), "txns": sum(n["txns"] for n in rest),
                        "flags": [], "countries": [], "abroad": False, "other": True})
        return out

    total_in = sum(t.amount for t in txns if t.direction == "in")
    total_out = sum(t.amount for t in txns if t.direction == "out")
    volume = total_in + total_out
    share = lambda pick: round(sum(t.amount for t in txns if pick(t)) / volume, 4) if volume else 0.0  # noqa: E731
    return {"party": {"id": party.id, "name": party.name}, "from": txns[0].ts.isoformat() if txns else None, "to": txns[-1].ts.isoformat() if txns else None,
            "txns": len(txns), "total_in": round(total_in, 2), "total_out": round(total_out, 2), "net": round(total_in - total_out, 2),
            "sources": side("in"), "destinations": side("out"),
            "measures": {"rapid_out_share": _rapid_share(txns), "rapid_days": RAPID_DAYS, "cash_share": share(lambda t: t.channel == "cash"),
                         "abroad_share": share(lambda t: (t.counterparty_country or home) != home),
                         "high_risk_share": share(lambda t: t.counterparty_country in HIGH_RISK_COUNTRIES)}}
