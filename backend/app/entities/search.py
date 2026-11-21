"""One search box across customers, counterparties, cases, alerts and transactions.

`SearchIndex` is the seam: this build answers from the database, a deployment at scale puts OpenSearch behind the
same three methods. Whatever the index returns, cases and alerts are re-read through the caller's session before
they are shown, so search can never reveal a case the person may not see.
"""
from __future__ import annotations

import re
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..cases.service import definition
from ..domain.models import Alert, Case, Entity, Party, Transaction
from . import names

KINDS = ("customer", "counterparty", "case", "alert", "transaction")
_ID = re.compile(r"^(case|alr|ent|mc)-[0-9a-z]+$|^[PAT][0-9A-Fa-f]{4,}$")


class SearchIndex(Protocol):
    def customers(self, s: Session, q: str, limit: int) -> list[tuple[float, Party]]: ...
    def counterparties(self, s: Session, q: str, limit: int) -> list[tuple[float, Entity]]: ...


def _score(q: str, text: str) -> float:
    """How well a typed query fits a name: whole match, a leading part, a word inside, or a near spelling."""
    a, b = names.fold(q).strip(), names.fold(text)
    if not a:
        return 0.0
    if a == b:
        return 1.0
    if b.startswith(a):
        return 0.92
    if any(w.startswith(a) for w in b.split()):
        return 0.85
    if a in b:
        return 0.75
    # A near spelling: every typed word must be a typo of some word in the name, not a shorter or longer word.
    typed, words = names.tokens(q), names.tokens(text)
    if not typed or not words:
        return 0.0
    close = [max((names.jaro_winkler(t, w) for w in words if abs(len(t) - len(w)) <= 1), default=0.0) for t in typed]
    return round(0.7 * sum(close) / len(close), 4) if min(close) >= 0.88 else 0.0


class DatabaseIndex:
    """Good to a few hundred thousand names. Past that, the same interface over a search engine."""

    def customers(self, s: Session, q: str, limit: int) -> list[tuple[float, Party]]:
        like = f"%{q.strip()}%"
        hits = list(s.scalars(select(Party).where(or_(Party.name.ilike(like), Party.id.ilike(f"{q.strip()}%"))).limit(limit * 5)))
        if len(hits) < limit:  # nothing contains it: try near spellings on the first word
            word = (names.tokens(q) or [""])[0][:3]
            if len(word) == 3:
                hits += [p for p in s.scalars(select(Party).where(Party.name.ilike(f"%{word}%")).limit(400)) if p not in hits]
        scored = [(1.0 if p.id.lower() == q.strip().lower() else max(_score(q, p.name), 0.9 if p.id.lower().startswith(q.strip().lower()) else 0), p) for p in hits]
        return sorted([x for x in scored if x[0] > 0], key=lambda x: (-x[0], x[1].name))[:limit]

    def counterparties(self, s: Session, q: str, limit: int) -> list[tuple[float, Entity]]:
        k = names.key(q)
        if not k:
            return []
        word = k.split()[0][:3]
        pool = list(s.scalars(select(Entity).where(Entity.kind == "counterparty", or_(Entity.key.ilike(f"%{k}%"), Entity.key.ilike(f"%{word}%"))).limit(600)))
        scored = [(max([_score(q, e.name)] + [_score(q, a["name"]) * 0.98 for a in e.aliases]), e) for e in pool]
        return sorted([x for x in scored if x[0] > 0], key=lambda x: (-x[0], -x[1].txns))[:limit]


index: SearchIndex = DatabaseIndex()


def search(s: Session, q: str, limit: int = 8) -> dict:
    q = (q or "").strip()
    out = {k: [] for k in KINDS}
    if len(q) < 2:
        return {"query": q, "groups": out, "total": 0}
    customers = index.customers(s, q, limit)
    out["customer"] = [{"id": p.id, "title": p.name, "detail": f"{p.id} · {p.occupation or p.kind} · {p.risk_rating} risk", "score": round(sc, 2)}
                       for sc, p in customers]
    out["counterparty"] = [{"id": e.id, "title": e.name, "score": round(sc, 2), "flags": e.flags,
                            "detail": f"{e.customers} customer{'s' if e.customers != 1 else ''} · {len(e.aliases)} spelling{'s' if len(e.aliases) != 1 else ''}"
                            + (f" · {', '.join(e.countries)}" if e.countries else "")} for sc, e in index.counterparties(s, q, limit)]
    # Cases: by id, or belonging to a customer that matched. Read through the session, so the caller's view applies.
    party_ids = [p.id for _, p in customers]
    cq = select(Case).order_by(Case.closed.is_not(None), Case.created.desc()).limit(limit)
    cases = list(s.scalars(cq.where(or_(Case.id.ilike(f"{q}%"), Case.party_id.in_(party_ids)) if party_ids else Case.id.ilike(f"{q}%"))))
    out["case"] = [{"id": c.id, "title": f"{c.party.name} · {definition(c)['title']}", "open": c.closed is None, "restricted": c.restricted,
                    "detail": f"{c.id} · {definition(c)['states'].get(c.state, {}).get('label', c.state)}", "score": 1.0 if c.id == q else 0.8} for c in cases]
    if _ID.match(q):
        out["alert"] = [{"id": a.id, "title": f"{a.scenario.replace('_', ' ')} · {a.party.name}", "detail": f"{a.id} · score {a.score:.2f}",
                         "case_id": a.case_id, "party_id": a.party_id, "score": 1.0}
                        for a in s.scalars(select(Alert).where(Alert.id.ilike(f"{q}%")).limit(limit))]
        out["transaction"] = [{"id": t.id, "title": f"{t.amount:,.2f} {t.currency} {'to' if t.direction == 'out' else 'from'} {t.counterparty or 'cash'}",
                               "detail": f"{t.id} · {t.ts.date().isoformat()} · {t.channel}", "party_id": t.party_id, "score": 1.0}
                              for t in s.scalars(select(Transaction).where(Transaction.id.ilike(f"{q}%")).limit(limit))]
    return {"query": q, "groups": out, "total": sum(len(v) for v in out.values())}
