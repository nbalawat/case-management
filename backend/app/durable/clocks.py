"""Stage clocks as the database sees them. No Temporal in here: this is what the activities do."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.service import definition, stage_due
from ..cases.timers import breach_case, run_timers
from ..domain.models import Case, now
from ..visibility import system_work


@dataclass(frozen=True)
class Clock:
    """One running deadline: a case, the moment it entered its stage, and when that stage is due."""

    case_id: str
    entered: str  # ISO-8601; names the stage visit, so a clock for an earlier visit can tell it is out of date
    due: str


@system_work
def due_clocks(s: Session) -> list[Clock]:
    """Every open case whose current stage has a deadline that has not yet been acted on."""
    out = []
    for c in s.scalars(select(Case).where(Case.closed.is_(None)).order_by(Case.id)):
        due = stage_due(c, definition(c))
        if due is None or (c.data or {}).get("stage_breach") == c.state_entered.isoformat():
            continue
        out.append(Clock(c.id, c.state_entered.isoformat(), due.isoformat()))
    return out


@system_work
def _fire(s: Session, case_id: str, entered: str, at: datetime) -> str:
    c = s.get(Case, case_id)
    if c is None:
        return "gone"
    if c.closed is not None:
        return "closed"
    if c.state_entered.isoformat() != entered:
        return "moved_on"  # the case left that stage; its new stage has its own clock
    if (c.data or {}).get("stage_breach") == entered:
        return "already"  # the sweep got there first, or this is a retry
    return "breached" if breach_case(s, c, at) else "not_due"


def fire(tenant_id: str, case_id: str, entered: str, at: datetime | None = None) -> str:
    """A clock has run out: apply the escalation, if the case is still in that visit to that stage. Safe to repeat."""
    from ..db import session_scope

    with session_scope(tenant_id) as s:
        return _fire(s, case_id, entered, at or now())


def clocks_by_client() -> dict[str, list[Clock]]:
    """The deadlines that should be running, for every live client."""
    from ..db import session_scope
    from ..tenants import registry

    out = {}
    for rt in registry.all_runtimes():
        if rt.status != "live":
            continue
        with session_scope(rt.id) as s:
            out[rt.id] = due_clocks(s)
    return out


def sweep_one(tenant_id: str) -> dict:
    """The periodic sweep for one client, in one transaction."""
    from ..db import session_scope

    with session_scope(tenant_id) as s:
        return run_timers(s)


def sweep_all() -> dict[str, dict]:
    """The whole periodic sweep for every live client, one transaction per client."""
    from ..db import session_scope
    from ..tenants import registry

    out = {}
    for rt in registry.all_runtimes():
        if rt.status != "live":
            continue
        with session_scope(rt.id) as s:
            out[rt.id] = run_timers(s)
    return out
