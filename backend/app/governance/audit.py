"""Audit search: the case audit trail across cases, for the cases the asker may see."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import Case, CaseEvent

PAGE = 25
EXPORT_LIMIT = 5000


class AuditError(ValueError):
    pass


def _when(value: str | None, what: str, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        raise AuditError(f"{what} is not a date") from None
    return d.replace(hour=23, minute=59, second=59) if end and len(value) <= 10 else d


def _query(s: Session, actor: str | None, kind: str | None, case_id: str | None, date_from: str | None, date_to: str | None, actor_kind: str | None):
    # Joining to the case applies the asker's visibility: an event on a case they cannot see is not found.
    q = select(CaseEvent).join(Case, (Case.id == CaseEvent.case_id) & (Case.tenant_id == CaseEvent.tenant_id))
    if actor:
        q = q.where(CaseEvent.actor == actor)
    if actor_kind:
        if actor_kind not in ("human", "agent", "system"):
            raise AuditError("Who acted is one of: human, agent, system")
        q = q.where(CaseEvent.actor_kind == actor_kind)
    if kind:
        q = q.where(CaseEvent.kind.like(f"{kind}%"))
    if case_id:
        q = q.where(CaseEvent.case_id == case_id)
    if (start := _when(date_from, "The first date")) is not None:
        q = q.where(CaseEvent.ts >= start)
    if (end := _when(date_to, "The last date", end=True)) is not None:
        q = q.where(CaseEvent.ts <= end)
    return q


def _row(e: CaseEvent) -> dict:
    return {"id": e.id, "ts": e.ts.isoformat(), "case_id": e.case_id, "actor": e.actor, "actor_kind": e.actor_kind, "kind": e.kind, "payload": e.payload}


def search(s: Session, *, actor: str | None = None, kind: str | None = None, case_id: str | None = None, date_from: str | None = None, date_to: str | None = None,
           actor_kind: str | None = None, text: str | None = None, page: int = 1, limit: int = PAGE) -> dict:
    """Events newest first. `text` looks inside what was recorded with the event, such as a note or a reason."""
    q = _query(s, actor, kind, case_id, date_from, date_to, actor_kind)
    page = max(1, page)
    if text:  # payloads are JSON, searched here so that the search means the same on every database
        needle = text.lower()
        hits = [e for e in s.scalars(q.order_by(CaseEvent.ts.desc(), CaseEvent.id.desc()).limit(EXPORT_LIMIT)) if needle in json.dumps(e.payload, default=str).lower()]
        total, rows = len(hits), hits[(page - 1) * limit: page * limit]
    else:
        total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = list(s.scalars(q.order_by(CaseEvent.ts.desc(), CaseEvent.id.desc()).offset((page - 1) * limit).limit(limit)))
    return {"total": total, "page": page, "pages": max(1, -(-total // limit)), "events": [_row(e) for e in rows]}


def facets(s: Session) -> dict:
    """What can be searched for: the kinds of event and the actors that appear on cases the asker may see."""
    base = select(CaseEvent).join(Case, (Case.id == CaseEvent.case_id) & (Case.tenant_id == CaseEvent.tenant_id)).subquery()
    kinds = s.execute(select(base.c.kind, func.count()).group_by(base.c.kind).order_by(func.count().desc())).all()
    actors = s.execute(select(base.c.actor, base.c.actor_kind, func.count()).group_by(base.c.actor, base.c.actor_kind).order_by(func.count().desc())).all()
    return {"kinds": [{"kind": k, "count": n} for k, n in kinds], "actors": [{"actor": a, "actor_kind": k, "count": n} for a, k, n in actors]}


def as_csv(s: Session, **filters) -> str:
    out = search(s, **filters, page=1, limit=EXPORT_LIMIT)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["when", "case", "who", "kind of actor", "event", "detail"])
    for e in out["events"]:
        w.writerow([e["ts"], e["case_id"], e["actor"], e["actor_kind"], e["kind"], json.dumps(e["payload"], default=str, sort_keys=True)])
    return buf.getvalue()
