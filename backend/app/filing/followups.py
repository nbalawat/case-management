"""What happens to a report after it is sent: waiting for the regulator's answer, and the continuing-activity review.

Two deadlines follow a report out of the building:

- the regulator's answer. A report still unanswered `answer_days` after it was sent (30 unless the client's filing
  profile says otherwise) is flagged on the report and its case, so someone chases it;
- for an accepted suspicious-activity report, the continuing-activity review: activity that carries on is reported
  again within CONTINUING_DAYS of the last report, so when that comes round the report and its case say so.

Both are applied once each, and are safe to apply again. On the built-in engine the periodic sweep applies them; on
Temporal each sent report has a `FilingWorkflow` holding durable timers for them (app.durable).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Filing, now
from ..visibility import system_work
from . import report as reports
from .service import CONTINUING_DAYS, SUSPICION_KINDS

ANSWER_DAYS = 30
TRACKER = "filing-tracker"
OVERDUE, CONTINUING = "answer_overdue", "continuing_review_due"


def answer_days(tenant_id: str) -> int:
    return int(reports.profile(tenant_id).get("answer_days") or ANSWER_DAYS)


def _done(f: Filing, what: str, since: datetime | None) -> bool:
    return any(h.get("what") == what and h.get("since") == (since.isoformat() if since else None) for h in f.history or [])


def deadlines(f: Filing) -> dict[str, datetime]:
    """The follow-ups still to come for this report, by kind, and when each falls due."""
    out: dict[str, datetime] = {}
    if f.status == "submitted" and f.submitted_at and not _done(f, OVERDUE, f.submitted_at):
        out[OVERDUE] = f.submitted_at + timedelta(days=answer_days(f.tenant_id))
    if f.status == "acknowledged" and f.kind in SUSPICION_KINDS and f.answered_at and not _done(f, CONTINUING, f.answered_at):
        out[CONTINUING] = f.answered_at + timedelta(days=CONTINUING_DAYS)
    return out


def _flag(s: Session, f: Filing, what: str, due: datetime, at: datetime) -> None:
    since = f.submitted_at if what == OVERDUE else f.answered_at
    detail = {"since": since.isoformat() if since else None, "due": due.isoformat()}
    f.history = [*f.history, {"at": at.isoformat(), "by": TRACKER, "what": what, **detail}]
    if f.case_id:
        from ..cases.service import record

        record(s, f.case_id, TRACKER, "system", f"filing.{what}", {"filing_id": f.id, "kind": f.kind, **detail}, ts=at)


def follow_up(s: Session, f: Filing, at: datetime | None = None) -> list[str]:
    """Apply whichever of this report's follow-ups have fallen due. Returns what was applied (nothing, the second time)."""
    at = at or now()
    done = [what for what, due in deadlines(f).items() if due <= at]
    for what in done:
        _flag(s, f, what, deadlines(f)[what], at)
    return done


@system_work
def sweep(s: Session, at: datetime | None = None) -> int:
    """Every sent report's follow-ups that are due. Part of the periodic sweep."""
    at = at or now()
    return sum(len(follow_up(s, f, at)) for f in s.scalars(select(Filing).where(Filing.status.in_(("submitted", "acknowledged")))))


def view(f: Filing | None) -> dict:
    """What a filing workflow needs: the report's status, and the next follow-up and when it falls due (None: nothing left to wait for)."""
    if f is None:
        return {"status": "gone", "next": None, "due": None}
    upcoming = sorted(deadlines(f).items(), key=lambda kv: kv[1])
    return {"status": f.status, "next": upcoming[0][0] if upcoming else None, "due": upcoming[0][1].isoformat() if upcoming else None}
