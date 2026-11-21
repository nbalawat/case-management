"""Ask anything: questions in plain language, answered from the asker's own client and role."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ask import service
from ..ai import gateway as ai
from ..ai import policy as ai_policy
from ..db import session_scope
from ..domain.personas import Persona
from ..tenants.registry import TenantRuntime
from ..visibility import bind_viewer
from .deps import get_session, persona, tenant

router = APIRouter(prefix="/api/ask")
log = logging.getLogger(__name__)


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


def _sse(kind: str, payload) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"


@router.post("/stream")
def post_ask_stream(body: AskIn, rt: TenantRuntime = Depends(tenant), p: Persona = Depends(persona)):
    """The same answer as POST /api/ask, as server-sent events: `started`, a `step` after each lookup, then `done`
    with the whole record (or `error`). The stream opens its own session: a request's session is closed by the
    time a streamed body runs."""
    try:
        question = service.clean_question(body.question)
    except service.AskError as e:
        raise HTTPException(422, str(e)) from None

    def events():
        try:
            with session_scope(rt.id) as s:
                bind_viewer(s, p)
                for kind, payload in service.ask_events(s, p, question, body.conversation_id):
                    yield _sse(kind, payload)
        except service.AskError as e:
            yield _sse("error", {"error": str(e)})
        except Exception:  # never a stack trace down the wire; the server log has it
            log.exception("ask stream failed")
            yield _sse("error", {"error": "Something went wrong answering that question"})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/start")
def get_start(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    res = ai.resolve(ai_policy.for_session(s), "ask")
    return {"suggestions": service.suggestions(s, p), "scope": service.scope(s, p), "provider": res.endpoint.id if res.endpoint else "offline",
            "model": res.describe()}


@router.get("/log")
def get_log(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return service.log(s, p)
