"""Case and customer visibility in the data layer.

A request's session is bound to the person making it. From then on every ORM read of a case, of anything that hangs
off a case (its events, briefings, requests, filings, alerts, agent ledger rows), of a customer, and of anything that
belongs to a customer (accounts, transactions, documents, assessments, holds, disputes) is filtered to what that
person may see, without the caller asking. What a person may not see does not exist for them: lists omit it, counts
exclude it, and its id returns "not found".

The rules combine:
  * what each of the person's roles sees (all cases, their queues, or their own; optionally only some case types),
    always including cases the person owns or has acted on;
  * which customers each role reaches by attribute: booking jurisdiction, segment, a relationship manager's own book.
    A case is visible only if its customer is, and acting on a case does not lift this;
  * tipping-off: customer-facing people never see the case types where suspicion is investigated, nor any report;
  * restricted cases, which need the view_restricted permission whatever the roles see;
  * break-glass grants: a live grant opens one case (and its customer) or one customer, whatever the rules above say,
    except that a restricted case opens only if the client's policy lets break-glass reach restricted cases.

Platform work done on someone's behalf (routing an alert to an existing case, an agent reading a customer's history)
must see everything, or it would open duplicates and brief from half a record. That work runs inside `system(...)`.
"""
from __future__ import annotations

import functools
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import and_, event, false, func, or_, select, true
from sqlalchemy.orm import Session, with_loader_criteria

from .domain.access import RESTRICTED_VIEW, Customers, Persona, Sees

VIEWER_KEY, SUSPENDED_KEY = "viewer", "viewer_suspended"


@dataclass(frozen=True)
class Viewer:
    id: str
    queues: tuple[str, ...]
    views: tuple[Sees, ...] | None  # None: sees every case that is not restricted
    sees_restricted: bool
    customers: tuple[Customers, ...] | None = None  # None: every customer
    customer_facing: bool = False
    confidential: frozenset[str] = frozenset()  # case types hidden from customer-facing people
    hide_filings: bool = False  # reports hidden from this viewer (customer-facing, where the client says so)
    glass_restricted: bool = False  # break-glass grants may open restricted cases

    @property
    def unlimited(self) -> bool:
        return self.views is None and self.sees_restricted and self.customers is None and not self.customer_facing


def viewer_of(p: Persona, policy=None) -> Viewer:
    """What this person may see. `policy` is the client's access policy, for its tipping-off and break-glass terms."""
    tip, glass = (policy.tipping_off, policy.break_glass) if policy is not None else (None, None)
    facing = p.customer_facing
    return Viewer(p.id, tuple(p.queues), p.views, p.can(RESTRICTED_VIEW), p.customers, facing,
                  tip.case_types if (tip and facing) else frozenset(), bool(tip and tip.filings and facing), bool(glass and glass.restricted))


def bind_viewer(session: Session, persona: Persona) -> Session:
    from .tenancy import TENANT_KEY
    from .tenants.registry import UnknownTenant, runtime

    try:
        policy = runtime(session.info.get(TENANT_KEY)).policy
    except UnknownTenant:
        policy = None
    session.info[VIEWER_KEY] = viewer_of(persona, policy)
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


def _tables(tenant: str | None, at=None):
    """Plain aliases of the tables the rules read, and the live grants of a viewer. Read unfiltered on purpose."""
    from .domain.models import AccessGrant, Case, CaseEvent, Party, now

    case, events, party, grant = (Case.__table__.alias("vis_case"), CaseEvent.__table__.alias("vis_event"),
                                  Party.__table__.alias("vis_party"), AccessGrant.__table__.alias("vis_grant"))
    return case, events, party, grant, (at or now()), tenant


def _scoped(q, table, tenant):
    return q.where(table.c.tenant_id == tenant) if tenant else q


def granted(v: Viewer, grant, kind: str, tenant: str | None, at):
    """Targets of this viewer's live break-glass grants of one kind."""
    q = select(grant.c.target).where(grant.c.person == v.id, grant.c.kind == kind, grant.c.ends > at, grant.c.closed.is_(None))
    return _scoped(q, grant, tenant)


def party_attr_clause(v: Viewer, party):
    """'This viewer's roles reach this customer by its attributes.' Grants are added by the callers."""
    if v.customers is None:
        return true()
    where = func.coalesce(party.c.booking_jurisdiction, party.c.country)
    options = []
    for c in v.customers:
        parts = []
        if c.jurisdictions is not None:
            parts.append(where.in_(sorted(c.jurisdictions)) if c.jurisdictions else false())
        if c.segments is not None:
            parts.append(party.c.segment.in_(sorted(c.segments)) if c.segments else false())
        if c.book:
            parts.append(party.c.relationship_manager == v.id)
        options.append(and_(*parts) if parts else true())
    return or_(*options)


def case_clause(v: Viewer, case, events, party=None, grant=None, tenant: str | None = None, at=None):
    """The SQL condition for 'this viewer may see this case'. `case` and `events` are the two tables (or aliases of them);
    `party` and `grant` are needed once the viewer has attribute rules or may hold break-glass grants."""
    mine = or_(case.c.owner == v.id, case.c.id.in_(select(events.c.case_id).where(events.c.actor == v.id, events.c.actor_kind == "human")))
    if v.views is None:
        allowed = true()
    else:
        options = []
        for view in v.views:
            where = {"all": true(), "queues": case.c.queue.in_(v.queues) if v.queues else false(), "own": false()}[view.scope]
            options.append(where if view.case_types is None else and_(where, case.c.case_type.in_(sorted(view.case_types))))
        allowed = or_(mine, *options)
    if v.customers is not None and party is not None:
        allowed = and_(allowed, case.c.party_id.in_(_scoped(select(party.c.id).where(party_attr_clause(v, party)), party, tenant)))
    if v.confidential:
        allowed = and_(allowed, case.c.case_type.not_in(sorted(v.confidential)))
    if not v.sees_restricted:
        allowed = and_(allowed, case.c.restricted.is_(False))
    return or_(allowed, opened_clause(v, case, grant, tenant, at)) if grant is not None else allowed


def opened_clause(v: Viewer, case, grant, tenant: str | None, at):
    """'A live break-glass grant opens this case for this viewer.' A grant never opens a restricted case unless the viewer
    could see restricted cases anyway or the client lets break-glass reach them, and never defeats tipping-off."""
    opened = case.c.id.in_(granted(v, grant, "case", tenant, at))
    if not (v.sees_restricted or v.glass_restricted):
        opened = and_(opened, case.c.restricted.is_(False))
    if v.confidential:
        opened = and_(opened, case.c.case_type.not_in(sorted(v.confidential)))
    return opened


def party_clause(v: Viewer, party, case, grant, tenant: str | None, at):
    """'This viewer may see this customer': their roles reach it, or a live grant opens it or one of its cases."""
    if v.customers is None:
        return true()
    via_case = _scoped(select(case.c.party_id).where(opened_clause(v, case, grant, tenant, at)), case, tenant)
    return or_(party_attr_clause(v, party), party.c.id.in_(granted(v, grant, "customer", tenant, at)), party.c.id.in_(via_case))


def reaches(v: Viewer, party) -> bool:
    """party_attr_clause in Python, for a customer already in hand."""
    if v.customers is None:
        return True
    where = party.booking_jurisdiction or party.country
    return any((c.jurisdictions is None or where in c.jurisdictions) and (c.segments is None or party.segment in c.segments)
               and (not c.book or party.relationship_manager == v.id) for c in v.customers)


def can_see(v: Viewer, case, acted: bool = False, granted_cases: frozenset[str] = frozenset(), party=None) -> bool:
    """The same rule in Python, for a case already in hand. `acted`: the viewer has an event on the case. `party`: the
    case's customer, when the viewer has attribute rules (defaults to case.party)."""
    if v.confidential and case.case_type in v.confidential:
        return False
    if opens(v, case, granted_cases):
        return True
    if case.restricted and not v.sees_restricted:
        return False
    if v.customers is not None and not reaches(v, party if party is not None else case.party):
        return False
    if v.views is None or case.owner == v.id or acted:
        return True
    return any((view.scope == "all" or (view.scope == "queues" and case.queue in v.queues))
               and (view.case_types is None or case.case_type in view.case_types) for view in v.views)


def opens(v: Viewer, case, granted_cases: frozenset[str]) -> bool:
    """opened_clause in Python."""
    return (case.id in granted_cases and (not case.restricted or v.sees_restricted or v.glass_restricted)
            and case.case_type not in v.confidential)


def can_see_party(v: Viewer, party, granted_customers: frozenset[str] = frozenset(), opened_cases=()) -> bool:
    """`opened_cases`: cases of this customer that a live grant opens for the viewer (see `opens`)."""
    return party.id in granted_customers or reaches(v, party) or any(c.party_id == party.id for c in opened_cases)


def find_case(session: Session, case_id: str | None):
    """The case with this id if the session's viewer may see it, else None.

    Use this, not session.get: get answers from objects the session already holds, which platform work done earlier
    in the same request may have loaded.
    """
    from .domain.models import Case

    return session.scalars(select(Case).where(Case.id == case_id)).first() if case_id else None


def visible_ids(session: Session, v: Viewer):
    """A subquery of the case ids this viewer may see. It reads the plain tables, so it is not itself filtered."""
    from .tenancy import TENANT_KEY

    case, events, party, grant, at, tenant = _tables(session.info.get(TENANT_KEY))
    return _scoped(select(case.c.id).where(case_clause(v, case, events, party, grant, tenant, at)), case, tenant)


def visible_party_ids(session: Session, v: Viewer):
    """A subquery of the customer ids this viewer may see. For code that joins customers by hand, such as Ask."""
    from .tenancy import TENANT_KEY

    case, _events, party, grant, at, tenant = _tables(session.info.get(TENANT_KEY))
    return _scoped(select(party.c.id).where(party_clause(v, party, case, grant, tenant, at)), party, tenant)


def viewer(session: Session) -> Viewer | None:
    return session.info.get(VIEWER_KEY)


def _party_keyed():
    """Every table whose rows belong to one customer, found from the model, so a new one is covered without a change here."""
    from .db import Base
    from .domain.models import Party

    return [mp.class_ for mp in Base.registry.mappers if mp.class_ is not Party and "party_id" in mp.class_.__table__.c]


@event.listens_for(Session, "do_orm_execute")
def _filter_by_viewer(state) -> None:
    session = state.session
    v: Viewer | None = session.info.get(VIEWER_KEY)
    if v is None or v.unlimited or session.info.get(SUSPENDED_KEY) or not (state.is_select or state.is_update or state.is_delete):
        return
    from .domain.models import AgentRun, AgentStep, Alert, Case, CaseEvent, Filing, InfoRequest, Party

    visible = visible_ids(session, v)
    # Lambdas, not fixed expressions: SQLAlchemy then adapts each criterion to the entity it is applied to, so a query
    # that joins a table under an alias (aliased(Case)) is filtered on the alias instead of failing.
    track = {"include_aliases": True, "track_closure_variables": True}
    opts = [with_loader_criteria(Case, lambda cls: cls.id.in_(visible), **track)]
    for child in (CaseEvent, AgentRun, InfoRequest):
        opts.append(with_loader_criteria(child, lambda cls: cls.case_id.in_(visible), **track))
    for loose in (Alert, AgentStep, Filing):  # rows that may belong to no case at all
        opts.append(with_loader_criteria(loose, lambda cls: or_(cls.case_id.is_(None), cls.case_id.in_(visible)), **track))
    if v.hide_filings:
        opts.append(with_loader_criteria(Filing, lambda cls: false(), **track))
    if v.customers is not None:
        parties = visible_party_ids(session, v)
        opts.append(with_loader_criteria(Party, lambda cls: cls.id.in_(parties), **track))
        for model in _party_keyed():
            if model.__table__.c.party_id.nullable or model is Filing:  # rows about no customer stay as they were
                opts.append(with_loader_criteria(
                    model, lambda cls: or_(cls.party_id.is_(None), cls.party_id == "", cls.party_id.in_(parties)), **track))
            else:
                opts.append(with_loader_criteria(model, lambda cls: cls.party_id.in_(parties), **track))
    state.statement = state.statement.options(*opts)
