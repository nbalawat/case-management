"""Day-to-day casework beyond one case at a time: bulk steps, merges, QA scoring, waiting alerts and the client's code lists."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import reference
from ..alerts import hub
from ..cases import bulk, merge, scorecard
from ..cases.qa import QA_CASE_TYPE
from ..cases.service import TransitionError, definition
from ..domain.models import Alert, Case, Party
from ..domain.personas import Persona
from .deps import actor, case_row, get_case_or_404, get_session, persona, require

router = APIRouter(prefix="/api")


def _refuse(e: Exception):
    raise HTTPException(409, str(e)) from None


# --- reference data ------------------------------------------------------------------------------------------------------
@router.get("/reference")
def get_reference(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return {"can_edit": p.can(reference.MANAGE) and not p.read_only, "lists": reference.describe(s)}


class CodesIn(BaseModel):
    codes: list


@router.put("/reference/{list_id}")
def put_reference(list_id: str, body: CodesIn, s: Session = Depends(get_session), p: Persona = Depends(require(reference.MANAGE))):
    try:
        return {"id": list_id, "codes": reference.replace(s, list_id, body.codes, p)}
    except reference.ReferenceError as e:
        raise HTTPException(400, str(e)) from None


# --- many cases at once, and two cases into one ----------------------------------------------------------------------------
class BulkIn(BaseModel):
    action: str
    case_ids: list[str]
    owner: str = ""
    to: str = ""
    note: str = ""
    reason: str = ""
    tick_tasks: bool = False


@router.post("/cases/bulk")
def post_bulk(body: BulkIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    try:
        return bulk.run(s, body.case_ids, body.action, p, owner=body.owner, to=body.to, note=body.note, reason=body.reason, tick_tasks=body.tick_tasks)
    except TransitionError as e:
        raise HTTPException(400, str(e)) from None


class IdsIn(BaseModel):
    case_ids: list[str]


@router.post("/cases/bulk/options")
def post_bulk_options(body: IdsIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return bulk.options(s, body.case_ids, p)


class MergeIn(BaseModel):
    into: str
    note: str = ""


@router.post("/cases/{case_id}/merge")
def post_merge(case_id: str, body: MergeIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    source, target = get_case_or_404(s, case_id), get_case_or_404(s, body.into)
    try:
        merge.merge(s, source, target, p, body.note)
    except TransitionError as e:
        _refuse(e)
    return {"ok": True, "into": target.id}


class UnmergeIn(BaseModel):
    note: str = ""


@router.post("/cases/{case_id}/unmerge")
def post_unmerge(case_id: str, body: UnmergeIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    source = get_case_or_404(s, case_id)
    try:
        merge.unmerge(s, source, p, body.note)
    except TransitionError as e:
        _refuse(e)
    return {"ok": True, "case": source.id}


class SplitIn(BaseModel):
    alert_ids: list[str]
    note: str = ""


@router.post("/cases/{case_id}/split")
def post_split(case_id: str, body: SplitIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    case = get_case_or_404(s, case_id)
    try:
        new = merge.split(s, case, body.alert_ids, p, body.note)
    except TransitionError as e:
        _refuse(e)
    return {"ok": True, "case": new.id}


@router.get("/cases/{case_id}/extras")
def get_extras(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """What the workspace needs beyond the case itself: who it could merge with, its closing reason, and its QA scorecard."""
    c = get_case_or_404(s, case_id)
    data = c.data or {}
    siblings = [] if c.closed is not None else [
        case_row(x) for x in s.scalars(select(Case).where(Case.party_id == c.party_id, Case.case_type == c.case_type, Case.id != c.id, Case.closed.is_(None)))]
    out = {"merge_candidates": siblings, "can_merge": p.can("work") and not p.read_only and (p.can("reassign") or any(x["owner"] == p.id for x in siblings)),
           "merged_into": data.get("merged_into"), "merged_from": data.get("merged_from", []),
           "split_from": data.get("split_from"), "split_into": data.get("split_into", []),
           "can_rework": p.can("work") and not p.read_only,
           "alerts": [] if c.closed is not None else [{"id": a.id, "scenario": a.scenario, "created": a.created.isoformat(), "score": a.score}
                                                      for a in s.scalars(select(Alert).where(Alert.case_id == c.id).order_by(Alert.created, Alert.id))],
           "reason": {"code": data["reason_code"], "label": reference.label(s, "disposition_reasons", data["reason_code"])} if data.get("reason_code") else None,
           "qa": None}
    if c.case_type == QA_CASE_TYPE:
        out["qa"] = {"criteria": reference.codes(s, "qa_criteria"), "root_causes": reference.codes(s, "qa_root_causes"), "scorecard": data.get("scorecard"),
                     "pass_mark": scorecard.PASS_MARK, "can_score": c.closed is None and not p.read_only and c.queue in p.queues}
    return out


class ScoreIn(BaseModel):
    answers: dict


@router.put("/cases/{case_id}/scorecard")
def put_scorecard(case_id: str, body: ScoreIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    try:
        return scorecard.score(s, get_case_or_404(s, case_id), p, body.answers)
    except TransitionError as e:
        _refuse(e)


@router.get("/qa/report")
def get_qa_report(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    if not p.can("view_team"):  # reading it is open to read-only roles such as audit
        raise HTTPException(403, f"{p.title} cannot see the QA report. It needs the 'view team' permission.")
    return scorecard.report(s)


# --- alerts that wait ------------------------------------------------------------------------------------------------------
@router.get("/alerts/intake")
def get_intake(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """Where alerts went: into cases, into waiting, closed by a rule or by an agent."""
    counts = dict(s.execute(select(Alert.status, func.count()).group_by(Alert.status)).all())
    waiting = list(s.scalars(select(Alert).where(Alert.status.in_(("hibernated", "expired"))).order_by(Alert.created.desc()).limit(200)))
    names = {x.id: x.name for x in s.scalars(select(Party).where(Party.id.in_({a.party_id for a in waiting})))} if waiting else {}
    return {"counts": counts, "rules": reference.codes(s, "alert_hibernation"), "can_wake": p.can("work") and not p.read_only,
            "alerts": [{"id": a.id, "status": a.status, "scenario": a.scenario, "score": a.score, "party_id": a.party_id, "party_name": names.get(a.party_id, a.party_id),
                        "source": a.source, "explanation": a.explanation, "created": a.created.isoformat(),
                        "wake_by": a.wake_by.isoformat() if a.wake_by else None, "note": a.policy_note} for a in waiting]}


@router.post("/alerts/{alert_id}/wake")
def post_wake(alert_id: str, s: Session = Depends(get_session), p: Persona = Depends(require("work"))):
    a = s.scalars(select(Alert).where(Alert.id == alert_id)).first()
    if a is None:
        raise HTTPException(404, "Alert not found")
    case = hub.wake(s, a, p.id)
    if case is None:
        raise HTTPException(409, "This alert is not waiting")
    return {"ok": True, "case_id": case.id, "state": definition(case)["states"][case.state]["label"]}
