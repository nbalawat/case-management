"""The KYC desk: who is due a review, what happened between reviews, and which decisions are waiting."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import reference
from ..cases.case_types import load_case_types
from ..cases.service import create_case, record
from ..domain.models import Case, Evidence, KycTrigger, OwnershipStructure, Party, now
from ..domain.personas import Persona
from ..tenancy import tenant_of
from ..visibility import system_work
from . import evidence, ownership, risk, triggers

REVIEWED = {"refreshed", "onboarded"}
SOON_DAYS = 60


class DeskError(ValueError):
    pass


def _add_months(d: datetime, months: int) -> datetime:
    return d + timedelta(days=round(months * 30.4375))


@system_work
def schedule(s: Session, at: datetime | None = None) -> dict:
    """Every customer's next periodic review, from their rating and when they were last reviewed."""
    at = at or now()
    months = {b["code"]: int(b.get("review_months", 12)) for b in risk.bands(s)}
    last = dict(s.execute(select(Case.party_id, func.max(Case.closed)).where(Case.case_type.in_(triggers.REVIEW_TYPES), Case.disposition.in_(REVIEWED))
                          .group_by(Case.party_id)).all())
    open_reviews = dict(s.execute(select(Case.party_id, func.min(Case.id)).where(Case.case_type.in_(triggers.REVIEW_TYPES), Case.closed.is_(None))
                                  .group_by(Case.party_id)).all())
    rows, counts = [], {"overdue": 0, "due_soon": 0, "in_review": 0, "customers": 0}
    for p in s.scalars(select(Party)):
        counts["customers"] += 1
        since = last.get(p.id) or p.onboarded
        due = _add_months(since, months.get(p.risk_rating, 36))
        state = "in_review" if p.id in open_reviews else "overdue" if due < at else "due_soon" if due < at + timedelta(days=SOON_DAYS) else None
        if state is None:
            continue
        counts[state] += 1
        rows.append({"party_id": p.id, "name": p.name, "risk_rating": p.risk_rating, "pep": p.pep, "last_review": since.date().isoformat(),
                     "reviewed_before": p.id in last, "due": due.date().isoformat(), "state": state, "case_id": open_reviews.get(p.id)})
    order = {"overdue": 0, "due_soon": 1, "in_review": 2}
    rank = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (order[r["state"]], rank.get(r["risk_rating"], 3), r["due"]))
    return {"counts": counts, "rows": rows[:150], "more": max(0, len(rows) - 150), "cycle": months}


@system_work
def overview(s: Session, persona: Persona, at: datetime | None = None) -> dict:
    at = at or now()
    names = dict(s.execute(select(Party.id, Party.name)).all())
    labels = {c["code"]: c["label"] for c in reference.codes(s, "kyc_triggers", include_retired=True)}
    recent = [{"id": t.id, "party_id": t.party_id, "party_name": names.get(t.party_id, t.party_id), "kind": t.kind, "label": labels.get(t.kind, t.kind),
               "detail": t.detail, "at": t.at.isoformat(), "outcome": t.outcome, "case_id": t.case_id}
              for t in s.scalars(select(KycTrigger).order_by(KycTrigger.at.desc(), KycTrigger.id).limit(40))]
    docs = [evidence.row(s, e, at) | {"party_name": names.get(e.party_id, e.party_id)} for e in
            s.scalars(select(Evidence).where(Evidence.status == "current", Evidence.expires.is_not(None),
                                             Evidence.expires < at + timedelta(days=triggers.EXPIRY_NOTICE_DAYS)).order_by(Evidence.expires))]
    structures = []
    threshold, layers = ownership._rules(s)
    for o in s.scalars(select(OwnershipStructure).where(OwnershipStructure.current.is_(True)).order_by(OwnershipStructure.recorded_at.desc())):
        a = ownership.analyse(o.nodes, o.links, threshold, layers)
        structures.append({"party_id": o.party_id, "party_name": names.get(o.party_id, o.party_id), "version": o.version, "recorded_at": o.recorded_at.isoformat(),
                           "beneficial_owners": [b["name"] for b in a["beneficial_owners"]], "unidentified": a["unidentified"], "flags": a["flags"]})
    return {"schedule": schedule(s, at), "triggers": recent, "pending_overrides": risk.pending(s), "documents": docs, "structures": structures,
            "can_start": persona.can("work") and not persona.read_only and "kyc_refresh" in load_case_types(tenant_of(s)),
            "can_approve": persona.can(risk.APPROVE) and not persona.read_only}


@system_work
def start_review(s: Session, party_id: str, persona: Persona, why: str = "") -> Case:
    """Open a periodic review for a customer who has none open."""
    if persona.read_only or not persona.can("work"):
        raise DeskError("Starting a review needs someone who works cases")
    types = load_case_types(tenant_of(s))
    if "kyc_refresh" not in types:
        raise DeskError("This client has no KYC refresh workflow")
    party = s.scalars(select(Party).where(Party.id == party_id)).first()
    if party is None:
        raise DeskError("Customer not found")
    if s.scalars(select(Case.id).where(Case.party_id == party_id, Case.case_type.in_(triggers.REVIEW_TYPES), Case.closed.is_(None))).first():
        raise DeskError("A review is already open for this customer")
    case = create_case(s, types["kyc_refresh"], party_id, "medium" if party.risk_rating == "high" else "low", actor=persona.id)
    record(s, case.id, persona.id, "human", "kyc.review_started", {"note": why.strip()[:300] or "Periodic review started from the KYC desk"})
    s.flush()
    return case
