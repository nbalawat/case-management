"""Sanctions beyond the case: screening lists, rescreening, false-match exemptions, held payments, reports to the authority,
and searches for law enforcement."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import holds
from ..domain.personas import Persona
from ..filing.report import SUBMIT
from ..sanctions import lists, reports, screening, sharing
from ..visibility import find_case
from .deps import actor, get_case_or_404, get_session, persona

router = APIRouter(prefix="/api")
ERRORS = (lists.ListError, screening.ScreeningError, holds.HoldError, reports.ReportError, sharing.SharingError)


def _do(fn):
    try:
        return fn()
    except ERRORS as e:
        text = str(e)
        raise HTTPException(404 if text.endswith("not found") else 403 if " needs " in text else 409, text) from None


def _seen(s: Session, rows: list[dict]) -> list[dict]:
    """Only the rows whose case this person may see."""
    return [r for r in rows if not r.get("case_id") or find_case(s, r["case_id"]) is not None]


@router.get("/sanctions")
def desk(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    held = _seen(s, [h for h in holds.active(s) if h["kind"] == "sanctions"])
    owed = _seen(s, reports.owed(s))
    exemptions = _seen(s, screening.exemptions(s))
    return lists.view(s, p) | {
        "exemptions": exemptions, "holds": held, "reports_owed": owed,
        "can_decide": p.can(screening.DECIDE) and not p.read_only, "can_report": p.can(SUBMIT) and not p.read_only, "me": p.id,
        "counts": {"entries": sum(x["entries"] for x in lists.view(s, p)["lists"] if x["active"]), "held": len(held),
                   "held_amount": round(sum(h["amount"] for h in held), 2), "awaiting_second": sum(1 for h in held if h["proposal"]),
                   "exemptions_pending": sum(1 for x in exemptions if x["status"] == "pending"),
                   "exemptions_active": sum(1 for x in exemptions if x["status"] == "active"),
                   "reports_owed": len(owed), "reports_overdue": sum(1 for r in owed if r["overdue"])},
        "requests": sharing.overview(s, p)}


# --- lists ------------------------------------------------------------------------------------------------------------------
class LoadIn(BaseModel):
    code: str
    name: str = ""
    kind: str = "sanctions"
    source: str = ""
    note: str = ""
    text: str = ""  # the file as issued: JSON or CSV
    entries: list | None = None
    rescreen: bool = True


@router.post("/sanctions/lists")
def post_list(body: LoadIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    """Load a list version, then screen everyone on the books against the lists as they now stand."""
    rows = body.entries if body.entries is not None else _do(lambda: lists.parse(body.text))
    result = _do(lambda: lists.load(s, body.code, rows, p, name=body.name, kind=body.kind, source=body.source, note=body.note))
    return result | {"rescreen": screening.rescreen(s) if body.rescreen and not result["unchanged"] else None}


class EntryIn(BaseModel):
    entry: dict
    note: str = ""


@router.put("/sanctions/lists/{code}/entries")
def put_entry(code: str, body: EntryIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    result = _do(lambda: lists.put_entry(s, code, body.entry, p, body.note))
    return result | {"rescreen": None if result["unchanged"] else screening.rescreen(s)}


class NoteIn(BaseModel):
    note: str = ""


@router.post("/sanctions/lists/{code}/entries/{ref}/remove")
def remove_entry(code: str, ref: str, body: NoteIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return _do(lambda: lists.remove_entry(s, code, ref, p, body.note))


class ActiveIn(BaseModel):
    active: bool
    note: str = ""


@router.post("/sanctions/lists/{code}/active")
def set_active(code: str, body: ActiveIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    wl = _do(lambda: lists.set_active(s, code, body.active, p, body.note))
    return {"code": wl.code, "active": wl.active, "rescreen": screening.rescreen(s) if wl.active else None}


@router.post("/sanctions/rescreen")
def post_rescreen(s: Session = Depends(get_session), p: Persona = Depends(actor)):
    if not p.can(lists.MANAGE):
        raise HTTPException(403, "Rescreening needs the permission to manage lists")
    return screening.rescreen(s)


# --- exemptions -------------------------------------------------------------------------------------------------------------
@router.get("/cases/{case_id}/screening")
def case_screening(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """What this case's hits matched, and the exemptions already proposed from it."""
    get_case_or_404(s, case_id)
    return {"matches": screening.candidates(s, case_id), "exemptions": [x for x in screening.exemptions(s) if x["case_id"] == case_id],
            "can_propose": p.can("work") and not p.read_only, "can_decide": p.can(screening.DECIDE) and not p.read_only, "me": p.id}


class ExemptionIn(BaseModel):
    subject: str
    entry_id: str
    reason: str
    case_id: str | None = None


@router.post("/sanctions/exemptions")
def post_exemption(body: ExemptionIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    if body.case_id:
        get_case_or_404(s, body.case_id)
    return {"id": _do(lambda: screening.propose_exemption(s, body.subject, body.entry_id, body.reason, p, body.case_id)).id}


class DecisionIn(BaseModel):
    approve: bool
    note: str = ""


@router.post("/sanctions/exemptions/{exemption_id}/decision")
def post_exemption_decision(exemption_id: str, body: DecisionIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return {"status": _do(lambda: screening.decide_exemption(s, exemption_id, body.approve, body.note, p)).status}


@router.post("/sanctions/exemptions/{exemption_id}/revoke")
def post_exemption_revoke(exemption_id: str, body: NoteIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return {"status": _do(lambda: screening.revoke_exemption(s, exemption_id, body.note, p)).status}


# --- holds ------------------------------------------------------------------------------------------------------------------
@router.get("/cases/{case_id}/holds")
def case_holds(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return holds.for_case(s, get_case_or_404(s, case_id), p) | {"me": p.id}


class HoldIn(BaseModel):
    target: str
    target_id: str = ""
    reason: str
    note: str = ""


@router.post("/cases/{case_id}/holds")
def post_hold(case_id: str, body: HoldIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    case = get_case_or_404(s, case_id)
    return holds.row(s, _do(lambda: holds.place(s, case, body.target, body.target_id, body.reason, p, body.note)))


def _hold(s: Session, hold_id: str):
    h = _do(lambda: holds._get(s, hold_id))
    if find_case(s, h.case_id) is None:
        raise HTTPException(404, "Hold not found")
    return h


class ChallengeIn(BaseModel):
    method: str


@router.post("/holds/{hold_id}/challenge")
def post_challenge(hold_id: str, body: ChallengeIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _hold(s, hold_id)
    return holds.row(s, _do(lambda: holds.challenge(s, hold_id, body.method, p)))


class ResultIn(BaseModel):
    passed: bool
    note: str = ""


@router.post("/holds/{hold_id}/challenge/result")
def post_challenge_result(hold_id: str, body: ResultIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _hold(s, hold_id)
    return holds.row(s, _do(lambda: holds.challenge_result(s, hold_id, body.passed, p, body.note)))


class OutcomeIn(BaseModel):
    outcome: str
    note: str


@router.post("/holds/{hold_id}/outcome")
def post_outcome(hold_id: str, body: OutcomeIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _hold(s, hold_id)
    return holds.row(s, _do(lambda: holds.decide(s, hold_id, body.outcome, body.note, p)))


@router.post("/holds/{hold_id}/decision")
def post_hold_decision(hold_id: str, body: DecisionIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _hold(s, hold_id)
    return holds.row(s, _do(lambda: holds.decide_proposal(s, hold_id, body.approve, body.note, p)))


@router.post("/holds/{hold_id}/report")
def post_report(hold_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    """Prepare the report to the sanctions authority for a hold that ended blocked or rejected."""
    _hold(s, hold_id)
    f = _do(lambda: reports.prepare(s, hold_id, p))
    return {"filing_id": f.id, "kind": f.kind, "problems": f.problems, "due": f.due.isoformat() if f.due else None}


# --- searches for law enforcement -------------------------------------------------------------------------------------------
class RequestIn(BaseModel):
    reference: str
    subjects: list


@router.post("/sanctions/requests")
def post_request(body: RequestIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return sharing.row(_do(lambda: sharing.receive(s, body.reference, body.subjects, p)))


@router.get("/sanctions/requests/{share_id}")
def get_request(share_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return _do(lambda: sharing.detail(s, share_id, p)) | {"can_handle": p.can(sharing.HANDLE) and not p.read_only}


@router.post("/sanctions/requests/{share_id}/search")
def post_search(share_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return sharing.row(_do(lambda: sharing.search(s, share_id, p)), full=True)


class MatchIn(BaseModel):
    confirmed: bool
    note: str


@router.post("/sanctions/requests/{share_id}/matches/{match_id}")
def post_match(share_id: str, match_id: str, body: MatchIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return sharing.row(_do(lambda: sharing.decide_match(s, share_id, match_id, body.confirmed, body.note, p)), full=True)


@router.post("/sanctions/requests/{share_id}/respond")
def post_respond(share_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return sharing.row(_do(lambda: sharing.respond(s, share_id, p)), full=True)
