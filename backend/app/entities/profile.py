"""Everything the platform knows about one customer, or one counterparty, in a single read."""
from __future__ import annotations

from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cases.service import definition
from ..domain.models import Alert, Case, Entity, EntityEdge, MediaItem, Party, Transaction
from ..domain.personas import policy_of
from . import graph, resolution


def _case(c: Case) -> dict:
    ct = definition(c)
    return {"id": c.id, "type_title": ct["title"], "state_label": ct["states"].get(c.state, {}).get("label", c.state), "open": c.closed is None,
            "priority": c.priority, "created": c.created.isoformat(), "disposition": c.disposition, "restricted": c.restricted,
            "queue_label": policy_of(c.tenant_id).queues.get(c.queue or "", "")}


def customer(s: Session, party_id: str) -> dict | None:
    p = s.scalars(select(Party).where(Party.id == party_id)).first()
    if p is None:
        return None
    month = func.strftime("%Y-%m", Transaction.ts) if s.bind.dialect.name == "sqlite" else func.to_char(Transaction.ts, "YYYY-MM")
    money_in = func.sum(sql_case((Transaction.direction == "in", Transaction.amount), else_=0.0))
    money_out = func.sum(sql_case((Transaction.direction == "out", Transaction.amount), else_=0.0))
    activity = [{"month": m, "in": round(float(i or 0), 2), "out": round(float(o or 0), 2), "txns": n} for m, i, o, n in
                s.execute(select(month, money_in, money_out, func.count()).where(Transaction.party_id == party_id).group_by(month).order_by(month))]
    by_channel = [{"channel": ch, "txns": n, "total": round(float(t or 0), 2)} for ch, n, t in
                  s.execute(select(Transaction.channel, func.count(), func.sum(Transaction.amount)).where(Transaction.party_id == party_id)
                            .group_by(Transaction.channel).order_by(func.sum(Transaction.amount).desc()))]
    edges = list(s.scalars(select(EntityEdge).where(EntityEdge.party_id == party_id).order_by((EntityEdge.sent + EntityEdge.received).desc())))
    ents = {e.id: e for e in s.scalars(select(Entity).where(Entity.id.in_({e.entity_id for e in edges})))} if edges else {}
    counterparties = [{"entity_id": e.entity_id, "name": ents[e.entity_id].name, "kind": ents[e.entity_id].kind, "party_id": ents[e.entity_id].party_id,
                       "flags": ents[e.entity_id].flags, "countries": ents[e.entity_id].countries, "txns": e.txns, "sent": e.sent,
                       "received": e.received, "last": e.last.isoformat(), "spellings": len(ents[e.entity_id].aliases)}
                      for e in edges if e.entity_id in ents]
    cases = [_case(c) for c in s.scalars(select(Case).where(Case.party_id == party_id).order_by(Case.created.desc()))]  # the caller's view
    alerts = s.scalar(select(func.count()).select_from(Alert).where(Alert.party_id == party_id)) or 0
    totals = s.execute(select(func.count(), money_in, money_out, func.min(Transaction.ts), func.max(Transaction.ts))
                       .where(Transaction.party_id == party_id)).one()
    return {
        "party": {"id": p.id, "name": p.name, "kind": p.kind, "occupation": p.occupation, "country": p.country, "risk_rating": p.risk_rating,
                  "pep": p.pep, "onboarded": p.onboarded.date().isoformat(), "accounts": [{"id": a.id, "product": a.product, "status": a.status} for a in p.accounts]},
        "entity_id": resolution.customer_entity_id(p.id),
        "totals": {"txns": totals[0], "in": round(float(totals[1] or 0), 2), "out": round(float(totals[2] or 0), 2),
                   "first": totals[3].isoformat() if totals[3] else None, "last": totals[4].isoformat() if totals[4] else None,
                   "counterparties": len(counterparties), "alerts": alerts, "open_cases": sum(1 for c in cases if c["open"]), "cases": len(cases)},
        "activity": activity, "channels": by_channel,
        "counterparties": counterparties[:25], "more_counterparties": max(0, len(counterparties) - 25),
        "flagged": [c for c in counterparties if set(c["flags"]) - {"common"}],
        "cases": cases, "related": graph.related_customers(s, party_id), "duplicates": resolution.duplicates_of(s, party_id),
        "media": [{"id": x.id, "source": x.source, "title": x.title, "published": x.published.isoformat()}
                  for x in s.scalars(select(MediaItem).where(MediaItem.party_id == party_id))],
        "network": graph.neighbourhood(s, party_id),
    }


def counterparty(s: Session, entity_id: str) -> dict | None:
    e = s.scalars(select(Entity).where(Entity.id == entity_id)).first()
    if e is None:
        return None
    edges = list(s.scalars(select(EntityEdge).where(EntityEdge.entity_id == entity_id).order_by((EntityEdge.sent + EntityEdge.received).desc())))
    people = {p.id: p for p in s.scalars(select(Party).where(Party.id.in_({x.party_id for x in edges})))} if edges else {}
    cases = graph._open_cases(s, set(people))
    return {"entity": {"id": e.id, "kind": e.kind, "name": e.name, "party_id": e.party_id, "countries": e.countries, "flags": e.flags,
                       "aliases": e.aliases, "customers": e.customers, "txns": e.txns, "total": e.total},
            "customers": [{"party_id": x.party_id, "name": people[x.party_id].name, "risk_rating": people[x.party_id].risk_rating,
                           "txns": x.txns, "sent": x.sent, "received": x.received, "last": x.last.isoformat(),
                           "open_cases": cases.get(x.party_id, 0)} for x in edges[:50] if x.party_id in people],
            "more_customers": max(0, len(edges) - 50)}
