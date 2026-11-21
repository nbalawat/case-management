"""KYC beyond the case: a customer's risk rating, ownership, documents, flow of funds and review triggers."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Party, now
from ..domain.personas import Persona
from ..kyc import desk, evidence, flows, ownership, risk, triggers
from ..visibility import find_case
from .deps import actor, get_session, persona

router = APIRouter(prefix="/api")
ERRORS = (risk.RiskError, ownership.OwnershipError, evidence.EvidenceError, desk.DeskError)


def _party(s: Session, party_id: str) -> Party:
    p = s.scalars(select(Party).where(Party.id == party_id)).first()
    if p is None:
        raise HTTPException(404, "Customer not found")
    return p


def _do(fn):
    """Run a change and turn a refusal into a message the person can act on."""
    try:
        return fn()
    except ERRORS as e:
        text = str(e)
        raise HTTPException(404 if text.endswith("not found") else 403 if " needs " in text else 409, text) from None


@router.get("/kyc")
def get_desk(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return desk.overview(s, p)


@router.get("/customers/{party_id}/kyc")
def get_kyc(party_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    _party(s, party_id)
    return {"risk": risk.view(s, party_id, p), "ownership": ownership.view(s, party_id) | {"can_record": p.can("work") and not p.read_only},
            "evidence": evidence.for_party(s, party_id, p), "triggers": triggers.for_party(s, party_id)}


class ReviewIn(BaseModel):
    why: str = ""


@router.post("/customers/{party_id}/review")
def post_review(party_id: str, body: ReviewIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return {"case_id": _do(lambda: desk.start_review(s, party_id, p, body.why)).id}


# --- risk rating ------------------------------------------------------------------------------------------------------------
@router.post("/customers/{party_id}/risk/assess")
def post_assess(party_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _party(s, party_id)
    if not p.can("work"):
        raise HTTPException(403, f"{p.title} cannot assess a customer. It needs someone who works cases.")
    _do(lambda: risk.assess(s, party_id, p.id))
    return risk.view(s, party_id, p)


class OverrideIn(BaseModel):
    band: str
    reason: str
    note: str


@router.post("/customers/{party_id}/risk/override")
def post_override(party_id: str, body: OverrideIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _party(s, party_id)
    _do(lambda: risk.propose_override(s, party_id, body.band, body.reason, body.note, p))
    return risk.view(s, party_id, p)


class DecisionIn(BaseModel):
    approve: bool
    note: str = ""


@router.post("/customers/{party_id}/risk/decision")
def post_decision(party_id: str, body: DecisionIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _party(s, party_id)
    _do(lambda: risk.decide_override(s, party_id, body.approve, body.note, p))
    return risk.view(s, party_id, p)


@router.post("/customers/{party_id}/risk/withdraw")
def post_withdraw_override(party_id: str, body: ReviewIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _party(s, party_id)
    _do(lambda: risk.withdraw_override(s, party_id, p, body.why))
    return risk.view(s, party_id, p)


# --- ownership --------------------------------------------------------------------------------------------------------------
class StructureIn(BaseModel):
    nodes: list
    links: list
    source: str = ""
    note: str = ""


@router.post("/customers/{party_id}/ownership/check")
def post_ownership_check(party_id: str, body: StructureIn, s: Session = Depends(get_session), _: Persona = Depends(persona)):
    """Work out a structure without saving it, so the person can see what it means before recording it."""
    _party(s, party_id)
    nodes, links = _do(lambda: ownership.check(body.nodes, body.links))
    return ownership.analyse(nodes, links, *ownership._rules(s))


@router.put("/customers/{party_id}/ownership")
def put_ownership(party_id: str, body: StructureIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _do(lambda: ownership.record(s, party_id, body.nodes, body.links, p, body.source, body.note))
    return ownership.view(s, party_id) | {"can_record": True}


# --- evidence ---------------------------------------------------------------------------------------------------------------
class EvidenceIn(BaseModel):
    kind: str
    title: str = ""
    filename: str
    media_type: str
    content_base64: str
    expires: str | None = None  # a date
    case_id: str | None = None
    replaces: str | None = None


@router.post("/customers/{party_id}/evidence")
def post_evidence(party_id: str, body: EvidenceIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _party(s, party_id)
    if body.case_id and find_case(s, body.case_id) is None:
        raise HTTPException(404, "Case not found")
    try:
        expires = datetime.fromisoformat(body.expires) if body.expires else None
    except ValueError:
        raise HTTPException(400, "The expiry date is not a date") from None
    e = _do(lambda: evidence.add(s, party_id, kind=body.kind, title=body.title, filename=body.filename, media_type=body.media_type,
                                 data=evidence.decode(body.content_base64), persona=p, case_id=body.case_id, expires=expires, replaces=body.replaces))
    return evidence.row(s, e)


@router.get("/evidence/{evidence_id}/content")
def get_content(evidence_id: str, redacted: bool = False, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    _do(lambda: evidence.guard(s, evidence_id))
    e, data = _do(lambda: evidence.read(s, evidence_id, p, redacted=redacted, purpose="downloaded"))
    name = f"redacted-{e.filename}" if redacted else e.filename
    return Response(data, media_type=e.media_type, headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff",
                                                            "X-Evidence-SHA256": e.redacted_sha256 if redacted else e.sha256})


class RedactIn(BaseModel):
    kinds: list[str] = []
    terms: list[str] = []


@router.post("/evidence/{evidence_id}/redact")
def post_redact(evidence_id: str, body: RedactIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _do(lambda: evidence.guard(s, evidence_id))
    return evidence.row(s, _do(lambda: evidence.redact(s, evidence_id, p, body.kinds, body.terms)))


@router.post("/evidence/{evidence_id}/verify")
def post_verify(evidence_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _do(lambda: evidence.guard(s, evidence_id))
    return evidence.row(s, _do(lambda: evidence.verify(s, evidence_id, p)))


@router.post("/evidence/{evidence_id}/withdraw")
def post_withdraw(evidence_id: str, body: ReviewIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _do(lambda: evidence.guard(s, evidence_id))
    return evidence.row(s, _do(lambda: evidence.withdraw(s, evidence_id, p, body.why)))


# --- flow of funds ----------------------------------------------------------------------------------------------------------
@router.get("/customers/{party_id}/flow")
def get_flow(party_id: str, days: int = 0, case_id: str | None = None, s: Session = Depends(get_session), _: Persona = Depends(persona)):
    """Money in and out for a customer: over the last `days`, over everything, or only the transactions behind one case's alerts."""
    _party(s, party_id)
    ids = None
    if case_id:
        case = find_case(s, case_id)
        if case is None or case.party_id != party_id:
            raise HTTPException(404, "Case not found")
        ids = sorted({t for a in s.scalars(select(Alert).where(Alert.case_id == case_id)) for t in a.txn_ids})
    return flows.flow(s, party_id, since=now() - timedelta(days=days) if days > 0 else None, txn_ids=ids)
