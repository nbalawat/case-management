"""Cases: list, read, follow and work them.

Reads show only the cases the caller's roles see; a case they may not see does not exist for them (404). Working a
case (moving it, completing tasks, writing the narrative, raising requests) is a person's act and needs a
person's own token; the workflow's four-eyes and segregation rules apply exactly as on the web interface.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ...agents.investigator import investigate
from ...api.deps import case_row
from ...cases import assignments, requests as info_requests
from ...cases.service import TransitionError, allowed_transitions, definition, facts, record, save_narrative, set_task, stage_tasks, transition
from ...domain.models import Alert, Case, CaseEvent, Transaction
from ...visibility import find_case
from ..conventions import ApiError, Page, check_if_match, decode_cursor, etag, limit_of, page
from ..guard import need, session
from ..principal import Principal
from . import schemas as sc

router = APIRouter(prefix="/cases", tags=["Cases"])


def get_case(s: Session, case_id: str) -> Case:
    c = find_case(s, case_id)
    if c is None:
        raise ApiError(404, "No case with that id, or not one you can see", "case_not_found")
    return c


def case_etag(s: Session, c: Case) -> str:
    last = s.scalar(select(func.max(CaseEvent.id)).where(CaseEvent.case_id == c.id))
    return etag(c.id, c.state, c.owner, c.priority, c.restricted, c.closed, last)


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except TransitionError as e:
        raise ApiError(409, str(e), "workflow_refused") from None


def detail(s: Session, c: Case, p: Principal) -> dict:
    ct = definition(c)
    return case_row(c) | {"narrative": c.narrative, "type_version": c.type_version, "tasks": stage_tasks(c, ct),
                          "transitions": allowed_transitions(s, c, p.persona), "facts": facts(s, c)}


@router.get("", response_model=Page[sc.CaseSummary], summary="List cases")
def list_cases(status: Literal["open", "closed", "all"] = "open", case_type: str | None = Query(None, max_length=60),
               state: str | None = Query(None, max_length=40), priority: Literal["critical", "high", "medium", "low"] | None = None,
               queue: str | None = Query(None, max_length=40), owner: str | None = Query(None, max_length=60),
               party_id: str | None = Query(None, max_length=40), created_after: datetime | None = None, created_before: datetime | None = None,
               limit: int = 50, cursor: str | None = None,
               p: Principal = Depends(need("cases:read")), s: Session = Depends(session)):
    """Newest first. Filters combine. Page through with `cursor`; the order is stable while new cases arrive."""
    n = limit_of(limit)
    q = select(Case)
    if status != "all":
        q = q.where(Case.closed.is_(None) if status == "open" else Case.closed.is_not(None))
    for col, val in ((Case.case_type, case_type), (Case.state, state), (Case.priority, priority), (Case.queue, queue),
                     (Case.owner, owner), (Case.party_id, party_id)):
        if val is not None:
            q = q.where(col == val)
    if created_after:
        q = q.where(Case.created >= created_after.replace(tzinfo=None))
    if created_before:
        q = q.where(Case.created < created_before.replace(tzinfo=None))
    after = decode_cursor(cursor, (datetime, str))
    if after:
        q = q.where(or_(Case.created < after[0], and_(Case.created == after[0], Case.id < after[1])))
    rows = list(s.scalars(q.order_by(Case.created.desc(), Case.id.desc()).limit(n + 1)))
    out = page(rows, n, lambda c: (c.created, c.id))
    return {"data": [case_row(c) for c in out["rows"]], "next_cursor": out["next_cursor"], "has_more": out["has_more"]}


@router.get("/{case_id}", response_model=sc.CaseDetail, summary="Read a case")
def read_case(case_id: str, response: Response, p: Principal = Depends(need("cases:read")), s: Session = Depends(session)):
    """With the stage's tasks, the transitions open to the caller (and why any are blocked), and the facts conditions test.
    The ETag header names this version; send it back as If-Match on a write to be sure nobody changed the case meanwhile."""
    c = get_case(s, case_id)
    response.headers["ETag"] = case_etag(s, c)
    return detail(s, c, p)


@router.get("/{case_id}/events", response_model=Page[sc.CaseEventOut], summary="A case's audit trail")
def case_events(case_id: str, limit: int = 100, cursor: str | None = None, p: Principal = Depends(need("cases:read")), s: Session = Depends(session)):
    """Oldest first: every change, who made it and why. Append-only."""
    c = get_case(s, case_id)
    n = limit_of(limit, 500)
    after = decode_cursor(cursor, (int,))
    q = select(CaseEvent).where(CaseEvent.case_id == c.id)
    if after:
        q = q.where(CaseEvent.id > after[0])
    rows = list(s.scalars(q.order_by(CaseEvent.id).limit(n + 1)))
    out = page(rows, n, lambda e: (e.id,))
    return {"data": [{"id": e.id, "case_id": e.case_id, "ts": e.ts, "actor": e.actor, "actor_kind": e.actor_kind, "kind": e.kind,
                      "payload": e.payload or {}} for e in out["rows"]], "next_cursor": out["next_cursor"], "has_more": out["has_more"]}


@router.get("/{case_id}/alerts", response_model=list[sc.AlertOut], summary="The alerts in a case")
def case_alerts(case_id: str, p: Principal = Depends(need("cases:read")), s: Session = Depends(session)):
    c = get_case(s, case_id)
    from .alerts import alert_out

    return [alert_out(a) for a in s.scalars(select(Alert).where(Alert.case_id == c.id).order_by(Alert.created))]


@router.get("/{case_id}/transactions", response_model=list[sc.TransactionOut], summary="The transactions a case's alerts point to")
def case_transactions(case_id: str, p: Principal = Depends(need("cases:read")), s: Session = Depends(session)):
    c = get_case(s, case_id)
    ids = {i for a in s.scalars(select(Alert).where(Alert.case_id == c.id)) for i in (a.txn_ids or [])}
    if not ids:
        return []
    from .transactions import txn_out

    return [txn_out(t) for t in s.scalars(select(Transaction).where(Transaction.id.in_(ids)).order_by(Transaction.ts))]


@router.get("/{case_id}/children", response_model=list[sc.CaseSummary], summary="Cases opened from this one")
def case_children(case_id: str, p: Principal = Depends(need("cases:read")), s: Session = Depends(session)):
    c = get_case(s, case_id)
    return [case_row(x) for x in s.scalars(select(Case).where(Case.parent_id == c.id).order_by(Case.created))]


def _write(s: Session, case_id: str, if_match: str | None) -> Case:
    c = get_case(s, case_id)
    check_if_match(if_match, case_etag(s, c))
    return c


@router.post("/{case_id}/transitions", response_model=sc.CaseDetail, summary="Move a case to another state (people only)")
def post_transition(case_id: str, body: sc.TransitionIn, response: Response, if_match: str | None = Header(None),
                    p: Principal = Depends(need("cases:write")), s: Session = Depends(session)):
    """The workflow decides whether the caller may: their role must be granted the step, four-eyes steps need a different
    person from the one who made the earlier decision, and required notes or reason codes must be given. A 409 says why not."""
    c = _write(s, case_id, if_match)
    _guard(transition, s, c, body.to, p.persona, body.note, reason=body.reason)
    s.flush()
    response.headers["ETag"] = case_etag(s, c)
    return detail(s, c, p)


@router.put("/{case_id}/narrative", response_model=sc.Ok, summary="Write the case narrative (people only)")
def put_narrative(case_id: str, body: sc.NarrativeIn, if_match: str | None = Header(None),
                  p: Principal = Depends(need("cases:write")), s: Session = Depends(session)):
    _guard(save_narrative, s, _write(s, case_id, if_match), body.text, p.persona)
    return {"ok": True}


@router.put("/{case_id}/tasks/{task_id}", response_model=sc.Ok, summary="Complete or reopen a stage task (people only)")
def put_task(case_id: str, task_id: str, body: sc.TaskIn, if_match: str | None = Header(None),
             p: Principal = Depends(need("cases:write")), s: Session = Depends(session)):
    _guard(set_task, s, _write(s, case_id, if_match), task_id, p.persona, body.done)
    return {"ok": True}


@router.post("/{case_id}/information-requests", status_code=201, summary="Ask someone for information (people only)")
def post_info_request(case_id: str, body: sc.InfoRequestIn, p: Principal = Depends(need("cases:write")), s: Session = Depends(session)) -> dict:
    r = _guard(info_requests.raise_request, s, get_case(s, case_id), p.persona, body.recipient, body.question, body.due_days)
    return {"id": r.id, "due": r.due.isoformat(), "status": r.status}


@router.put("/{case_id}/assignment", response_model=sc.CaseSummary, summary="Assign a case to a member of its queue")
def put_assignment(case_id: str, body: sc.AssignIn, if_match: str | None = Header(None),
                   p: Principal = Depends(need("cases:assign")), s: Session = Depends(session)):
    """For workforce-management systems balancing queues. Needs the reassign permission; the new owner must be in the case's queue."""
    c = _write(s, case_id, if_match)
    _guard(assignments.reassign, s, c, body.owner, p.persona, body.reason)
    return case_row(c)


@router.post("/{case_id}/investigations", status_code=202, summary="Ask the investigation agent to brief the case")
def post_investigation(case_id: str, p: Principal = Depends(need("agents:run")), s: Session = Depends(session)) -> dict:
    """The agent reads the case and the customer's history and records a briefing and a recommendation. It never decides."""
    c = get_case(s, case_id)
    record(s, c.id, p.caller, "human", "agent.requested", {"via": "api"})
    run = investigate(s, c, requested_by=p.caller)
    return {"run_id": run.id, "recommendation": run.recommendation, "confidence": run.confidence}
