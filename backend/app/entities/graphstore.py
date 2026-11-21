"""Where the payment network is walked: the database, or a graph engine behind the same question.

`GraphStore` is the seam. The network pages read their rows through the caller's session either way; an engine is
only ever asked which entities lie on a route or two steps from a customer, and answers with ids. So the client boundary and what a person may
see never depend on the engine, and an engine that is down costs speed, not the feature.
"""
from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Entity, EntityEdge
from .resolution import customer_entity_id

log = logging.getLogger("caseforge.graph")


class GraphError(Exception):
    pass


class GraphStore(Protocol):
    def rebuild(self, s: Session) -> dict: ...
    def route(self, tenant_id: str, start: str, goal: str, max_hops: int) -> list[str] | None: ...
    def shared(self, tenant_id: str, start: str) -> list[tuple[str, str]]: ...
    def size(self, tenant_id: str) -> tuple[int, int] | None: ...
    def drop(self, tenant_id: str) -> None: ...


def links(s: Session) -> tuple[set[str], list[tuple[str, str]]]:
    """This client's network as an engine should hold it: every entity, and every relationship that may connect people.

    Counterparties nearly every customer uses are kept as entities and their relationships left out, or everyone
    would be linked to everyone.
    """
    ents = list(s.execute(select(Entity.id, Entity.kind, Entity.flags)))
    hubs = {i for i, kind, flags in ents if kind == "counterparty" and "common" in flags}
    pairs = sorted({(customer_entity_id(p), e) for p, e in s.execute(select(EntityEdge.party_id, EntityEdge.entity_id)) if e not in hubs})
    return {i for i, _, _ in ents} | {x for pair in pairs for x in pair}, pairs


_engine: GraphStore | None = None
_engine_for: str | None = None


def current() -> GraphStore | None:
    """The configured engine, or None when the network is walked in the database."""
    from ..config import settings

    global _engine, _engine_for
    if not settings.graph_url:
        return None
    if _engine is None or _engine_for != settings.graph_url:
        from .age import connect

        _engine, _engine_for = connect(), settings.graph_url
    return _engine


def after_resolution(s: Session) -> None:
    """Called when a client's entities have been rebuilt. A failure to load the engine never fails the load that caused it."""
    engine = current()
    if engine is None:
        return
    try:
        engine.rebuild(s)
    except Exception:  # noqa: BLE001
        log.exception("Graph rebuild failed; routes are found in the database until the next rebuild")
