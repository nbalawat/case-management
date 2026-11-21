"""Customers, counterparties, the network between them, entity resolution and search."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..customers import service as customers
from ..domain.models import Entity, MatchCandidate
from ..domain.personas import Persona
from ..entities import graph, profile, resolution, search
from ..tenants.registry import TenantRuntime
from .deps import get_session, persona, require, tenant

router = APIRouter(prefix="/api")


@router.get("/search")
def get_search(q: str = "", limit: int = 8, s: Session = Depends(get_session)):
    return search.search(s, q[:120], max(1, min(limit, 25)))


@router.get("/customers/{party_id}")
def get_customer(party_id: str, s: Session = Depends(get_session)):
    out = profile.customer(s, party_id)
    if out is None:
        raise HTTPException(404, "Customer not found")
    return out


class IdentityChange(BaseModel):
    changes: dict
    reason: str = ""


class IdentifierIn(BaseModel):
    kind: str
    value: str
    issuer_country: str = ""
    expires: str = ""


class IdentifierOut(BaseModel):
    kind: str
    value: str
    reason: str


class Reason(BaseModel):
    reason: str


def _customers(fn):
    try:
        return fn()
    except customers.CustomerError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from None


@router.post("/customers/{party_id}/identity")
def post_identity(party_id: str, body: IdentityChange, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """Correct a customer's identity, address or contact details. The change is kept with who made it and why."""
    return _customers(lambda: customers.update(s, party_id, body.changes, p, body.reason))


@router.post("/customers/{party_id}/identifiers")
def post_identifier(party_id: str, body: IdentifierIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return _customers(lambda: customers.add_identifier(s, party_id, body.kind, body.value, p, body.issuer_country, body.expires))


@router.post("/customers/{party_id}/identifiers/remove")
def remove_identifier(party_id: str, body: IdentifierOut, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return _customers(lambda: customers.remove_identifier(s, party_id, body.kind, body.value, p, body.reason))


@router.post("/customers/{party_id}/identifiers/reveal")
def reveal_identifiers(party_id: str, body: Reason, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """The full numbers behind the masked ones. Who asked, and why, is recorded on the customer."""
    return _customers(lambda: customers.reveal(s, party_id, p, body.reason))


@router.get("/customers/{party_id}/history")
def customer_history(party_id: str, s: Session = Depends(get_session)):
    return {"events": customers.history(s, party_id)}


@router.get("/customers/{party_id}/path/{other_id}")
def get_path(party_id: str, other_id: str, s: Session = Depends(get_session)):
    """How two customers are connected through payments, if they are."""
    return {"path": graph.path(s, party_id, other_id)}


@router.get("/counterparties/{entity_id}")
def get_counterparty(entity_id: str, s: Session = Depends(get_session)):
    out = profile.counterparty(s, entity_id)
    if out is None:
        raise HTTPException(404, "Counterparty not found")
    return out


@router.get("/entities")
def overview(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(persona)):
    """The resolution desk: what the platform has merged by itself, what it is unsure of, and what people decided."""
    counts = dict(s.execute(select(Entity.kind, func.count()).group_by(Entity.kind)).all())
    cps = list(s.scalars(select(Entity).where(Entity.kind == "counterparty")))
    merged = sorted((e for e in cps if len(e.aliases) > 1), key=lambda e: (-len(e.aliases), e.name))
    return {"tenant": {"id": rt.id, "name": rt.name}, "can_decide": p.can(resolution.RESOLVE), "thresholds": resolution.thresholds(rt.id),
            "counts": {"customers": counts.get("customer", 0), "counterparties": counts.get("counterparty", 0),
                       "spellings": sum(len(e.aliases) for e in cps), "merged": len(merged),
                       "open": s.scalar(select(func.count()).select_from(MatchCandidate).where(MatchCandidate.status == "open")) or 0,
                       "decided": s.scalar(select(func.count()).select_from(MatchCandidate).where(MatchCandidate.status != "open")) or 0},
            "questions": resolution.questions(s, "open"), "decided": resolution.questions(s, "decided", 50),
            "merged": [{"id": e.id, "name": e.name, "aliases": e.aliases, "flags": e.flags, "customers": e.customers} for e in merged[:40]],
            "flagged": [{"id": e.id, "name": e.name, "flags": e.flags, "customers": e.customers, "countries": e.countries, "total": e.total}
                        for e in sorted(cps, key=lambda e: -e.total) if set(e.flags) - {"common"}][:40]}


class DecisionIn(BaseModel):
    same: bool
    note: str = ""


@router.post("/entities/questions/{candidate_id}")
def decide(candidate_id: str, body: DecisionIn, s: Session = Depends(get_session), p: Persona = Depends(require(resolution.RESOLVE))):
    try:
        c = resolution.decide(s, candidate_id, p, body.same, body.note)
    except resolution.ResolutionError as e:
        raise HTTPException(404, str(e)) from None
    return {"ok": True, "status": c.status}


@router.post("/entities/resolve")
def run(s: Session = Depends(get_session), _: Persona = Depends(require(resolution.RESOLVE))):
    return resolution.resolve(s)
