"""Screening against the lists in force: rescreening after a list changes, and the decisions that stop a false match recurring.

Payments are screened by the detection scenario as they load. This module adds the other half: when a list changes,
everyone already on the books is screened again, customers by their own names and payments by their counterparties.
"""
from __future__ import annotations

import difflib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..detection.scenarios import subject_key
from ..domain.models import Alert, Party, ScreeningExemption, Watchlist, WatchlistEntry, new_id, now
from ..domain.personas import Persona
from ..entities import names
from ..visibility import system_work
from . import lists

DECIDE = "sanctions_decisions"
CUSTOMER_SCENARIO = "sanctions_customer"


class ScreeningError(ValueError):
    pass


def as_written(a: str, b: str) -> float:
    """Likeness of two names exactly as written, the measure the payment scenario uses."""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def best_match(name: str, pairs: tuple[tuple[str, str], ...], threshold: float, exempt: frozenset = frozenset(), measure=names.similarity) -> tuple[str, str, float] | None:
    """The closest list name to this one at or above the threshold: (list name, entry id, similarity)."""
    key, best = subject_key(name), None
    for listed, entry in pairs:
        if (key, entry) in exempt:
            continue
        score = measure(name, listed)
        if score >= threshold and (best is None or score > best[2]):
            best = (listed, entry, score)
    return best


@system_work
def rescreen(s: Session, at: datetime | None = None) -> dict:
    """Screen every customer and every payment against the lists as they now stand, and open work for anything new."""
    from ..alerts.hub import route_alerts
    from ..detection.engine import run_detection

    at = at or now()
    w = lists.watch(s, at)
    existing = set(s.scalars(select(Alert.dedupe_key).where(Alert.scenario == CUSTOMER_SCENARIO)))
    entry_names = {e.id: (e.name, e.ref) for e in lists.entries(s)}
    created, customers = [], 0
    for p in s.scalars(select(Party).order_by(Party.id)):
        customers += 1
        hit = best_match(p.name, w.names, w.threshold or 0.86, w.exempt)
        if hit is None:
            continue
        listed, entry, score = hit
        key = f"{CUSTOMER_SCENARIO}:{p.id}:{entry}"
        if key in existing:
            continue
        a = Alert(id=new_id("alr"), dedupe_key=key, scenario=CUSTOMER_SCENARIO, scenario_version="1.0.0", party_id=p.id, score=round(score, 2), created=at,
                  explanation=f"The customer's own name '{p.name}' matches watchlist entry '{entry_names[entry][0]}' ({entry_names[entry][1]})"
                              f"{'' if listed == entry_names[entry][0] else f' under the alias {listed!r}'} at {score:.0%} similarity.", txn_ids=[])
        s.add(a)
        created.append(a)
    s.flush()
    payment_alerts = run_detection(s, only={"sanctions_name"})
    cases = route_alerts(s, [*created, *payment_alerts])
    return {"customers_screened": customers, "entries": len(entry_names), "customer_hits": len(created), "payment_hits": len(payment_alerts),
            "cases": sorted({c.id for c in cases})}


# --- decisions that a name is not a list entry ------------------------------------------------------------------------------
def _entry(s: Session, entry_id: str) -> WatchlistEntry:
    e = s.scalars(select(WatchlistEntry).where(WatchlistEntry.id == entry_id)).first()
    if e is None or e.removed_version is not None:
        raise ScreeningError("List entry not found")
    return e


@system_work
def propose_exemption(s: Session, subject: str, entry_id: str, reason: str, persona: Persona, case_id: str | None = None, at: datetime | None = None) -> ScreeningExemption:
    """Propose that this name is not this list entry. It takes effect only when a second person agrees."""
    if persona.read_only or not persona.can("work"):
        raise ScreeningError("Proposing an exemption needs someone who works cases")
    subject = subject.strip()
    if len(subject) < 3:
        raise ScreeningError("Give the name as it was screened")
    if len(reason.strip()) < 15:
        raise ScreeningError("Say what tells this name apart from the list entry. Audit will read this.")
    e = _entry(s, entry_id)
    key = subject_key(subject)
    if s.scalars(select(ScreeningExemption.id).where(ScreeningExemption.subject_key == key, ScreeningExemption.entry_id == entry_id,
                                                     ScreeningExemption.status.in_(("pending", "active")))).first():
        raise ScreeningError("An exemption for this name and entry already exists")
    x = ScreeningExemption(id=new_id("exm"), subject=subject[:200], subject_key=key[:200], entry_id=entry_id, case_id=case_id, reason=reason.strip()[:1000],
                           proposed_by=persona.id, proposed_at=at or now(), entry_version=lists.fingerprint(e))
    s.add(x)
    s.flush()
    return x


def _exemption(s: Session, exemption_id: str) -> ScreeningExemption:
    x = s.scalars(select(ScreeningExemption).where(ScreeningExemption.id == exemption_id)).first()
    if x is None:
        raise ScreeningError("Exemption not found")
    return x


@system_work
def decide_exemption(s: Session, exemption_id: str, approve: bool, note: str, persona: Persona, at: datetime | None = None) -> ScreeningExemption:
    if persona.read_only or not persona.can(DECIDE):
        raise ScreeningError("Deciding an exemption needs the permission to decide sanctions matters")
    x = _exemption(s, exemption_id)
    if x.status != "pending":
        raise ScreeningError("This exemption has already been decided")
    if x.proposed_by == persona.id:
        raise ScreeningError("An exemption is decided by someone other than the person who proposed it")
    if not approve and not note.strip():
        raise ScreeningError("Say why it is refused")
    at = at or now()
    if approve and lists.fingerprint(_entry(s, x.entry_id)) != x.entry_version:
        raise ScreeningError("The list entry has changed since this was proposed. Propose it again against the entry as it now stands.")
    months = float(reference.setting(s, "screening_rules", "exemption_months", "value", 12))
    x.status, x.decided_by, x.decided_at, x.decision_note = ("active" if approve else "refused"), persona.id, at, note.strip()[:1000]
    x.expires = at + timedelta(days=round(months * 30.4375)) if approve else None
    s.flush()
    return x


@system_work
def revoke_exemption(s: Session, exemption_id: str, note: str, persona: Persona) -> ScreeningExemption:
    if persona.read_only or not persona.can(DECIDE):
        raise ScreeningError("Ending an exemption needs the permission to decide sanctions matters")
    x = _exemption(s, exemption_id)
    if x.status != "active":
        raise ScreeningError("Only an exemption in force can be ended")
    if not note.strip():
        raise ScreeningError("Say why it is ended")
    x.status, x.decided_at = "revoked", now()
    x.decision_note = f"{x.decision_note} Ended by {persona.id}: {note.strip()[:500]}".strip()
    s.flush()
    return x


@system_work
def exemptions(s: Session, at: datetime | None = None) -> list[dict]:
    at = at or now()
    entry = {e.id: e for e in s.scalars(select(WatchlistEntry))}
    lists_ = {w.id: w.name for w in s.scalars(select(Watchlist))}
    out = []
    for x in s.scalars(select(ScreeningExemption).order_by(ScreeningExemption.proposed_at.desc(), ScreeningExemption.id)):
        e = entry.get(x.entry_id)
        lapsed = x.status == "active" and x.expires is not None and x.expires <= at
        out.append({"id": x.id, "subject": x.subject, "entry_id": x.entry_id, "entry_name": e.name if e else "", "entry_ref": e.ref if e else "",
                    "list": lists_.get(e.list_id, "") if e else "", "case_id": x.case_id, "reason": x.reason, "status": "expired" if lapsed else x.status,
                    "proposed_by": x.proposed_by, "proposed_at": x.proposed_at.isoformat(), "decided_by": x.decided_by,
                    "decided_at": x.decided_at.isoformat() if x.decided_at else None, "decision_note": x.decision_note,
                    "expires": x.expires.date().isoformat() if x.expires else None})
    return out


@system_work
def candidates(s: Session, case_id: str) -> list[dict]:
    """The name-and-entry pairs behind a case's sanctions alerts: what an exemption on this case could cover."""
    from ..domain.models import Transaction

    w = lists.watch(s)
    entry = {e.id: e for e in lists.entries(s)}
    threshold = w.threshold or 0.86
    seen, out = set(), []
    for a in s.scalars(select(Alert).where(Alert.case_id == case_id, Alert.scenario.in_(("sanctions_name", CUSTOMER_SCENARIO)))):
        subjects = [s.scalars(select(Party.name).where(Party.id == a.party_id)).first()] if a.scenario == CUSTOMER_SCENARIO else \
            [t.counterparty for t in s.scalars(select(Transaction).where(Transaction.id.in_(a.txn_ids or [""])))]
        for subject in filter(None, subjects):
            hit = best_match(subject, w.names, threshold, measure=names.similarity if a.scenario == CUSTOMER_SCENARIO else as_written)
            if hit and (subject_key(subject), hit[1]) not in seen:
                seen.add((subject_key(subject), hit[1]))
                e = entry[hit[1]]
                out.append({"subject": subject, "entry_id": e.id, "entry_name": e.name, "entry_ref": e.ref, "score": round(hit[2], 2),
                            "countries": e.countries, "programs": e.programs, "aliases": e.aliases})
    return out
