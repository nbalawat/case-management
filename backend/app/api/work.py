"""Work management: personal work lists, queues, assignment, stage tasks, information requests, timers."""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases import assignments, requests, restriction
from ..cases.case_types import load_case_types
from ..domain.access import RESTRICT, RESTRICTED_VIEW
from ..cases.qa import QA_CASE_TYPE
from ..cases.service import TransitionError, set_task
from ..cases.timers import run_timers
from ..domain.models import Case, InfoRequest, now
from ..domain.personas import Persona
from ..tenants.registry import TenantRuntime
from .deps import actor, case_row, get_case_or_404, get_session, persona, require, tenant

router = APIRouter(prefix="/api")


def describe_view(p: Persona, rt: TenantRuntime) -> dict:
    """In words, which cases this person can see."""
    titles = {k: v["title"] for k, v in load_case_types(rt.id).items()}
    views = ["Every case"] if p.views is None else [v.describe(titles) for v in p.views]
    return {"everything": p.views is None, "views": views, "restricted": p.can(RESTRICTED_VIEW),
            "note": "Always includes cases they own or have acted on." if p.views is not None else ""}
PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e


def _urgency(row: dict):
    return (not row["stage_breached"], PRIORITY_RANK[row["priority"]], row["stage_due"] or row["sla_due"] or "")


def _persona_out(p: Persona, rt: TenantRuntime) -> dict:
    return {"id": p.id, "name": p.name, "role": p.role, "roles": list(p.roles), "title": p.title, "about": p.about, "read_only": p.read_only,
            "org": p.org, "engagement": p.engagement,
            "queues": [{"id": q, "label": rt.policy.queues[q]} for q in p.queues], "permissions": sorted(p.permissions),
            "sees": describe_view(p, rt),
            "tenant": {"id": rt.id, "name": rt.name}}


@router.get("/me")
def me(p: Persona = Depends(persona), rt: TenantRuntime = Depends(tenant)):
    return _persona_out(p, rt)


@router.get("/work")
def my_work(s: Session = Depends(get_session), p: Persona = Depends(persona), rt: TenantRuntime = Depends(tenant)):
    """What this persona should do next: their own cases first, then unassigned work in their queues."""
    run_timers(s)
    open_cases = [case_row(c) for c in s.scalars(select(Case).where(Case.closed.is_(None)))]
    mine = sorted((r for r in open_cases if r["owner"] == p.id), key=_urgency)
    queues = [{"id": q, "label": rt.policy.queues[q],
               "cases": sorted((r for r in open_cases if r["queue"] == q and r["owner"] is None), key=_urgency),
               "assigned": sum(1 for r in open_cases if r["queue"] == q and r["owner"])} for q in p.queues]
    return {"persona": _persona_out(p, rt), "mine": mine, "queues": queues,
            "stats": {"mine": len(mine), "available": sum(len(q["cases"]) for q in queues),
                      "breached": sum(1 for r in mine if r["stage_breached"]),
                      "waiting": sum(1 for r in mine if r["state_kind"] == "wait")}}


@router.get("/team")
def team(s: Session = Depends(get_session), p: Persona = Depends(persona), rt: TenantRuntime = Depends(tenant)):
    if not p.can("view_team"):
        raise HTTPException(403, f"{p.title} does not have the team view")
    run_timers(s)
    cases = list(s.scalars(select(Case)))
    rows = [case_row(c) for c in cases if c.closed is None]
    owned, late = Counter(r["owner"] for r in rows if r["owner"]), Counter(r["owner"] for r in rows if r["owner"] and r["stage_breached"])
    people = [{"id": x.id, "name": x.name, "title": x.title, "queues": list(x.queues), "open": owned[x.id], "breached": late[x.id]}
              for x in rt.policy.people.values() if x.can("work")]
    today = now()
    queues = []
    for q, label in rt.policy.queues.items():
        inq = [r for r in rows if r["queue"] == q]
        waiting = [r for r in inq if r["owner"] is None]
        oldest = min((r["stage_entered"] for r in waiting), default=None)
        queues.append({"id": q, "label": label, "open": len(inq), "unassigned": len(waiting),
                       "breached": sum(1 for r in inq if r["stage_breached"]),
                       "oldest_hours": None if oldest is None else round((today - next(c.state_entered for c in cases if c.state_entered.isoformat() == oldest)).total_seconds() / 3600),
                       "members": [m.id for m in rt.policy.queue_members(q)]})
    reviews = [c for c in cases if c.case_type == QA_CASE_TYPE]
    return {"people": people, "queues": queues,
            "attention": sorted((r for r in rows if r["stage_breached"]), key=_urgency)[:25],
            "qa": {"sampled": len(reviews), "open": sum(1 for c in reviews if c.closed is None),
                   "passed": sum(1 for c in reviews if c.disposition == "qa_passed"),
                   "failed": sum(1 for c in reviews if c.disposition == "qa_failed")},
            "can_reassign": p.can("reassign"), "can_run_timers": p.can("run_timers")}


@router.post("/cases/{case_id}/claim")
def claim(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _guard(assignments.claim, s, get_case_or_404(s, case_id), p)
    return {"ok": True}


@router.post("/cases/{case_id}/release")
def release(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _guard(assignments.release, s, get_case_or_404(s, case_id), p)
    return {"ok": True}


class ReassignIn(BaseModel):
    owner: str
    reason: str = ""


@router.post("/cases/{case_id}/reassign")
def reassign(case_id: str, body: ReassignIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    _guard(assignments.reassign, s, get_case_or_404(s, case_id), body.owner, p, body.reason)
    return {"ok": True}


class RestrictIn(BaseModel):
    restricted: bool = True
    reason: str = Field(min_length=1, max_length=400)


@router.post("/cases/{case_id}/restrict")
def restrict(case_id: str, body: RestrictIn, s: Session = Depends(get_session), p: Persona = Depends(require(RESTRICT))):
    changed = _guard(restriction.set_restricted, s, get_case_or_404(s, case_id), p, body.restricted, body.reason)
    return {"ok": True, "cases": [c.id for c in changed]}


class TaskIn(BaseModel):
    done: bool = True


@router.post("/cases/{case_id}/tasks/{task_id}")
def post_task(case_id: str, task_id: str, body: TaskIn, s: Session = Depends(get_session), p: Persona = Depends(require("work"))):
    _guard(set_task, s, get_case_or_404(s, case_id), task_id, p, body.done)
    return {"ok": True}


class RequestIn(BaseModel):
    recipient: str
    question: str = Field(min_length=1)
    due_days: int = Field(default=5, ge=1, le=60)


@router.post("/cases/{case_id}/requests", status_code=201)
def post_request(case_id: str, body: RequestIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    r = _guard(requests.raise_request, s, get_case_or_404(s, case_id), p, body.recipient, body.question, body.due_days)
    return {"id": r.id}


class AnswerIn(BaseModel):
    response: str = Field(min_length=1)


@router.post("/requests/{request_id}/answer")
def post_answer(request_id: str, body: AnswerIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    r = s.scalars(select(InfoRequest).where(InfoRequest.id == request_id)).first()
    if r is None:
        raise HTTPException(404, "Request not found")
    _guard(requests.answer_request, s, r, p, body.response)
    return {"ok": True}


@router.post("/timers/run")
def post_timers(s: Session = Depends(get_session), _: Persona = Depends(require("run_timers"))):
    return run_timers(s)
