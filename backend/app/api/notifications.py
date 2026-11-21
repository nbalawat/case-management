"""A person's notifications: how many are unread, the latest, and marking them read."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..domain.personas import Persona
from ..notifications import store
from .deps import get_session, persona

router = APIRouter(prefix="/api/notifications")


class ReadIn(BaseModel):
    ids: list[str] | None = None  # None: all of them


@router.get("")
def get_notifications(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return store.inbox(s, p.id)


@router.post("/read")
def post_read(body: ReadIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return {"marked": store.mark_read(s, p.id, body.ids), **store.inbox(s, p.id)}
