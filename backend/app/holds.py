"""Holds: money or access stopped while a case is decided.

A hold belongs to a case and names what is stopped: one payment, an account, a card, or online access. It ends in one
of three ways: released (let go), blocked (kept, as sanctions law requires for property of a listed party) or rejected
(the payment is sent back). Where the client's reason says so, one person proposes the outcome and a second decides it.
A step-up check asks the customer to prove who they are before a fraud hold is released.

This module records the decision. Telling the payment system to stop or let go is an integration: see `on_change`.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import reference
from .cases.service import CLOSE_GUARDS, record
from .domain.models import Account, Case, Hold, Transaction, new_id, now
from .domain.personas import Persona
from .visibility import system_work

RELEASE = "sanctions_decisions"
TARGETS = {"payment": "Payment", "account": "Account", "card": "Card", "online_access": "Online access"}
OUTCOMES = {"release": "released", "block": "blocked", "reject": "rejected"}
METHODS = {"one_time_code": "One-time code to the phone on file", "call_back": "Call back on the number on file", "in_branch": "Identity checked in a branch"}
# Whoever needs to act on a hold (a payment engine, a card processor) subscribes here. Nothing subscribes in this build.
on_change: list[Callable[[Session, Hold, str], None]] = []


class HoldError(ValueError):
    pass


def _note(s: Session, h: Hold, by: str, what: str, note: str = "", at: datetime | None = None, **detail: object) -> None:
    at = at or now()
    h.history = [*h.history, {"at": at.isoformat(), "by": by, "what": what, "note": note}]
    record(s, h.case_id, by, "human" if by != "timers" else "system", f"hold.{what}",
           {"hold_id": h.id, "target": h.target, "target_id": h.target_id, "note": note, **detail}, ts=at)
    for fn in on_change:
        fn(s, h, what)


def _worker(persona: Persona) -> None:
    if persona.read_only or not persona.can("work"):
        raise HoldError("This needs someone who works cases")


def _reason(s: Session, code: str) -> dict:
    r = next((c for c in reference.codes(s, "hold_reasons") if c["code"] == code), None)
    if r is None:
        raise HoldError("Choose a reason this client offers for a hold")
    return r


def _reason_of(s: Session, h: Hold) -> dict:
    return next((c for c in reference.codes(s, "hold_reasons", include_retired=True) if c["code"] == h.reason_code), {})


def _two_person(s: Session, h: Hold) -> bool:
    return str(_reason_of(s, h).get("two_person", "no")).lower() == "yes"


def kind(s: Session, h: Hold) -> str:
    """sanctions, fraud or legal, as the client's reason says. A sanctions hold that is kept must be reported."""
    return str(_reason_of(s, h).get("kind") or "fraud").lower()


def _get(s: Session, hold_id: str) -> Hold:
    h = s.scalars(select(Hold).where(Hold.id == hold_id)).first()
    if h is None:
        raise HoldError("Hold not found")
    return h


@system_work
def place(s: Session, case: Case, target: str, target_id: str, reason_code: str, persona: Persona, note: str = "", at: datetime | None = None) -> Hold:
    _worker(persona)
    if case.closed is not None:
        raise HoldError("The case is closed")
    if target not in TARGETS:
        raise HoldError(f"A hold is on one of: {', '.join(TARGETS.values()).lower()}")
    reason = _reason(s, reason_code)
    amount, target_id = 0.0, (target_id or "").strip()
    if target == "payment":
        t = s.scalars(select(Transaction).where(Transaction.id == target_id, Transaction.party_id == case.party_id)).first()
        if t is None:
            raise HoldError("That payment is not one of this customer's")
        amount = t.amount
    else:
        accounts = list(s.scalars(select(Account.id).where(Account.party_id == case.party_id)))
        if target == "account" and target_id not in accounts:
            raise HoldError("That account is not one of this customer's")
        if target != "account" and target_id and target_id not in accounts:
            raise HoldError("Name one of this customer's accounts, or leave it empty to cover them all")
    if s.scalars(select(Hold.id).where(Hold.party_id == case.party_id, Hold.target == target, Hold.target_id == target_id, Hold.status == "active")).first():
        raise HoldError("That is already on hold")
    at = at or now()
    h = Hold(id=new_id("hld"), case_id=case.id, party_id=case.party_id, target=target, target_id=target_id, amount=amount, reason_code=reason_code,
             note=note.strip()[:1000], placed_by=persona.id, placed_at=at, review_by=at + timedelta(hours=int(reason.get("review_hours") or 24)), history=[])
    s.add(h)
    s.flush()
    _note(s, h, persona.id, "placed", note.strip(), at, reason=reason["label"], amount=amount)
    return h


@system_work
def challenge(s: Session, hold_id: str, method: str, persona: Persona, at: datetime | None = None) -> Hold:
    """Ask the customer to prove who they are before the hold is decided."""
    _worker(persona)
    h = _get(s, hold_id)
    if h.status != "active":
        raise HoldError("The hold is no longer active")
    if method not in METHODS:
        raise HoldError(f"A step-up check is one of: {', '.join(METHODS.values()).lower()}")
    if (h.challenge or {}).get("status") == "sent":
        raise HoldError("A step-up check is already waiting for the customer")
    at = at or now()
    h.challenge = {"method": method, "status": "sent", "requested_by": persona.id, "requested_at": at.isoformat(), "completed_at": None, "note": ""}
    _note(s, h, persona.id, "challenge_sent", METHODS[method], at)
    s.flush()
    return h


@system_work
def challenge_result(s: Session, hold_id: str, passed: bool, persona: Persona, note: str = "", at: datetime | None = None) -> Hold:
    _worker(persona)
    h = _get(s, hold_id)
    if (h.challenge or {}).get("status") != "sent":
        raise HoldError("No step-up check is waiting")
    at = at or now()
    h.challenge = h.challenge | {"status": "passed" if passed else "failed", "completed_at": at.isoformat(), "note": note.strip()[:300], "recorded_by": persona.id}
    _note(s, h, persona.id, "challenge_passed" if passed else "challenge_failed", note.strip(), at)
    s.flush()
    return h


def _finish(s: Session, h: Hold, outcome: str, by: str, note: str, at: datetime) -> None:
    h.status, h.decided_by, h.decided_at, h.decision_note, h.proposal = OUTCOMES[outcome], by, at, note, None
    _note(s, h, by, OUTCOMES[outcome], note, at)


@system_work
def decide(s: Session, hold_id: str, outcome: str, note: str, persona: Persona, at: datetime | None = None) -> Hold:
    """End a hold, or propose its end where two people are needed. Returns the hold; `proposal` is set while it waits."""
    _worker(persona)
    h = _get(s, hold_id)
    if h.status != "active":
        raise HoldError("The hold is no longer active")
    if outcome not in OUTCOMES:
        raise HoldError("A hold ends released, blocked or rejected")
    if outcome == "reject" and h.target != "payment":
        raise HoldError("Only a payment can be rejected and sent back")
    note = note.strip()
    if len(note) < 10:
        raise HoldError("Say why. Audit will read this.")
    if outcome == "release" and (h.challenge or {}).get("status") == "failed":
        raise HoldError("The customer failed the step-up check, so this cannot be released. Send a new check, or block it.")
    if outcome == "release" and (h.challenge or {}).get("status") == "sent":
        raise HoldError("A step-up check is still waiting for the customer")
    at = at or now()
    if not _two_person(s, h):
        _finish(s, h, outcome, persona.id, note, at)
    elif h.proposal is None:
        h.proposal = {"outcome": outcome, "note": note, "by": persona.id, "at": at.isoformat()}
        _note(s, h, persona.id, "proposed", note, at, outcome=outcome)
    else:
        raise HoldError("An outcome is already proposed and waiting for a second person")
    s.flush()
    return h


@system_work
def decide_proposal(s: Session, hold_id: str, approve: bool, note: str, persona: Persona, at: datetime | None = None) -> Hold:
    if persona.read_only or not persona.can(RELEASE):
        raise HoldError("Deciding a proposed outcome needs the permission to decide sanctions matters")
    h = _get(s, hold_id)
    if h.status != "active" or h.proposal is None:
        raise HoldError("Nothing is waiting for a decision on this hold")
    if h.proposal["by"] == persona.id:
        raise HoldError("The outcome is decided by someone other than the person who proposed it")
    at = at or now()
    if approve:
        _finish(s, h, h.proposal["outcome"], persona.id, f"{h.proposal['note']} Approved by a second person{': ' + note.strip() if note.strip() else ''}.", at)
    else:
        if not note.strip():
            raise HoldError("Say why the proposal is refused")
        h.proposal = None
        _note(s, h, persona.id, "proposal_refused", note.strip(), at)
    s.flush()
    return h


@system_work
def sweep(s: Session, at: datetime | None = None) -> int:
    """Flag every hold that has sat past its review time, once, and raise the case's priority."""
    at, n = at or now(), 0
    for h in s.scalars(select(Hold).where(Hold.status == "active", Hold.overdue_flagged.is_(False), Hold.review_by < at)):
        h.overdue_flagged = True
        case = s.scalars(select(Case).where(Case.id == h.case_id)).first()
        if case is not None and case.closed is None and case.priority != "critical":
            case.priority = {"low": "medium", "medium": "high", "high": "critical"}[case.priority]
        _note(s, h, "timers", "review_overdue", f"Not decided by {h.review_by.isoformat(timespec='minutes')}", at)
        n += 1
    s.flush()
    return n


def close_guard(s: Session, case: Case) -> str | None:
    """A case cannot close while it still holds something: every hold is decided first."""
    n = len(list(s.scalars(select(Hold.id).where(Hold.case_id == case.id, Hold.status == "active"))))
    return f"{n} hold{'s are' if n != 1 else ' is'} still active on this case. Release, block or reject {'them' if n != 1 else 'it'} first." if n else None


def row(s: Session, h: Hold, at: datetime | None = None) -> dict:
    at = at or now()
    return {"id": h.id, "case_id": h.case_id, "party_id": h.party_id, "target": h.target, "target_label": TARGETS[h.target], "target_id": h.target_id,
            "amount": h.amount, "reason_code": h.reason_code, "reason": reference.label(s, "hold_reasons", h.reason_code), "kind": kind(s, h), "note": h.note, "status": h.status,
            "placed_by": h.placed_by, "placed_at": h.placed_at.isoformat(), "review_by": h.review_by.isoformat() if h.review_by else None,
            "overdue": h.status == "active" and h.review_by is not None and h.review_by < at, "two_person": _two_person(s, h),
            "challenge": (h.challenge | {"method_label": METHODS.get(h.challenge["method"], h.challenge["method"])}) if h.challenge else None,
            "proposal": h.proposal, "decided_by": h.decided_by, "decided_at": h.decided_at.isoformat() if h.decided_at else None,
            "decision_note": h.decision_note, "history": h.history}


@system_work
def for_case(s: Session, case: Case, persona: Persona) -> dict:
    rows = [row(s, h) for h in s.scalars(select(Hold).where(Hold.case_id == case.id).order_by(Hold.placed_at.desc(), Hold.id))]
    worker = persona.can("work") and not persona.read_only
    return {"holds": rows, "can_place": worker and case.closed is None, "can_work": worker, "can_decide": persona.can(RELEASE) and not persona.read_only,
            "reasons": reference.codes(s, "hold_reasons"), "targets": [{"id": k, "label": v} for k, v in TARGETS.items()],
            "methods": [{"id": k, "label": v} for k, v in METHODS.items()],
            "accounts": list(s.scalars(select(Account.id).where(Account.party_id == case.party_id).order_by(Account.id)))}


@system_work
def active(s: Session, at: datetime | None = None) -> list[dict]:
    """Every hold in force for this client, most urgent first."""
    return sorted((row(s, h, at) for h in s.scalars(select(Hold).where(Hold.status == "active"))), key=lambda r: r["review_by"] or "")


if close_guard not in CLOSE_GUARDS:
    CLOSE_GUARDS.append(close_guard)
