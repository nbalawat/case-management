"""Alerts: read them, and send them in from any monitoring or screening engine (overlay mode)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ...agents.investigator import investigate
from ...alerts.hub import ingest_external_alert
from ...domain.models import Alert
from ...visibility import find_case
from ..conventions import ApiError, Page, decode_cursor, limit_of, page
from ..guard import need, session
from ..principal import Principal
from . import schemas as sc

router = APIRouter(prefix="/alerts", tags=["Alerts"])


def alert_out(a: Alert) -> dict:
    return {"id": a.id, "source": a.source, "scenario": a.scenario, "scenario_version": a.scenario_version, "party_id": a.party_id,
            "score": a.score, "explanation": a.explanation, "txn_ids": list(a.txn_ids or []), "status": a.status, "case_id": a.case_id,
            "created": a.created}


def _visible_case(s: Session, case_id: str | None) -> str | None:
    return case_id if case_id and find_case(s, case_id) is not None else None


@router.get("", response_model=Page[sc.AlertOut], summary="List alerts")
def list_alerts(status: str | None = Query(None, max_length=20), scenario: str | None = Query(None, max_length=60),
                source: str | None = Query(None, max_length=40), party_id: str | None = Query(None, max_length=40),
                created_after: datetime | None = None, limit: int = 50, cursor: str | None = None,
                p: Principal = Depends(need("alerts:read")), s: Session = Depends(session)):
    """Newest first. An alert inside a case the caller cannot see shows no case id."""
    n = limit_of(limit)
    q = select(Alert)
    for col, val in ((Alert.status, status), (Alert.scenario, scenario), (Alert.source, source), (Alert.party_id, party_id)):
        if val is not None:
            q = q.where(col == val)
    if created_after:
        q = q.where(Alert.created >= created_after.replace(tzinfo=None))
    after = decode_cursor(cursor, (datetime, str))
    if after:
        q = q.where(or_(Alert.created < after[0], and_(Alert.created == after[0], Alert.id < after[1])))
    rows = list(s.scalars(q.order_by(Alert.created.desc(), Alert.id.desc()).limit(n + 1)))
    out = page(rows, n, lambda a: (a.created, a.id))
    return {"data": [alert_out(a) | {"case_id": _visible_case(s, a.case_id)} for a in out["rows"]],
            "next_cursor": out["next_cursor"], "has_more": out["has_more"]}


@router.get("/{alert_id}", response_model=sc.AlertOut, summary="Read an alert")
def read_alert(alert_id: str, p: Principal = Depends(need("alerts:read")), s: Session = Depends(session)):
    a = s.scalars(select(Alert).where(Alert.id == alert_id)).first()
    if a is None:
        raise ApiError(404, "No alert with that id", "alert_not_found")
    return alert_out(a) | {"case_id": _visible_case(s, a.case_id)}


def _accept(s: Session, body: sc.AlertIn) -> dict:
    key = f"ext:{body.source}:{body.external_id}"
    duplicate = s.scalars(select(Alert.id).where(Alert.dedupe_key == key)).first() is not None
    alert, case = ingest_external_alert(s, **body.model_dump())
    if case is not None:
        investigate(s, case)
    return {"alert_id": alert.id, "status": alert.status, "duplicate": duplicate, "case_id": _visible_case(s, alert.case_id)}


@router.post("", status_code=201, response_model=sc.AlertAccepted, summary="Send an alert")
def post_alert(body: sc.AlertIn, p: Principal = Depends(need("alerts:write")), s: Session = Depends(session)):
    """The hub de-duplicates on (source, external_id), groups the alert with the customer's open work, and opens or updates a
    case. The triage agent may close it inside the client's policy. Replays return the stored alert with duplicate: true."""
    return _accept(s, body)


@router.post("/batch", status_code=201, response_model=sc.AlertBatchOut, summary="Send up to 500 alerts at once")
def post_alerts(body: sc.AlertBatchIn, p: Principal = Depends(need("alerts:write")), s: Session = Depends(session)):
    """All or nothing: if any alert is refused, none are kept."""
    return {"accepted": [_accept(s, a) for a in body.alerts]}
