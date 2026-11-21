"""Case visibility in the data layer.

A request's session is bound to the person making it. From then on every ORM read of a case, or of anything that
hangs off a case (its events, briefings, requests, filings, alerts, agent ledger rows), is filtered to what that
person may see, without the caller asking. A case a person may not see does not exist for them: lists omit it,
counts exclude it, and its id returns "not found".

Two rules combine:
  * what each of the person's roles sees (all cases, their queues, or their own; optionally only some case types),
    always including cases the person owns or has acted on;
  * restricted cases, which need the view_restricted permission whatever the roles see.

Platform work done on someone's behalf (routing an alert to an existing case, an agent reading a customer's history)
must see everything, or it would open duplicates and brief from half a record. That work runs inside `system(...)`.
"""
from __future__ import annotations

import functools
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import and_, event, false, or_, select, true
from sqlalchemy.orm import Session, with_loader_criteria

from .domain.access import RESTRICTED_VIEW, Persona, Sees

VIEWER_KEY, SUSPENDED_KEY = "viewer", "viewer_suspended"


@dataclass(frozen=True)
class Viewer:
    id: str
    queues: tuple[str, ...]
    views: tuple[Sees, ...] | None  # None: sees every case that is not restricted
    sees_restricted: bool

    @property
    def unlimited(self) -> bool:
        return self.views is None and self.sees_restricted


def viewer_of(p: Persona) -> Viewer:
    return Viewer(p.id, tuple(p.queues), p.views, p.can(RESTRICTED_VIEW))


def bind_viewer(session: Session, persona: Persona) -> Session:
    session.info[VIEWER_KEY] = viewer_of(persona)
    return session


@contextmanager
def system(session: Session):
    """Do platform work that must see every case of the client, whoever asked for it."""
    before = session.info.get(SUSPENDED_KEY, 0)
    session.info[SUSPENDED_KEY] = before + 1
    try:
        yield session
    finally:
        session.info[SUSPENDED_KEY] = before


def system_work(fn):
    """Marks a function whose first argument is the session as platform work."""
    @functools.wraps(fn)
    def inner(s, *args, **kw):
        with system(s):
            return fn(s, *args, **kw)
    return inner


def case_clause(v: Viewer, case, events):
    """The SQL condition for 'this viewer may see this case'. `case` and `events` are the two tables (or aliases of them)."""
    mine = or_(case.c.owner == v.id, case.c.id.in_(select(events.c.case_id).where(events.c.actor == v.id, events.c.actor_kind == "human")))
    if v.views is None:
        allowed = true()
    else:
        options = []
        for view in v.views:
            where = {"all": true(), "queues": case.c.queue.in_(v.queues) if v.queues else false(), "own": false()}[view.scope]
            options.append(where if view.case_types is None else and_(where, case.c.case_type.in_(sorted(view.case_types))))
        allowed = or_(mine, *options)
    return allowed if v.sees_restricted else and_(allowed, case.c.restricted.is_(False))


def can_see(v: Viewer, case, acted: bool = False) -> bool:
    """The same rule in Python, for a case already in hand. `acted`: the viewer has an event on the case."""
    if case.restricted and not v.sees_restricted:
        return False
    if v.views is None or case.owner == v.id or acted:
        return True
    return any((view.scope == "all" or (view.scope == "queues" and case.queue in v.queues))
               and (view.case_types is None or case.case_type in view.case_types) for view in v.views)


def find_case(session: Session, case_id: str | None):
    """The case with this id if the session's viewer may see it, else None.

    Use this, not session.get: get answers from objects the session already holds, which platform work done earlier
    in the same request may have loaded.
    """
    from .domain.models import Case

    return session.scalars(select(Case).where(Case.id == case_id)).first() if case_id else None


def visible_ids(session: Session, v: Viewer):
    """A subquery of the case ids this viewer may see. It reads the plain tables, so it is not itself filtered."""
    from .domain.models import Case, CaseEvent
    from .tenancy import TENANT_KEY

    case, events = Case.__table__.alias("vis_case"), CaseEvent.__table__.alias("vis_event")
    q = select(case.c.id).where(case_clause(v, case, events))
    tenant = session.info.get(TENANT_KEY)
    return q.where(case.c.tenant_id == tenant) if tenant else q


@event.listens_for(Session, "do_orm_execute")
def _filter_by_viewer(state) -> None:
    session = state.session
    v: Viewer | None = session.info.get(VIEWER_KEY)
    if v is None or v.unlimited or session.info.get(SUSPENDED_KEY) or not (state.is_select or state.is_update or state.is_delete):
        return
    from .domain.models import AgentRun, AgentStep, Alert, Case, CaseEvent, Filing, InfoRequest

    visible = visible_ids(session, v)
    opts = [with_loader_criteria(Case, Case.id.in_(visible), include_aliases=True)]
    for child in (CaseEvent, AgentRun, InfoRequest, Filing):
        opts.append(with_loader_criteria(child, child.case_id.in_(visible), include_aliases=True))
    for loose in (Alert, AgentStep):  # rows that may belong to no case at all
        opts.append(with_loader_criteria(loose, or_(loose.case_id.is_(None), loose.case_id.in_(visible)), include_aliases=True))
    state.statement = state.statement.options(*opts)
