"""A client's data lineage: which jobs read and wrote which datasets, and the OpenLineage events behind it."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..lineage import graph as lineage
from .deps import get_session, require

router = APIRouter(prefix="/api/lineage")


@router.get("/graph")
def graph(days: int = Query(30, ge=1, le=365), s: Session = Depends(get_session), _=Depends(require("view_team"))):
    return lineage.graph(s, days) | {"sent_to": _sent_to()}


@router.get("/events")
def events(job: str | None = None, limit: int = Query(100, ge=1, le=500), s: Session = Depends(get_session), _=Depends(require("view_team"))):
    return {"events": lineage.events(s, job, limit)}


@router.get("/events/{event_id}")
def event(event_id: int, s: Session = Depends(get_session), _=Depends(require("view_team"))):
    ev = lineage.event(s, event_id)
    if ev is None:
        raise HTTPException(404, "No such lineage event")
    return ev


def _sent_to() -> str | None:
    """Where events also go, without credentials or a server path: the scheme and host only."""
    import os
    from urllib.parse import urlsplit

    url = os.getenv("LINEAGE_URL", "")
    if not url:
        return None
    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname}" if u.hostname else f"{u.scheme} file"
