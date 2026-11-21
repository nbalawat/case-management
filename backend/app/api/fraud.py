"""Fraud beyond the case: disputes with their clocks and recoveries, and the fraud desk."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import holds
from ..domain.personas import Persona
from ..fraud import disputes
from ..visibility import find_case
from .deps import actor, get_case_or_404, get_session, persona

router = APIRouter(prefix="/api")


def _do(fn):
    try:
        return fn()
    except disputes.DisputeError as e:
        text = str(e)
        raise HTTPException(404 if text.endswith("not found") else 403 if " needs " in text else 409, text) from None


@router.get("/fraud")
def desk(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """Disputes and fraud holds on the cases this person may see, with the totals over the same cases."""
    rows = [r for r in disputes.all_rows(s) if find_case(s, r["case_id"]) is not None]
    held = [h for h in holds.active(s) if h["kind"] != "sanctions" and find_case(s, h["case_id"]) is not None]
    return {"disputes": rows, "summary": disputes.summary(rows), "holds": held, "can_work": p.can("work") and not p.read_only}


@router.get("/cases/{case_id}/disputes")
def case_disputes(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return disputes.for_case(s, get_case_or_404(s, case_id), p)


class OpenIn(BaseModel):
    txn_ids: list[str]
    reason: str
    reported_at: str | None = None
    note: str = ""


@router.post("/cases/{case_id}/disputes")
def post_dispute(case_id: str, body: OpenIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    case = get_case_or_404(s, case_id)
    try:
        reported = datetime.fromisoformat(body.reported_at) if body.reported_at else None
    except ValueError:
        raise HTTPException(400, "The date the customer reported it is not a date") from None
    return disputes.row(s, _do(lambda: disputes.open_dispute(s, case, body.txn_ids, body.reason, p, reported, body.note)))


def _dispute(s: Session, dispute_id: str) -> None:
    d = _do(lambda: disputes._get(s, dispute_id))
    if find_case(s, d.case_id) is None:
        raise HTTPException(404, "Dispute not found")


class StepIn(BaseModel):
    step: str  # credit | final_credit | recovery | recovered | close
    amount: float = 0.0
    reference: str = ""
    outcome: str | None = None
    note: str = ""


@router.post("/disputes/{dispute_id}")
def post_step(dispute_id: str, body: StepIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _dispute(s, dispute_id)
    steps = {
        "credit": lambda: disputes.credit(s, dispute_id, body.amount, p, note=body.note),
        "final_credit": lambda: disputes.credit(s, dispute_id, body.amount, p, final=True, note=body.note),
        "recovery": lambda: disputes.start_recovery(s, dispute_id, body.reference, p, body.note),
        "recovered": lambda: disputes.record_recovery(s, dispute_id, body.amount, p, body.note),
        "close": lambda: disputes.close(s, dispute_id, p, body.outcome, body.note),
    }
    if body.step not in steps:
        raise HTTPException(400, f"A step is one of: {', '.join(steps)}")
    return disputes.row(s, _do(steps[body.step]))
