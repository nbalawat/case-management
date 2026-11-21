"""Runs scenarios over parties and writes de-duplicated alerts."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Transaction, new_id
from .scenarios import REGISTRY


def run_detection(s: Session, party_ids: set[str] | None = None) -> list[Alert]:
    q = select(Transaction).order_by(Transaction.party_id, Transaction.ts)
    if party_ids is not None:
        if not party_ids:
            return []
        q = q.where(Transaction.party_id.in_(party_ids))
    by_party: dict[str, list[Transaction]] = defaultdict(list)
    for t in s.scalars(q):
        by_party[t.party_id].append(t)
    existing = set(s.scalars(select(Alert.dedupe_key)))
    created: list[Alert] = []
    for pid, txns in by_party.items():
        for sc in REGISTRY.values():
            for h in sc["fn"](txns):
                key = f"{h.scenario}:{pid}:{h.window_key}"
                if key in existing:
                    continue
                existing.add(key)
                a = Alert(id=new_id("alr"), dedupe_key=key, scenario=h.scenario, scenario_version=h.version, party_id=pid,
                          score=round(h.score, 2), explanation=h.explanation, txn_ids=h.txn_ids,
                          created=max(t.ts for t in txns if t.id in set(h.txn_ids)))
                s.add(a)
                created.append(a)
    s.flush()
    return created
