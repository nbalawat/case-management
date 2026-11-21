"""Disputes: a customer's claim that payments were not theirs, followed to the end.

Three things run in parallel once a claim is made, each with its own clock set by how the payment was made: the
customer gets their money back while the bank looks into it (provisional credit), the bank tries to recover the money
from the other side (a chargeback on a card, a return on ACH, a recall request on a wire), and the claim is resolved.
What was credited and not recovered is the bank's loss.

Recovery outlives the case: a claim is refunded and closed in days, while a chargeback can take months. So a dispute
can be opened and worked on a case that closed with the fraud confirmed.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..cases.service import record
from ..domain.models import Case, Dispute, Transaction, new_id, now
from ..domain.personas import Persona
from ..visibility import system_work
from ..workdays import add_business_days

RECOVERY_NAME = {"card": "chargeback", "ach": "ACH return", "wire": "recall request", "p2p": "recall request"}
OUTCOMES = ("recovered", "partly_recovered", "written_off", "customer_liable", "withdrawn")


class DisputeError(ValueError):
    pass


def _worker(persona: Persona) -> None:
    if persona.read_only or not persona.can("work"):
        raise DisputeError("This needs someone who works cases")


def _note(s: Session, d: Dispute, by: str, what: str, note: str = "", amount: float | None = None, at: datetime | None = None) -> None:
    at = at or now()
    d.history = [*d.history, {"at": at.isoformat(), "by": by, "what": what, "note": note, "amount": amount}]
    record(s, d.case_id, by, "system" if by == "timers" else "human", f"dispute.{what}", {"dispute_id": d.id, "note": note, "amount": amount}, ts=at)


def _get(s: Session, dispute_id: str) -> Dispute:
    d = s.scalars(select(Dispute).where(Dispute.id == dispute_id)).first()
    if d is None:
        raise DisputeError("Dispute not found")
    return d


def _open(d: Dispute) -> None:
    if d.status == "closed":
        raise DisputeError("The dispute is closed")


@system_work
def open_dispute(s: Session, case: Case, txn_ids: list[str], reason_code: str, persona: Persona, reported_at: datetime | None = None, note: str = "") -> Dispute:
    _worker(persona)
    if case.closed is not None and case.disposition != "fraud_confirmed":
        raise DisputeError("The case is closed without fraud being confirmed, so there is nothing to recover")
    if not any(c["code"] == reason_code for c in reference.codes(s, "dispute_reasons")):
        raise DisputeError("Choose a reason this client offers for a dispute")
    txns = list(s.scalars(select(Transaction).where(Transaction.id.in_(txn_ids or [""]), Transaction.party_id == case.party_id).order_by(Transaction.ts)))
    if not txns or len(txns) != len(set(txn_ids)):
        raise DisputeError("Choose payments that belong to this customer")
    if any(t.direction != "out" for t in txns):
        raise DisputeError("Only money that left the account can be disputed")
    rails = {t.channel for t in txns}
    if len(rails) > 1:
        raise DisputeError(f"One dispute covers one kind of payment. These are {', '.join(sorted(rails))}: open one for each.")
    rail = rails.pop()
    rule = next((c for c in reference.codes(s, "dispute_rules") if c["code"] == rail), None)
    if rule is None:
        raise DisputeError(f"{rail.capitalize()} payments cannot be disputed under this client's rules")
    taken = {t for d in s.scalars(select(Dispute).where(Dispute.party_id == case.party_id, Dispute.outcome.is_distinct_from("withdrawn"))) for t in d.txn_ids}
    if taken & set(txn_ids):
        raise DisputeError("One of these payments is already in a dispute")
    at = now()
    reported = reported_at or at
    if reported > at + timedelta(minutes=1):
        raise DisputeError("The customer cannot have reported it in the future")
    credit_days, recovery_days = int(rule.get("credit_days") or 0), int(rule.get("recovery_days") or 0)
    d = Dispute(id=new_id("dsp"), case_id=case.id, party_id=case.party_id, txn_ids=[t.id for t in txns], amount=round(sum(t.amount for t in txns), 2), rail=rail,
                reason_code=reason_code, rule=rail, reported_at=reported, opened_by=persona.id,
                credit_due=add_business_days(reported, credit_days) if credit_days else None,
                resolve_due=reported + timedelta(days=int(rule.get("resolve_days") or 45)),
                recovery_due=txns[0].ts + timedelta(days=recovery_days) if recovery_days else None, history=[])
    s.add(d)
    s.flush()
    _note(s, d, persona.id, "opened", note.strip()[:500], d.amount, reported if reported_at else at)
    return d


@system_work
def credit(s: Session, dispute_id: str, amount: float, persona: Persona, final: bool = False, note: str = "", at: datetime | None = None) -> Dispute:
    """Give the customer their money back: provisionally while the claim is open, or finally."""
    _worker(persona)
    d = _get(s, dispute_id)
    _open(d)
    if d.credit_final:
        raise DisputeError("The credit is already final")
    if not 0 < amount <= d.amount + 0.005:
        raise DisputeError(f"The credit is between a cent and the disputed ${d.amount:,.2f}")
    at = at or now()
    d.credit_amount, d.credit_at, d.credit_final = round(amount, 2), d.credit_at or at, final
    if d.status == "open":
        d.status = "credited"
    _note(s, d, persona.id, "credit_made_final" if final else "provisional_credit", note.strip()[:500], d.credit_amount, at)
    s.flush()
    return d


@system_work
def start_recovery(s: Session, dispute_id: str, reference_no: str, persona: Persona, note: str = "", at: datetime | None = None) -> Dispute:
    """Record the chargeback, return or recall sent to the other side."""
    _worker(persona)
    d = _get(s, dispute_id)
    _open(d)
    at = at or now()
    if d.recovery_at is not None:
        raise DisputeError(f"A {RECOVERY_NAME[d.rail]} is already on record")
    if d.recovery_due is not None and at > d.recovery_due:
        raise DisputeError(f"The window for a {RECOVERY_NAME[d.rail]} closed on {d.recovery_due.date().isoformat()}")
    if not reference_no.strip():
        raise DisputeError("Give the reference the network or the other bank assigned")
    d.recovery_ref, d.recovery_at, d.status = reference_no.strip()[:60], at, "recovery"
    _note(s, d, persona.id, "recovery_started", f"{RECOVERY_NAME[d.rail].capitalize()} {d.recovery_ref}. {note.strip()[:400]}".strip(), None, at)
    s.flush()
    return d


@system_work
def record_recovery(s: Session, dispute_id: str, amount: float, persona: Persona, note: str = "", at: datetime | None = None) -> Dispute:
    """Money came back from the other side. Recorded as the running total."""
    _worker(persona)
    d = _get(s, dispute_id)
    _open(d)
    if d.recovery_at is None:
        raise DisputeError(f"Record the {RECOVERY_NAME[d.rail]} before recording money back from it")
    if not 0 < amount <= d.amount - d.recovered + 0.005:
        raise DisputeError(f"At most ${d.amount - d.recovered:,.2f} is still to recover")
    d.recovered = round(d.recovered + amount, 2)
    _note(s, d, persona.id, "recovered", note.strip()[:500], round(amount, 2), at)
    s.flush()
    return d


@system_work
def close(s: Session, dispute_id: str, persona: Persona, outcome: str | None = None, note: str = "", at: datetime | None = None) -> Dispute:
    """Resolve the claim. Without an outcome it follows the money: recovered in full, in part, or written off."""
    _worker(persona)
    d = _get(s, dispute_id)
    _open(d)
    if outcome is not None and outcome not in ("customer_liable", "withdrawn"):
        raise DisputeError("Leave the outcome to the figures, or choose: customer liable, or withdrawn")
    if outcome and len(note.strip()) < 10:
        raise DisputeError("Say why. The customer is entitled to the reason, and audit will read it.")
    at = at or now()
    if outcome in ("customer_liable", "withdrawn"):
        d.customer_liability = d.amount if outcome == "customer_liable" else 0.0
        if d.credit_amount:  # the provisional credit is taken back
            _note(s, d, persona.id, "credit_reversed", "", d.credit_amount, at)
            d.credit_amount = 0.0
        d.loss = 0.0
    else:
        if not d.credit_amount:
            raise DisputeError("The customer has not been credited. Credit them, or close the claim as the customer's liability with the reason.")
        outcome = "recovered" if d.recovered >= d.credit_amount - 0.005 else "partly_recovered" if d.recovered > 0 else "written_off"
        d.loss = round(max(0.0, d.credit_amount - d.recovered), 2)
    d.outcome, d.status, d.closed_at, d.credit_final = outcome, "closed", at, True
    _note(s, d, persona.id, "closed", f"{outcome.replace('_', ' ')}. {note.strip()[:400]}".strip(), d.loss, at)
    s.flush()
    return d


@system_work
def sweep(s: Session, at: datetime | None = None) -> int:
    """Flag, once each, a credit the customer should have had by now and a claim past its resolution date."""
    at, n = at or now(), 0
    for d in s.scalars(select(Dispute).where(Dispute.status != "closed")):
        flagged = {h["what"] for h in d.history}
        if d.credit_due and d.credit_at is None and d.credit_due < at and "credit_overdue" not in flagged:
            _note(s, d, "timers", "credit_overdue", f"The customer was owed a provisional credit by {d.credit_due.date().isoformat()}", d.amount, at)
            n += 1
        if d.resolve_due and d.resolve_due < at and "resolution_overdue" not in flagged:
            _note(s, d, "timers", "resolution_overdue", f"The claim was to be resolved by {d.resolve_due.date().isoformat()}", None, at)
            n += 1
    s.flush()
    return n


def row(s: Session, d: Dispute, at: datetime | None = None) -> dict:
    at = at or now()
    iso = lambda x: x.isoformat() if x else None  # noqa: E731
    open_ = d.status != "closed"
    return {"id": d.id, "case_id": d.case_id, "party_id": d.party_id, "txn_ids": d.txn_ids, "amount": d.amount, "rail": d.rail, "reason_code": d.reason_code,
            "reason": reference.label(s, "dispute_reasons", d.reason_code), "status": d.status, "outcome": d.outcome, "reported_at": iso(d.reported_at),
            "opened_by": d.opened_by, "credit_due": iso(d.credit_due), "credit_at": iso(d.credit_at), "credit_amount": d.credit_amount, "credit_final": d.credit_final,
            "credit_overdue": bool(open_ and d.credit_due and d.credit_at is None and d.credit_due < at),
            "credit_late": bool(d.credit_due and d.credit_at and d.credit_at > d.credit_due),
            "resolve_due": iso(d.resolve_due), "resolve_overdue": bool(open_ and d.resolve_due and d.resolve_due < at),
            "recovery_name": RECOVERY_NAME[d.rail], "recovery_ref": d.recovery_ref, "recovery_at": iso(d.recovery_at), "recovery_due": iso(d.recovery_due),
            "recovery_open": bool(open_ and d.recovery_at is None and (d.recovery_due is None or d.recovery_due >= at)),
            "recovery_by_request": d.recovery_due is None, "recovered": d.recovered, "customer_liability": d.customer_liability, "loss": d.loss,
            "exposure": round(max(0.0, d.credit_amount - d.recovered), 2) if open_ else 0.0, "closed_at": iso(d.closed_at), "history": d.history}


@system_work
def for_case(s: Session, case: Case, persona: Persona) -> dict:
    rows = [row(s, d) for d in s.scalars(select(Dispute).where(Dispute.case_id == case.id).order_by(Dispute.reported_at.desc(), Dispute.id))]
    taken = {t for r in rows if r["outcome"] != "withdrawn" for t in r["txn_ids"]}
    rails = {c["code"] for c in reference.codes(s, "dispute_rules")}
    payments = [{"id": t.id, "ts": t.ts.isoformat(), "amount": t.amount, "channel": t.channel, "counterparty": t.counterparty, "disputed": t.id in taken}
                for t in s.scalars(select(Transaction).where(Transaction.party_id == case.party_id, Transaction.direction == "out",
                                                             Transaction.channel.in_(rails)).order_by(Transaction.ts.desc()).limit(40))]
    return {"disputes": rows, "can_work": persona.can("work") and not persona.read_only, "can_open": case.closed is None or case.disposition == "fraud_confirmed",
            "reasons": reference.codes(s, "dispute_reasons"), "rules": reference.codes(s, "dispute_rules"), "payments": payments}


@system_work
def all_rows(s: Session, at: datetime | None = None) -> list[dict]:
    return [row(s, d, at) for d in s.scalars(select(Dispute).order_by(Dispute.reported_at.desc(), Dispute.id))]


def summary(rows: list[dict]) -> dict:
    """Totals for the fraud desk, from the disputes a person may see."""
    open_ = [r for r in rows if r["status"] != "closed"]
    closed = [r for r in rows if r["status"] == "closed"]
    by_reason: dict[str, dict] = {}
    for r in rows:
        b = by_reason.setdefault(r["reason"], {"reason": r["reason"], "claims": 0, "claimed": 0.0, "recovered": 0.0, "loss": 0.0})
        b["claims"] += 1
        b["claimed"] = round(b["claimed"] + r["amount"], 2)
        b["recovered"] = round(b["recovered"] + r["recovered"], 2)
        b["loss"] = round(b["loss"] + r["loss"], 2)
    credited = sum(r["credit_amount"] for r in closed if r["outcome"] in ("recovered", "partly_recovered", "written_off"))
    return {"open": len(open_), "open_amount": round(sum(r["amount"] for r in open_), 2), "exposure": round(sum(r["exposure"] for r in open_), 2),
            "credit_overdue": sum(1 for r in open_ if r["credit_overdue"]), "resolve_overdue": sum(1 for r in open_ if r["resolve_overdue"]),
            "recovery_open": sum(1 for r in open_ if r["recovery_open"] and not r["recovery_by_request"]),
            "closed": len(closed), "loss": round(sum(r["loss"] for r in closed), 2), "recovered": round(sum(r["recovered"] for r in rows), 2),
            "recovery_rate": round(sum(r["recovered"] for r in closed) / credited, 4) if credited else None,
            "by_reason": sorted(by_reason.values(), key=lambda b: -b["claimed"])}

