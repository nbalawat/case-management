"""Tells the workflow engine about cases that were opened or moved outside it.

Cases are opened by the alert hub, pipelines, perpetual KYC and the QA sampler, and moved by merges and QA reopening
them; none of those is a person's step through the engine. After each commit, the engine hears which cases changed
stage, opened or closed, and which reports to a regulator were sent or answered, and an engine that keeps state of its
own (Temporal) reads them again. The built-in engine
keeps none, so for it this does nothing at all.
"""
from __future__ import annotations

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from ..domain.models import Case, Filing

KEY, FILINGS = "workflow_changed", "workflow_filings"
WATCHED = ("state", "state_entered", "closed")
OWNED = "workflow_case"  # app.durable.steps marks the case workflow's own transactions with the case they are for


def _moved(c: Case) -> bool:
    state = inspect(c)
    return any(state.attrs[a].history.has_changes() for a in WATCHED)


@event.listens_for(Session, "before_flush")
def _collect(session: Session, _ctx, _instances) -> None:
    from .engine import engine

    if engine().in_process:
        return
    from ..tenancy import TENANT_KEY

    owned = session.info.get(OWNED)
    tenant = session.info.get(TENANT_KEY)
    changed = session.info.setdefault(KEY, set())
    for obj in session.new:
        if isinstance(obj, Case):
            changed.add((obj.tenant_id or tenant, obj.id))
    for obj in session.dirty:
        if isinstance(obj, Case) and obj.id != owned and _moved(obj):
            changed.add((obj.tenant_id or tenant, obj.id))
    filings = session.info.setdefault(FILINGS, set())
    for obj in (*session.new, *session.dirty):  # a report sent, answered or replaced: its follow-ups change
        if isinstance(obj, Filing) and obj.id != owned and inspect(obj).attrs["status"].history.has_changes() and obj.status != "prepared":
            filings.add((obj.tenant_id or tenant, obj.id))


def _by_tenant(pairs: set[tuple[str, str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for tenant_id, oid in sorted(pairs):
        if tenant_id:
            out.setdefault(tenant_id, []).append(oid)
    return out


@event.listens_for(Session, "after_commit")
def _tell(session: Session) -> None:
    cases, filings = session.info.pop(KEY, None), session.info.pop(FILINGS, None)
    if not cases and not filings:
        return
    from .engine import engine

    for tenant_id, ids in _by_tenant(cases or set()).items():
        engine().observe(tenant_id, ids)
    for tenant_id, ids in _by_tenant(filings or set()).items():
        engine().observe_filings(tenant_id, ids)


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop(KEY, None)
    session.info.pop(FILINGS, None)
