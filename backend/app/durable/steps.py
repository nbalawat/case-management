"""Case workflows as the database sees them. No Temporal in here: this is what the case workflow's activities do.

Every function opens its own transaction for one client and is safe to repeat: Temporal retries an activity whose
worker died, and a retry must never take a person's step twice.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.service import TransitionError
from ..domain.models import Case
from ..visibility import bind_viewer, find_case, system_work
from ..workflow.actions import Action, CaseView, apply, view_of

OWNED = "workflow_case"  # session.info key: this transaction is the case workflow's own, so it need not be told about it




def look(tenant_id: str, case_id: str) -> dict:
    """Where the case is now. A case that is not there (deleted, or another client's) reads as closed."""
    from ..db import session_scope

    with session_scope(tenant_id) as s:
        s.info[OWNED] = case_id
        c = _case(s, case_id)
        if c is None:
            return CaseView(case_id, "", "", None, True).as_dict() | {"gone": True}
        return view_of(c).as_dict()


@system_work
def _case(s: Session, case_id: str) -> Case | None:
    return s.scalars(select(Case).where(Case.id == case_id)).first()


def perform(tenant_id: str, case_id: str, action: dict) -> dict:
    """Take a person's step, as that person, with every check the case type declares.

    The step carries an id; the case remembers the last one it took, so a retry after the step was committed answers
    with where the case is instead of trying the step a second time. (The workflow applies one step at a time, so only
    the last can be retried.)
    """
    from ..db import session_scope
    from ..tenants.registry import runtime

    act = Action.from_dict(action)
    persona = runtime(tenant_id).policy.people.get(act.persona_id)
    if persona is None:
        raise TransitionError(f"'{act.persona_id}' is not a user of this client")
    with session_scope(tenant_id) as s:
        s.info[OWNED] = case_id
        bind_viewer(s, persona)
        c = find_case(s, case_id)
        if c is None:
            raise TransitionError("Case not found")
        if act.step_id and (c.data or {}).get("last_step") == act.step_id:
            return view_of(c).as_dict()
        view = apply(s, c, persona, act)
        if act.step_id:
            c.data = dict(c.data or {}) | {"last_step": act.step_id}
        return view.as_dict()


@system_work
def _open_ids(s: Session) -> list[str]:
    return list(s.scalars(select(Case.id).where(Case.closed.is_(None)).order_by(Case.id)))


def open_cases(tenant_id: str) -> list[str]:
    """Every open case of a client: each should have a running case workflow."""
    from ..db import session_scope

    with session_scope(tenant_id) as s:
        return _open_ids(s)


# ---- reports to a regulator ------------------------------------------------------------------------------------------

@system_work
def _filing(s: Session, filing_id: str):
    from ..domain.models import Filing

    return s.scalars(select(Filing).where(Filing.id == filing_id)).first()


def filing_look(tenant_id: str, filing_id: str) -> dict:
    """Where a sent report is, and its next follow-up. Another client's report reads as gone."""
    from ..db import session_scope
    from ..filing import followups

    with session_scope(tenant_id) as s:
        return followups.view(_filing(s, filing_id))


def filing_follow_up(tenant_id: str, filing_id: str) -> list[str]:
    """Apply the report's follow-ups that have fallen due. Safe to repeat."""
    from ..db import session_scope
    from ..filing import followups

    with session_scope(tenant_id) as s:
        f = _filing(s, filing_id)
        return followups.follow_up(s, f) if f is not None else []


@system_work
def _sent_ids(s: Session) -> list[str]:
    from ..domain.models import Filing

    return list(s.scalars(select(Filing.id).where(Filing.status.in_(("submitted", "acknowledged"))).order_by(Filing.id)))


def sent_filings(tenant_id: str) -> list[str]:
    """Every report that may still owe a follow-up: each should have a filing workflow until it has none left."""
    from ..db import session_scope
    from ..filing import followups

    with session_scope(tenant_id) as s:
        return [fid for fid in _sent_ids(s) if followups.view(_filing(s, fid))["next"]]
