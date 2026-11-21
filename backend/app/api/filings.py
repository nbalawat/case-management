"""Regulatory reports: the register, one report and its document, and each step of its life."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..domain.personas import Persona
from ..filing import service
from ..workflow import engine
from ..filing.formats import FORMATS
from ..filing.report import SUBMIT
from ..tenants.registry import TenantRuntime
from .deps import get_session, persona, require, tenant

router = APIRouter(prefix="/api/filings")


def reader(p: Persona = Depends(persona)) -> Persona:
    """Reports are confidential even inside the firm: only those who send them, or oversee the team, may read the register."""
    if not (p.can(SUBMIT) or p.can("view_team")):
        raise HTTPException(403, f"{p.title} cannot see regulatory reports.")
    return p


def _do(fn, *args, **kw) -> dict:
    try:
        return service.row(fn(*args, **kw))
    except service.FilingError as e:
        raise HTTPException(409, str(e)) from None


@router.get("")
def register(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(reader)):
    return service.overview(s, rt.id) | {"can_submit": p.can(SUBMIT) and not p.read_only}


@router.get("/{filing_id}")
def one(filing_id: str, s: Session = Depends(get_session), p: Persona = Depends(reader)):
    try:
        return service.detail(s, filing_id) | {"can_submit": p.can(SUBMIT) and not p.read_only}
    except service.FilingError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/{filing_id}/document")
def document(filing_id: str, s: Session = Depends(get_session), _: Persona = Depends(reader)):
    try:
        d = service.detail(s, filing_id)
    except service.FilingError as e:
        raise HTTPException(404, str(e)) from None
    ext = FORMATS[d["format"]].extension if d["format"] in FORMATS else "txt"
    return Response(d["document"], media_type="application/xml" if ext == "xml" else "text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{d["id"]}.{ext}"'})


class CtrIn(BaseModel):
    party_id: str
    day: str


@router.post("/ctr")
def prepare_ctr(body: CtrIn, s: Session = Depends(get_session), p: Persona = Depends(require(SUBMIT))):
    return _do(service.prepare_ctr, s, body.party_id, body.day, p)


@router.post("/{filing_id}/recheck")
def recheck(filing_id: str, s: Session = Depends(get_session), p: Persona = Depends(require(SUBMIT))):
    return _do(service.recheck, s, filing_id, p)


@router.post("/{filing_id}/submit")
def submit(filing_id: str, s: Session = Depends(get_session), p: Persona = Depends(require(SUBMIT))):
    def send():
        engine().run_job(s, "filing.submit", {"filing_id": filing_id}, p)  # retried on Temporal if the channel stumbles
        return service._get(s, filing_id)
    return _do(send)


class AnswerIn(BaseModel):
    reference: str = ""
    reason: str = ""


@router.post("/{filing_id}/acknowledge")
def acknowledge(filing_id: str, body: AnswerIn, s: Session = Depends(get_session), p: Persona = Depends(require(SUBMIT))):
    return _do(service.acknowledge, s, filing_id, body.reference, p)


@router.post("/{filing_id}/reject")
def reject(filing_id: str, body: AnswerIn, s: Session = Depends(get_session), p: Persona = Depends(require(SUBMIT))):
    return _do(service.reject, s, filing_id, body.reason, p)


class AmendIn(BaseModel):
    narrative: str | None = None


@router.post("/{filing_id}/amend")
def amend(filing_id: str, body: AmendIn, s: Session = Depends(get_session), p: Persona = Depends(require(SUBMIT))):
    return _do(service.amend, s, filing_id, p, body.narrative)
