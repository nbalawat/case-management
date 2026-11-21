"""One page of cases: filtered, sorted and counted in the database, for lists that must stay fast at volume.

The list on screen never loads every case. It asks for one page, sorted and filtered by columns the database can
index, plus facet counts so the filters can say how many cases each choice would leave. Everything runs through
the caller's own session, so the client boundary and per-person visibility apply exactly as everywhere else.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Case, Party, now

PRIORITY_RANK = case({"critical": 0, "high": 1, "medium": 2, "low": 3}, value=Case.priority, else_=4)
# Sort keys the database can order by. The stage clock depends on each workflow's settings, so it is shown, not sorted.
SORTS = {
    "priority": PRIORITY_RANK, "created": Case.created, "deadline": Case.sla_due, "customer": Party.name,
    "stage": Case.state, "type": Case.case_type, "owner": Case.owner, "queue": Case.queue, "stage_entered": Case.state_entered,
}
STATUSES = ("open", "closed", "all")
DUES = ("overdue", "week")  # past the regulatory deadline, or due within seven days (overdue included)
PERIOD = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")  # a day or a month, as dashboards drill into them
MAX_PAGE = 200
MAX_EXPORT = 50_000


@dataclass(frozen=True)
class CaseQuery:
    status: str = "open"
    case_types: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()
    queues: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    owner: str | None = None  # a person id, "me" (resolved by the caller) or "unassigned"
    text: str = ""
    # Drill-through filters, set by dashboards: a deadline window, alert scenarios, and the day or month a case
    # was opened or closed.
    due: str | None = None
    scenarios: tuple[str, ...] = ()
    opened: str = ""
    closed_in: str = ""
    sort: tuple[tuple[str, bool], ...] = field(default=(("priority", False), ("deadline", False)))  # (key, descending)
    page: int = 1
    size: int = 50

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        unknown = [k for k, _ in self.sort if k not in SORTS]
        if unknown:
            raise ValueError(f"cannot sort by {', '.join(unknown)}; use {', '.join(SORTS)}")
        if self.due and self.due not in DUES:
            raise ValueError(f"due must be one of {', '.join(DUES)}")
        for name in ("opened", "closed_in"):
            if getattr(self, name) and not PERIOD.match(getattr(self, name)):
                raise ValueError(f"{name} is a day (2026-11-21) or a month (2026-11)")
        if self.page < 1 or not 1 <= self.size <= MAX_PAGE:
            raise ValueError(f"page starts at 1 and size is between 1 and {MAX_PAGE}")


def parse_sort(raw: str | None) -> tuple[tuple[str, bool], ...]:
    """'-created,priority' -> (('created', True), ('priority', False)). Empty means the default order."""
    if not raw:
        return CaseQuery().sort
    return tuple((k.lstrip("-"), k.startswith("-")) for k in raw.split(",") if k.strip())


def period(value: str) -> tuple[datetime, datetime]:
    """'2026-11-21' -> that day; '2026-11' -> that month. Half-open, so the end is the next period's start."""
    if len(value) == 7:
        start = datetime.strptime(value, "%Y-%m")
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, end
    start = datetime.strptime(value, "%Y-%m-%d")
    return start, start + timedelta(days=1)


def _base(q: CaseQuery, skip: str | None = None) -> Select:
    """Every filter except the one named by skip, so a facet counts what choosing each of its values would give."""
    stmt = select(Case).join(Case.party)
    if q.status == "open":
        stmt = stmt.where(Case.closed.is_(None))
    elif q.status == "closed":
        stmt = stmt.where(Case.closed.is_not(None))
    filters = {"case_type": (Case.case_type, q.case_types), "priority": (Case.priority, q.priorities),
               "queue": (Case.queue, q.queues), "state": (Case.state, q.states)}
    for name, (col, values) in filters.items():
        if values and name != skip:
            stmt = stmt.where(col.in_(values))
    if q.owner and skip != "owner":
        stmt = stmt.where(Case.owner.is_(None) if q.owner == "unassigned" else Case.owner == q.owner)
    if q.due:
        today = now()
        stmt = stmt.where(Case.sla_due.is_not(None), (Case.sla_due < today) if q.due == "overdue" else (Case.sla_due <= today + timedelta(days=7)))
    if q.scenarios:
        stmt = stmt.where(Case.id.in_(select(Alert.case_id).where(Alert.scenario.in_(q.scenarios))))
    if q.opened:
        a, b = period(q.opened)
        stmt = stmt.where(Case.created >= a, Case.created < b)
    if q.closed_in:
        a, b = period(q.closed_in)
        stmt = stmt.where(Case.closed >= a, Case.closed < b)
    if q.text.strip():
        like = f"%{q.text.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Party.name).like(like), func.lower(Case.id).like(like), func.lower(Case.party_id).like(like)))
    return stmt


def _ordered(stmt: Select, q: CaseQuery) -> Select:
    order = []
    for key, desc in q.sort:
        col = SORTS[key]
        order.append(col.desc().nulls_last() if desc else col.asc().nulls_last())
    return stmt.order_by(*order, Case.id)  # the id makes the order total, so pages never overlap


def page(s: Session, q: CaseQuery) -> tuple[list[Case], int, dict[str, int]]:
    """The cases on one page, the total that match, and how many alerts each case on the page carries."""
    stmt = _base(q)
    total = s.scalar(select(func.count()).select_from(stmt.with_only_columns(Case.id).subquery())) or 0
    rows = list(s.scalars(_ordered(stmt, q).offset((q.page - 1) * q.size).limit(q.size)))
    ids = [c.id for c in rows]
    alerts = dict(s.execute(select(Alert.case_id, func.count()).where(Alert.case_id.in_(ids)).group_by(Alert.case_id)).all()) if ids else {}
    return rows, total, alerts


def facets(s: Session, q: CaseQuery) -> dict[str, dict[str, int]]:
    """For each filter, how many cases each value would show with the other filters as they are."""
    out: dict[str, dict[str, int]] = {}
    for name, col in (("case_type", Case.case_type), ("priority", Case.priority), ("queue", Case.queue), ("state", Case.state)):
        sub = _base(q, skip=name).with_only_columns(col.label("v")).subquery()
        out[name] = {v: n for v, n in s.execute(select(sub.c.v, func.count()).group_by(sub.c.v)).all() if v is not None}
    sub = _base(q, skip="owner").with_only_columns(Case.owner.label("v")).subquery()
    owners = dict(s.execute(select(sub.c.v, func.count()).group_by(sub.c.v)).all())
    out["owner"] = {("unassigned" if k is None else k): n for k, n in owners.items()}
    return out


def export_csv(s: Session, q: CaseQuery, row) -> tuple[str, int]:
    """Every matching case (up to MAX_EXPORT) as CSV, in the list's order. `row` turns a case into a dict."""
    cases = list(s.scalars(_ordered(_base(q), q).limit(MAX_EXPORT)))
    cols = ["id", "party_name", "party_id", "type_title", "state_label", "priority", "owner", "queue_label", "created", "sla_due",
            "stage_due", "agent_recommendation", "disposition", "restricted"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for c in cases:
        w.writerow(row(c))
    return buf.getvalue(), len(cases)
