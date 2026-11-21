"""The knowledge library on the API: the typologies, policies and procedures Ask and the agents cite.

Anyone signed in at a client may read it. They see the platform library with their own client's documents over it,
never another client's.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import knowledge
from ..tenancy import tenant_of
from .deps import get_session

router = APIRouter()


def _documents(client: str) -> dict[str, list[knowledge.Passage]]:
    out: dict[str, list[knowledge.Passage]] = {}
    for p in knowledge.documents(client):
        out.setdefault(p.document, []).append(p)
    return out


def _head(ps: list[knowledge.Passage]) -> dict:
    p = ps[0]
    return {"document": p.document, "title": p.title, "kind": p.kind, "scenarios": list(p.scenarios), "source": p.source,
            "client_document": p.client is not None, "link": knowledge.link(f"kb:{p.document}"),
            "sections": [{"id": x.id, "section": x.section} for x in ps]}


@router.get("/api/knowledge")
def list_documents(s: Session = Depends(get_session)):
    docs = _documents(tenant_of(s))
    return sorted((_head(ps) for ps in docs.values()), key=lambda d: (d["kind"], d["title"]))


@router.get("/api/knowledge/search")
def search(q: str = Query(min_length=2, max_length=300), kind: str | None = None, scenario: str | None = None,
           limit: int = Query(5, ge=1, le=10), s: Session = Depends(get_session)):
    hits = knowledge.search(tenant_of(s), q, kind=kind, scenario=scenario, limit=limit)
    return {"query": q, "passages": [h | {"link": knowledge.link(h["id"])} for h in hits]}


@router.get("/api/knowledge/{document}")
def read_document(document: str, s: Session = Depends(get_session)):
    ps = _documents(tenant_of(s)).get(document)
    if not ps:
        raise HTTPException(404, f"No document '{document}' in this client's library")
    return _head(ps) | {"sections": [{"id": p.id, "section": p.section, "text": p.text} for p in ps]}
