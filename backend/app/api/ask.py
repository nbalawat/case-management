"""Ask anything: questions in plain language, answered from the asker's own client and role."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ask import service
from ..ai import gateway as ai
from ..ai import policy as ai_policy
from ..domain.personas import Persona
from .deps import get_session, persona

router = APIRouter(prefix="/api/ask")


class AskIn(BaseModel):
    question: str = Field(max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=40)


@router.post("")
def post_ask(body: AskIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """Asking is reading, so read-only roles may ask. The record of the question is the only thing written."""
    try:
        return service.ask(s, p, body.question, body.conversation_id)
    except service.AskError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/start")
def get_start(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    res = ai.resolve(ai_policy.for_session(s), "ask")
    return {"suggestions": service.suggestions(s, p), "scope": service.scope(s, p), "provider": res.endpoint.id if res.endpoint else "offline",
            "model": res.describe()}


@router.get("/log")
def get_log(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return service.log(s, p)
