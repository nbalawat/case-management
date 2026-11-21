"""Runs scenarios over parties and writes de-duplicated alerts."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Transaction, new_id
from .scenarios import REGISTRY, screening_against, tuned
from ..visibility import system_work


@system_work
def run_detection(s: Session, party_ids: set[str] | None = None, only: set[str] | None = None) -> list[Alert]:
    """Run the scenarios (all of them, or just those named in `only`) and write an alert for each new hit."""
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
    from ..sanctions import lists  # here, not at the top: the lists module reads the scenario defaults

    from . import tuning  # the client's own settings, and the scenarios it has switched off

    client = tuning.live(s)
    with screening_against(lists.watch(s)), tuned(client):
        _run(s, by_party, existing, created, only, client.off)
    s.flush()
    return created


def _run(s: Session, by_party: dict[str, list[Transaction]], existing: set[str], created: list[Alert], only: set[str] | None, off: frozenset[str] = frozenset()) -> None:
    for pid, txns in by_party.items():
        for sc in REGISTRY.values():
            if (only is not None and sc["name"] not in only) or sc["name"] in off:
                continue
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
