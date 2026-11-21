"""The network around a customer: who they pay and are paid by, and which other customers share those counterparties.

Read from the edges that resolution builds. Counterparties nearly every customer uses (a utility, a payroll firm)
are shown but never used to connect customers, or everyone would be linked to everyone.
"""
from __future__ import annotations

from collections import defaultdict, deque

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import Case, Entity, EntityEdge, Party
from ..tenancy import tenant_of
from . import graphstore
from .resolution import customer_entity_id


def _node(e: Entity, open_cases: dict[str, int], focus: str | None = None) -> dict:
    return {"id": e.id, "kind": e.kind, "label": e.name, "party_id": e.party_id, "flags": list(e.flags), "customers": e.customers,
            "open_cases": open_cases.get(e.party_id or "", 0), "focus": e.id == focus}


def _open_cases(s: Session, party_ids: set[str]) -> dict[str, int]:
    """Open cases per customer, counted through the caller's session: a case they may not see is not counted."""
    if not party_ids:
        return {}
    return dict(s.execute(select(Case.party_id, func.count()).where(Case.closed.is_(None), Case.party_id.in_(party_ids)).group_by(Case.party_id)).all())


def _edge(e: EntityEdge) -> dict:
    return {"source": customer_entity_id(e.party_id), "target": e.entity_id, "txns": e.txns, "sent": e.sent, "received": e.received,
            "total": round(e.sent + e.received, 2), "first": e.first.isoformat(), "last": e.last.isoformat(), "channels": e.channels}


def _shared(s: Session, focus: str) -> dict[str, set[str]] | None:
    """From the graph engine: each entity linked to this one, with the other entities linked to it. None means there
    is no engine, or it could not answer, and the caller reads the same thing from the database."""
    engine = graphstore.current()
    if engine is None:
        return None
    try:
        out: dict[str, set[str]] = defaultdict(set)
        for via, other in engine.shared(tenant_of(s), focus):
            out[via].add(other)
        return out
    except Exception:  # noqa: BLE001  an engine outage must not take the network view away
        graphstore.log.warning("Graph engine could not answer; reading the network from the database", exc_info=True)
        return None


def _owners(s: Session, entity_ids: set[str]) -> dict[str, str]:
    """Which customer each customer entity stands for, read through the caller's session."""
    if not entity_ids:
        return {}
    return dict(s.execute(select(Entity.id, Entity.party_id).where(Entity.id.in_(entity_ids), Entity.kind == "customer", Entity.party_id.is_not(None))).all())


def neighbourhood(s: Session, party_id: str, per_hop: int = 14, per_counterparty: int = 6) -> dict | None:
    """Two hops out from one customer: their counterparties, then the other customers of the uncommon ones."""
    focus = customer_entity_id(party_id)
    if s.scalars(select(Entity.id).where(Entity.id == focus)).first() is None:
        return None
    mine = list(s.scalars(select(EntityEdge).where((EntityEdge.party_id == party_id) | (EntityEdge.entity_id == focus))))
    mine.sort(key=lambda e: -(e.sent + e.received))
    ids = {e.entity_id for e in mine} | {customer_entity_id(e.party_id) for e in mine}
    ents = {e.id: e for e in s.scalars(select(Entity).where(Entity.id.in_(ids)))}
    flagged = [e for e in mine if ents.get(e.entity_id) and (set(ents[e.entity_id].flags) - {"common"} or ents[e.entity_id].kind == "customer")]
    first = list({e.id: e for e in flagged + mine[:per_hop]}.values())
    edges, hidden = list(first), len(mine) - len(first)
    second: list[EntityEdge] = []
    reached = _shared(s, focus)  # who else is on the far side of each counterparty, when a graph engine holds the network
    owners = _owners(s, {o for os in reached.values() for o in os}) if reached is not None else {}
    for e in first:
        target = ents.get(e.entity_id)
        if target is None or target.kind != "counterparty" or "common" in target.flags:
            continue
        q = select(EntityEdge).where(EntityEdge.entity_id == e.entity_id, EntityEdge.party_id != party_id)
        if reached is not None:  # the engine found them: the database is asked only for those relationships, by id
            q = q.where(EntityEdge.party_id.in_({owners[o] for o in reached.get(e.entity_id, ()) if o in owners}))
        others = list(s.scalars(q.order_by((EntityEdge.sent + EntityEdge.received).desc(), EntityEdge.id).limit(per_counterparty + 1)))
        hidden += max(0, len(others) - per_counterparty)
        second += others[:per_counterparty]
    edges += second
    ids = {x for e in edges for x in (customer_entity_id(e.party_id), e.entity_id)} | {focus}
    ents = {e.id: e for e in s.scalars(select(Entity).where(Entity.id.in_(ids)))}
    cases = _open_cases(s, {e.party_id for e in ents.values() if e.party_id})
    return {"focus": focus, "nodes": [_node(ents[i], cases, focus) for i in sorted(ids) if i in ents],
            "edges": [_edge(e) for e in {e.id: e for e in edges}.values()], "hidden": hidden}


def related_customers(s: Session, party_id: str, limit: int = 12) -> list[dict]:
    """Other customers who share an uncommon counterparty with this one, strongest link first."""
    focus = customer_entity_id(party_id)
    mine = {e.entity_id: e for e in s.scalars(select(EntityEdge).where(EntityEdge.party_id == party_id))}
    if not mine:
        return []
    ents = {e.id: e for e in s.scalars(select(Entity).where(Entity.id.in_(set(mine))))}
    rare = [i for i, e in ents.items() if e.kind == "counterparty" and "common" not in e.flags]
    via: dict[str, list[Entity]] = defaultdict(list)
    reached = _shared(s, focus) if rare else None
    if reached is not None:  # walked in the graph engine: it answers with ids, and the rows are read here
        owners = _owners(s, {o for c in rare for o in reached.get(c, ())})
        for c in rare:
            for o in reached.get(c, ()):
                if owners.get(o) not in (None, party_id):
                    via[owners[o]].append(ents[c])
    else:
        for e in s.scalars(select(EntityEdge).where(EntityEdge.entity_id.in_(rare), EntityEdge.party_id != party_id)) if rare else []:
            via[e.party_id].append(ents[e.entity_id])
    direct = {e.party_id for e in s.scalars(select(EntityEdge).where(EntityEdge.entity_id == focus))} \
        | {ents[i].party_id for i in mine if i in ents and ents[i].kind == "customer"}
    people = {p.id: p for p in s.scalars(select(Party).where(Party.id.in_(set(via) | direct)))} if via or direct else {}
    cases = _open_cases(s, set(people))
    out = [{"party_id": pid, "name": p.name, "risk_rating": p.risk_rating, "open_cases": cases.get(pid, 0), "direct": pid in direct,
            "shared": sorted({e.name for e in via.get(pid, [])}), "flagged": any(set(e.flags) - {"common"} for e in via.get(pid, []))}
           for pid, p in people.items()]
    out.sort(key=lambda r: (not r["direct"], not r["flagged"], -len(r["shared"]), r["name"]))
    return out[:limit]


def _route_in_database(s: Session, start: str, goal: str, max_hops: int) -> list[str] | None:
    """Breadth first over every relationship the client has. Fine for a laptop; an engine is the answer past that."""
    hubs = {e.id for e in s.scalars(select(Entity).where(Entity.kind == "counterparty")) if "common" in e.flags}
    links: dict[str, set[str]] = defaultdict(set)
    for e in s.scalars(select(EntityEdge)):
        if e.entity_id in hubs:
            continue
        src = customer_entity_id(e.party_id)
        links[src].add(e.entity_id)
        links[e.entity_id].add(src)
    if start not in links or goal not in links:
        return None
    came, queue = {start: None}, deque([(start, 0)])
    while queue:
        node, depth = queue.popleft()
        if node == goal:
            break
        if depth >= max_hops:
            continue
        for nxt in sorted(links[node]):
            if nxt not in came:
                came[nxt] = node
                queue.append((nxt, depth + 1))
    if goal not in came:
        return None
    chain, node = [], goal
    while node is not None:
        chain.append(node)
        node = came[node]
    return chain[::-1]


def _route(s: Session, start: str, goal: str, max_hops: int) -> list[str] | None:
    """Ask the graph engine when there is one; if it is down, slow or not yet built, answer from the database."""
    engine = graphstore.current()
    if engine is not None and start != goal:
        try:
            return engine.route(tenant_of(s), start, goal, max_hops)
        except Exception:  # noqa: BLE001  an engine outage must not take the network view away
            graphstore.log.warning("Graph engine could not answer; finding the route in the database", exc_info=True)
    return _route_in_database(s, start, goal, max_hops)


def path(s: Session, a: str, b: str, max_hops: int = 6) -> list[dict] | None:
    """A shortest chain of payments linking two customers, ignoring common counterparties. None if there is none within reach.

    Whoever finds the route, the entities on it are read here through the caller's session.
    """
    chain = _route(s, customer_entity_id(a), customer_entity_id(b), max_hops)
    if chain is None:
        return None
    ents = {e.id: e for e in s.scalars(select(Entity).where(Entity.id.in_(chain)))}
    if any(i not in ents for i in chain):  # the engine is behind the database: an entity on its route is gone
        chain = _route_in_database(s, customer_entity_id(a), customer_entity_id(b), max_hops)
        if chain is None:
            return None
        ents = {e.id: e for e in s.scalars(select(Entity).where(Entity.id.in_(chain)))}
    return [{"id": i, "kind": ents[i].kind, "label": ents[i].name, "party_id": ents[i].party_id, "flags": ents[i].flags} for i in chain]
