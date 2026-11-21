"""Stage clocks. A sweep finds breaches and applies the escalation a case type declares.

The sweep runs on demand and when work lists load. On Temporal (app/durable) every open case's workflow also holds a
durable timer, so a breach fires at the moment it happens whether or not anyone is looking.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Case, InfoRequest, now
from .service import definition, record, stage_due
from ..visibility import system_work

PRIORITIES = ["low", "medium", "high", "critical"]


def breach_case(s: Session, c: Case, at: datetime) -> bool:
    """Apply a case's escalation if its stage clock has run out and this has not been done for this stage yet.

    Safe to call twice, from the sweep and from a durable timer alike: the stage it was applied to is marked on the case.
    """
    if c.closed is not None:
        return False
    defn = definition(c)
    due = stage_due(c, defn)
    if due is None or due > at or (c.data or {}).get("stage_breach") == c.state_entered.isoformat():
        return False
    esc = defn.get("escalation", {})
    before = c.priority
    if esc.get("raise_priority", True) and c.priority != "critical":
        c.priority = PRIORITIES[PRIORITIES.index(c.priority) + 1]
    c.data = dict(c.data or {}) | {"stage_breach": c.state_entered.isoformat()}
    record(s, c.id, "timers", "system", "sla.stage_breached",
           {"stage": c.state, "due": due.isoformat(), "priority_from": before, "priority_to": c.priority,
            "notify": esc.get("notify", "team_lead")}, ts=at)
    return True


@system_work
def run_timers(s: Session, at: datetime | None = None) -> dict:
    at = at or now()
    overdue = 0
    breached = sum(breach_case(s, c, at) for c in s.scalars(select(Case).where(Case.closed.is_(None))))
    for r in s.scalars(select(InfoRequest).where(InfoRequest.status == "open", InfoRequest.due < at)):
        c = s.get(Case, r.case_id)
        flagged = set((c.data or {}).get("requests_overdue", []))
        if r.id in flagged:
            continue
        c.data = dict(c.data or {}) | {"requests_overdue": sorted(flagged | {r.id})}
        record(s, c.id, "timers", "system", "request.overdue", {"request_id": r.id, "recipient": r.recipient, "due": r.due.isoformat()}, ts=at)
        overdue += 1
    s.flush()
    from ..alerts.hub import sweep_hibernated

    from ..kyc import triggers  # here, not at the top: perpetual KYC opens cases, so it imports this package

    from .. import holds
    from ..filing import followups
    from ..fraud import disputes

    return {"stage_breaches": breached, "requests_overdue": overdue, "alerts_expired": sweep_hibernated(s, at), "kyc_triggers": triggers.scan(s, at),
            "holds_overdue": holds.sweep(s, at), "dispute_deadlines": disputes.sweep(s, at), "filing_follow_ups": followups.sweep(s, at),
            "at": at.isoformat()}
