"""Detection governance: each client's scenario settings with their backtests, and lookback projects."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..detection import lookbacks, tuning
from ..domain.personas import Persona
from ..visibility import find_case
from .deps import actor, get_session, persona

router = APIRouter(prefix="/api")


def _do(fn):
    try:
        return fn()
    except (tuning.TuningError, lookbacks.LookbackError) as e:
        text = str(e)
        raise HTTPException(404 if text.endswith("not found") else 403 if " needs the " in text else 409, text) from None


def _lookback(s: Session, row: dict) -> dict:
    """A project as this person may see it: the totals are everyone's, the cases listed are only theirs to open."""
    seen = [c for c in row["progress"]["case_ids"] if find_case(s, c) is not None]
    return row | {"progress": row["progress"] | {"case_ids": seen}}


@router.get("/detection")
def desk(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return tuning.view(s, p) | {"lookbacks": [_lookback(s, r) for r in lookbacks.rows(s)], "lookback_limit": lookbacks.LIMIT,
                                "can_sign_off": p.can(lookbacks.SIGN_OFF) and not p.read_only}


class SettingsIn(BaseModel):
    params: dict = {}
    enabled: bool = True
    reason: str = ""
    days: int = 365


@router.post("/detection/scenarios/{name}/backtest")
def post_backtest(name: str, body: SettingsIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """What the settings would have done to past alerts. Changes nothing, so anyone who can see the page may try one."""
    if not 30 <= body.days <= 1825:
        raise HTTPException(400, "Days must be between 30 and 1825")
    return _do(lambda: tuning.backtest(s, name, tuning.validate(name, body.params), body.enabled, body.days))


@router.post("/detection/scenarios/{name}/proposals")
def post_proposal(name: str, body: SettingsIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return tuning.row(_do(lambda: tuning.propose(s, name, body.params, body.enabled, body.reason, p, days=body.days)), full=True)


class DecisionIn(BaseModel):
    approve: bool
    note: str = ""


@router.post("/detection/proposals/{version_id}/decision")
def post_decision(version_id: str, body: DecisionIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return tuning.row(_do(lambda: tuning.decide(s, version_id, body.approve, body.note, p)))


@router.post("/detection/proposals/{version_id}/withdraw")
def post_withdraw(version_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return tuning.row(_do(lambda: tuning.withdraw(s, version_id, p)))


class LookbackIn(BaseModel):
    name: str
    reason: str
    scenario: str
    params: dict = {}
    period_from: str
    period_to: str
    risk_ratings: list[str] = []
    due: str | None = None


@router.post("/lookbacks")
def post_lookback(body: LookbackIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    lb = _do(lambda: lookbacks.create(s, p, body.name, body.reason, body.scenario, body.params, body.period_from, body.period_to, body.risk_ratings, body.due))
    return _lookback(s, lookbacks.row(s, lb, full=True))


class NoteIn(BaseModel):
    note: str = ""


def _run_lookback(s: Session, lookback_id: str, p: Persona):
    """Running a project raises its alerts: a durable job on Temporal, retried if a worker dies halfway; at once otherwise."""
    from ..workflow import engine

    engine().run_job(s, "lookback.run", {"lookback_id": lookback_id}, p)
    return lookbacks._get(s, lookback_id)


@router.post("/lookbacks/{lookback_id}/{step}")
def post_step(lookback_id: str, step: str, body: NoteIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    steps = {"count": lambda: lookbacks.refresh(s, lookback_id, p), "run": lambda: _run_lookback(s, lookback_id, p),
             "close": lambda: lookbacks.close(s, lookback_id, body.note, p), "cancel": lambda: lookbacks.cancel(s, lookback_id, body.note, p)}
    if step not in steps:
        raise HTTPException(404, "Not found")
    return _lookback(s, lookbacks.row(s, _do(steps[step]), full=True))


@router.get("/lookbacks/{lookback_id}/report", response_class=PlainTextResponse)
def get_report(lookback_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return PlainTextResponse(_do(lambda: lookbacks.report_text(s, lookback_id)), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="lookback-{lookback_id}.md"'})
