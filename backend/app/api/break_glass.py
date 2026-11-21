"""Break-glass access over HTTP: open, end early, list, review."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import break_glass
from ..break_glass import BreakGlassError
from ..domain.personas import Persona
from .deps import get_session, persona

router = APIRouter(prefix="/api/break-glass")


class OpenIn(BaseModel):
    kind: str
    target: str
    reason: str


@router.get("")
def listing(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return break_glass.listing(s, p)


@router.post("", status_code=201)
def open_grant(body: OpenIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    if p.read_only:
        raise HTTPException(403, f"{p.name} has read-only access")
    try:
        g = break_glass.open_grant(s, p, body.kind, body.target.strip(), body.reason)
    except BreakGlassError as e:
        raise HTTPException(422, str(e)) from None
    return break_glass.out(g, g.opened)


@router.post("/{grant_id}/close")
def close(grant_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    try:
        g = break_glass.close(s, grant_id, p)
    except BreakGlassError as e:
        raise HTTPException(422, str(e)) from None
    return break_glass.out(g, g.closed)


class ReviewIn(BaseModel):
    outcome: str
    note: str = ""


@router.post("/{grant_id}/review")
def review(grant_id: str, body: ReviewIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    try:
        g = break_glass.review(s, grant_id, p, body.outcome, body.note)
    except BreakGlassError as e:
        raise HTTPException(422, str(e)) from None
    return break_glass.out(g, g.reviewed)
