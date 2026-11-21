"""Requests for information: questions sent to a customer, branch or relationship manager, with a due date."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..domain.models import Case, InfoRequest, new_id, now
from ..domain.personas import Persona
from .service import TransitionError, record
from ..visibility import system_work

RECIPIENTS = {"customer": "Customer", "branch": "Branch", "relationship_manager": "Relationship manager",
              "payment_ops": "Payment operations", "merchant": "Merchant or card scheme"}


@system_work
def raise_request(s: Session, case: Case, persona: Persona, recipient: str, question: str, due_days: int = 5,
                  ts: datetime | None = None) -> InfoRequest:
    if persona.read_only or not persona.can("work"):
        raise TransitionError("This role cannot raise information requests")
    if case.closed is not None:
        raise TransitionError("The case is closed")
    if recipient not in RECIPIENTS:
        raise TransitionError(f"Unknown recipient. Use one of: {', '.join(RECIPIENTS)}")
    if not question.strip():
        raise TransitionError("A question is required")
    ts = ts or now()
    r = InfoRequest(id=new_id("rfi"), case_id=case.id, recipient=recipient, question=question.strip(), raised_by=persona.id,
                    created=ts, due=ts + timedelta(days=max(1, min(due_days, 60))))
    s.add(r)
    s.flush()
    record(s, case.id, persona.id, "human", "request.raised", {"request_id": r.id, "recipient": recipient, "due": r.due.isoformat()}, ts=ts)
    return r


@system_work
def answer_request(s: Session, r: InfoRequest, persona: Persona, response: str, ts: datetime | None = None) -> InfoRequest:
    if persona.read_only or not persona.can("work"):
        raise TransitionError("This role cannot record responses")
    if r.status != "open":
        raise TransitionError("This request is already answered")
    if not response.strip():
        raise TransitionError("A response is required")
    ts = ts or now()
    r.status, r.response, r.answered, r.answered_by = "answered", response.strip(), ts, persona.id
    s.flush()
    record(s, r.case_id, persona.id, "human", "request.answered", {"request_id": r.id, "recipient": r.recipient}, ts=ts)
    return r
