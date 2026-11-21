"""Perpetual KYC: events between periodic reviews that should make someone look at the customer again.

Each kind of event is a code in the client's `kyc_triggers` list, which says whether it opens a review or is only
noted. An event fires once (its key is unique), opens a refresh case or joins the one already open, and is always
recorded on the customer.
"""
from __future__ import annotations

import contextlib
import contextvars
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..cases.case_types import load_case_types
from ..cases.service import ON_CLOSED, create_case, record
from ..domain.models import Case, Evidence, KycTrigger, new_id, now
from ..tenancy import tenant_of
from ..visibility import system_work

REVIEW_TYPES = ("kyc_refresh", "kyc_onboarding")
EXPIRY_NOTICE_DAYS = 30
_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("kyc_triggers_quiet", default=False)


@contextlib.contextmanager
def suspended():
    """History being loaded is not news: while this is open, nothing fires. Used when back-filling synthetic data."""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


@contextlib.contextmanager
def resumed():
    """Inside a suspended block, let the events of this block count: used for the few the demo is meant to show."""
    token = _quiet.set(False)
    try:
        yield
    finally:
        _quiet.reset(token)


@system_work
def fire(s: Session, party_id: str, kind: str, key: str, detail: str, at: datetime | None = None) -> KycTrigger | None:
    """Record an event for a customer and act on it as the client's list says. Returns nothing if it is switched off or already known."""
    if _quiet.get():
        return None
    rule = next((c for c in reference.codes(s, "kyc_triggers") if c["code"] == kind), None)
    if rule is None or s.scalars(select(KycTrigger.id).where(KycTrigger.key == key)).first():
        return None
    at = at or now()
    outcome, case = "noted", None
    types = load_case_types(tenant_of(s))
    if rule.get("action") == "open_review" and "kyc_refresh" in types:
        case = s.scalars(select(Case).where(Case.party_id == party_id, Case.case_type.in_(REVIEW_TYPES), Case.closed.is_(None)).order_by(Case.created)).first()
        if case is None:
            case = create_case(s, types["kyc_refresh"], party_id, "medium", created=at, actor="perpetual-kyc")
            outcome = "opened_review"
        else:
            outcome = "joined_review"
        record(s, case.id, "perpetual-kyc", "system", "kyc.trigger", {"trigger": kind, "label": rule["label"], "note": detail}, ts=at)
    row = KycTrigger(id=new_id("trg"), party_id=party_id, kind=kind, key=key[:200], detail=detail, at=at, outcome=outcome, case_id=case.id if case else None)
    s.add(row)
    s.flush()
    return row


@system_work
def scan(s: Session, at: datetime | None = None) -> int:
    """Look for events that happen by the calendar rather than by someone's action: documents expiring or expired."""
    at, fired = at or now(), 0
    for e in s.scalars(select(Evidence).where(Evidence.status == "current", Evidence.expires.is_not(None), Evidence.expires < at + timedelta(days=EXPIRY_NOTICE_DAYS))):
        label = reference.label(s, "document_types", e.kind)
        if e.expires < at:
            fired += fire(s, e.party_id, "document_expired", f"expired:{e.id}", f"{label} '{e.title}' expired on {e.expires.date().isoformat()}", at) is not None
        else:
            fired += fire(s, e.party_id, "document_expiring", f"expiring:{e.id}", f"{label} '{e.title}' expires on {e.expires.date().isoformat()}", at) is not None
    return fired


def _on_closed(s: Session, case: Case, defn: dict, actor, ts: datetime) -> None:
    """A filed report or a confirmed sanctions match is a reason to review the customer, whatever the calendar says."""
    if case.disposition == "sar_filed":
        fire(s, case.party_id, "report_filed", f"report:{case.id}", f"A suspicious activity report was filed from {case.id}", ts)
    elif case.disposition == "true_match":
        fire(s, case.party_id, "true_match", f"match:{case.id}", f"A sanctions match was confirmed on {case.id}", ts)


def install() -> None:
    if _on_closed not in ON_CLOSED:
        ON_CLOSED.append(_on_closed)


@system_work
def for_party(s: Session, party_id: str) -> list[dict]:
    labels = {c["code"]: c["label"] for c in reference.codes(s, "kyc_triggers", include_retired=True)}
    return [{"id": t.id, "kind": t.kind, "label": labels.get(t.kind, t.kind), "detail": t.detail, "at": t.at.isoformat(), "outcome": t.outcome, "case_id": t.case_id}
            for t in s.scalars(select(KycTrigger).where(KycTrigger.party_id == party_id).order_by(KycTrigger.at.desc()))]
