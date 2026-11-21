"""Customer risk rating: a score from the client's own factor list, and governed overrides.

The model is the `risk_factors` and `risk_bands` lists. Each factor has a test here that reads the customer's record
and says whether it applies and on what evidence. A person may set a different rating: raising it takes effect at
once; lowering it needs a second person who holds the approval permission. Every assessment is kept.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..agents.tools import extract
from ..detection.scenarios import HIGH_RISK_COUNTRIES
from ..domain.models import Case, Evidence, Filing, MediaItem, Party, RiskAssessment, Transaction, new_id, now
from ..domain.personas import Persona
from ..visibility import system_work
from . import ownership

APPROVE = "approve_risk"
ORDER = ["low", "medium", "high"]
HIGH_RISK_BUSINESS = ("crypto", "import-export", "import/export", "money service", "money transfer", "casino", "gambling", "jewel", "precious",
                      "arms", "art dealer", "scrap", "pawn", "minister", "shell")
CONFIRMED = {"sar_filed", "fraud_confirmed", "true_match"}
ADVERSE = {"financial_crime", "regulatory"}


class RiskError(ValueError):
    pass


@dataclass
class Context:
    """Everything the factor tests read, gathered once."""
    party: Party
    at: datetime
    structure: dict | None
    media: list
    reports: list[Filing]
    cases: list[Case]
    txns: list[Transaction]
    evidence: list[Evidence]
    identity_types: set[str] = field(default_factory=set)


Test = Callable[[Context], str | None]  # the evidence if the factor applies, otherwise nothing


def _pep(c: Context) -> str | None:
    owners = [o["name"] for o in (c.structure or {}).get("beneficial_owners", []) if o["pep"]]
    return "The customer is recorded as a PEP" if c.party.pep else f"Beneficial owner {', '.join(owners)} is a PEP" if owners else None


def _country(c: Context) -> str | None:
    places = ({c.party.country} | {o["country"] for o in (c.structure or {}).get("owners", [])}) & HIGH_RISK_COUNTRIES
    return f"Based in or owned from {', '.join(sorted(places))}" if places else None


def _business(c: Context) -> str | None:
    job = (c.party.occupation or "").lower()
    return f"Recorded as '{c.party.occupation}'" if any(w in job for w in HIGH_RISK_BUSINESS) else None


def _flag(code: str) -> Test:
    return lambda c: next((f["text"] for f in (c.structure or {}).get("flags", []) if f["code"] == code), None)


def _media(c: Context) -> str | None:
    hits = [x for x in (extract(m, c.party.name) for m in c.media) if x.names_subject and x.category in ADVERSE]
    return f"{len(hits)} item(s), most recent: {hits[0].title} ({hits[0].source}, {hits[0].published})" if hits else None


def _report(c: Context) -> str | None:
    sent = [f for f in c.reports if f.kind in ("SAR", "STR") and f.status in ("submitted", "acknowledged")]
    return f"{len(sent)} report(s) sent, most recent {max(f.filed_at for f in sent).date().isoformat()}" if sent else None


def _alerts(c: Context) -> str | None:
    recent = [x for x in c.cases if x.disposition in CONFIRMED and x.closed and x.closed >= c.at - timedelta(days=365)]
    return f"{len(recent)} case(s) confirmed in the last year: {', '.join(x.id for x in recent[:3])}" if recent else None


def _share(c: Context, pick: Callable[[Transaction], bool], floor: float, what: str) -> str | None:
    total = sum(t.amount for t in c.txns)
    if len(c.txns) < 5 or total <= 0:
        return None
    part = sum(t.amount for t in c.txns if pick(t)) / total
    return f"{part:.0%} of activity by value is {what}" if part >= floor else None


def _identity(c: Context) -> str | None:
    held = [e for e in c.evidence if e.kind in c.identity_types and e.status == "current" and e.verified_by and (e.expires is None or e.expires > c.at)]
    return None if held else "No current, verified identity document is in the evidence locker"


TESTS: dict[str, Test] = {
    "pep": _pep, "high_risk_country": _country, "high_risk_business": _business,
    "opaque_ownership": _flag("opaque"), "complex_ownership": _flag("complex"), "nominee_or_trust": _flag("nominee_or_trust"),
    "adverse_media": _media, "prior_report": _report, "confirmed_alerts": _alerts,
    "cash_intensive": lambda c: _share(c, lambda t: t.channel == "cash", 0.5, "cash"),
    "cross_border": lambda c: _share(c, lambda t: (t.counterparty_country or "US") != (c.party.country or "US"), 0.4, "to or from abroad"),
    "unverified_identity": _identity,
}


def _context(s: Session, party: Party, at: datetime) -> Context:
    view = ownership.view(s, party.id)
    return Context(
        party=party, at=at, structure=view["analysis"],
        media=list(s.scalars(select(MediaItem).where(MediaItem.party_id == party.id).order_by(MediaItem.published.desc()))),
        reports=list(s.scalars(select(Filing).where(Filing.party_id == party.id))),
        cases=list(s.scalars(select(Case).where(Case.party_id == party.id))),
        txns=list(s.scalars(select(Transaction).where(Transaction.party_id == party.id, Transaction.ts >= at - timedelta(days=365)))),
        evidence=list(s.scalars(select(Evidence).where(Evidence.party_id == party.id))),
        identity_types={c["code"] for c in reference.codes(s, "document_types", include_retired=True) if c.get("proves") == "identity"})


def bands(s: Session) -> list[dict]:
    return sorted(reference.codes(s, "risk_bands"), key=lambda b: b.get("from_score", 0))


def band_for(s: Session, score: int) -> str:
    return next(b["code"] for b in reversed(bands(s)) if score >= b.get("from_score", 0))


def _fingerprint(s: Session) -> str:
    model = [reference.codes(s, "risk_factors"), bands(s)]
    return hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()[:12]


@system_work
def score(s: Session, party: Party, at: datetime | None = None) -> dict:
    """What the model says about this customer now. Reads only; stores nothing."""
    ctx = _context(s, party, at or now())
    factors = []
    for f in reference.codes(s, "risk_factors"):
        evidence = TESTS[f["code"]](ctx) if f["code"] in TESTS else None
        factors.append({"code": f["code"], "label": f["label"], "points": int(f.get("points", 0)), "applies": evidence is not None, "evidence": evidence or ""})
    total = sum(f["points"] for f in factors if f["applies"])
    result = {"score": total, "band": band_for(s, total), "factors": factors, "model": _fingerprint(s)}
    from ..decisions.bindings import risk_band  # a client's risk band table, when enabled, has the last word

    decided = risk_band(s, party, result)
    if decided is not None:
        result |= {"score_band": result["band"], "band": decided["band"], "decision": decided,
                   "model": f"{result['model']}+{decided['table']}@{decided['version']}"}
    return result


@system_work
def current(s: Session, party_id: str) -> RiskAssessment | None:
    return s.scalars(select(RiskAssessment).where(RiskAssessment.party_id == party_id, RiskAssessment.current.is_(True))).first()


def _apply(s: Session, party: Party, a: RiskAssessment, was: str, at: datetime) -> None:
    """Make the assessment's final rating the customer's rating, and tell perpetual KYC if it went up."""
    party.risk_rating = a.final_band
    if was in ORDER and ORDER.index(a.final_band) > ORDER.index(was):
        from . import triggers

        triggers.fire(s, party.id, "risk_increased", f"risk:{a.id}:{a.final_band}", f"Risk rating went from {was} to {a.final_band} (score {a.score})", at)


@system_work
def assess(s: Session, party_id: str, by: str, at: datetime | None = None) -> RiskAssessment:
    """Score the customer and make the result their rating. An override in force stands only while the model's own answer is unchanged."""
    party = s.scalars(select(Party).where(Party.id == party_id)).first()
    if party is None:
        raise RiskError("Customer not found")
    at = at or now()
    result, before = score(s, party, at), current(s, party_id)
    a = RiskAssessment(id=new_id("risk"), party_id=party_id, at=at, by=by, score=result["score"], band=result["band"], final_band=result["band"],
                       factors=result["factors"], model=result["model"])
    if before is not None:
        before.current = False
        o = before.override
        if o and o["status"] == "applied" and before.band == result["band"]:
            a.override, a.final_band = dict(o) | {"carried_from": before.id}, o["band"]
        elif o and o["status"] == "pending":
            before.override = dict(o) | {"status": "lapsed", "decision_note": "A new assessment replaced this one before a decision was made"}
    s.add(a)
    s.flush()
    # A first assessment sets the baseline; only a later one can be said to have raised the rating.
    _apply(s, party, a, party.risk_rating if before is not None else "", at)
    return a


@system_work
def propose_override(s: Session, party_id: str, band: str, reason_code: str, note: str, persona: Persona, at: datetime | None = None) -> RiskAssessment:
    """Set a rating the model does not give. Raising applies now; lowering waits for a second person."""
    if persona.read_only or not persona.can("work"):
        raise RiskError("Overriding a rating needs someone who works cases")
    a = current(s, party_id)
    if a is None:
        raise RiskError("Assess the customer first: an override is a departure from an assessment")
    if band not in {b["code"] for b in bands(s)}:
        raise RiskError("Unknown rating")
    if band == a.band:
        raise RiskError(f"The model already rates this customer {band}")
    if reason_code not in {c["code"] for c in reference.codes(s, "risk_override_reasons")}:
        raise RiskError("Choose a reason from the client's list")
    if len(note.strip()) < 10:
        raise RiskError("Explain the override in a sentence: it will be read by audit")
    if (a.override or {}).get("status") == "pending":
        raise RiskError("An override is already waiting for approval")
    at = at or now()
    raising = ORDER.index(band) > ORDER.index(a.band)
    party = s.scalars(select(Party).where(Party.id == party_id)).one()
    a.override = {"band": band, "reason_code": reason_code, "note": note.strip()[:1000], "by": persona.id, "at": at.isoformat(),
                  "status": "applied" if raising else "pending", "decided_by": None, "decided_at": None, "decision_note": ""}
    if raising:
        was, a.final_band = a.final_band, band
        _apply(s, party, a, was, at)
    s.flush()
    return a


@system_work
def decide_override(s: Session, party_id: str, approve: bool, note: str, persona: Persona, at: datetime | None = None) -> RiskAssessment:
    """Approve or refuse a lower rating. Never the person who asked for it."""
    a = current(s, party_id)
    o = (a.override or {}) if a is not None else {}
    if o.get("status") != "pending":
        raise RiskError("No override is waiting for a decision")
    if persona.read_only or not persona.can(APPROVE):
        raise RiskError("Approving a lower rating needs the 'approve risk' permission")
    if persona.id == o["by"]:
        raise RiskError("An override cannot be approved by the person who proposed it")
    if not approve and not note.strip():
        raise RiskError("Say why the override is refused")
    at = at or now()
    a.override = dict(o) | {"status": "applied" if approve else "refused", "decided_by": persona.id, "decided_at": at.isoformat(), "decision_note": note.strip()[:1000]}
    if approve:
        a.final_band = o["band"]
        s.scalars(select(Party).where(Party.id == party_id)).one().risk_rating = o["band"]
    s.flush()
    return a


@system_work
def withdraw_override(s: Session, party_id: str, persona: Persona, note: str = "") -> RiskAssessment:
    """End an override in force and return to the model's rating."""
    a = current(s, party_id)
    o = (a.override or {}) if a is not None else {}
    if o.get("status") != "applied":
        raise RiskError("No override is in force")
    if persona.read_only or not (persona.can(APPROVE) or persona.id == o["by"]):
        raise RiskError("Only the person who set the override, or someone who approves overrides, can withdraw it")
    party = s.scalars(select(Party).where(Party.id == party_id)).one()
    a.override = dict(o) | {"status": "withdrawn", "withdrawn_by": persona.id, "decision_note": note.strip()[:1000] or o.get("decision_note", "")}
    was, a.final_band = a.final_band, a.band
    _apply(s, party, a, was, now())
    s.flush()
    return a


def _row(s: Session, a: RiskAssessment) -> dict:
    o = a.override
    return {"id": a.id, "at": a.at.isoformat(), "by": a.by, "score": a.score, "band": a.band, "final_band": a.final_band, "factors": a.factors, "model": a.model,
            "override": None if not o else o | {"reason": reference.label(s, "risk_override_reasons", o["reason_code"])}}


@system_work
def view(s: Session, party_id: str, persona: Persona) -> dict:
    party = s.scalars(select(Party).where(Party.id == party_id)).one()
    rows = list(s.scalars(select(RiskAssessment).where(RiskAssessment.party_id == party_id).order_by(RiskAssessment.at.desc(), RiskAssessment.id.desc())))
    cur = next((r for r in rows if r.current), None)
    months = {b["code"]: int(b.get("review_months", 12)) for b in bands(s)}
    pending = bool(cur and (cur.override or {}).get("status") == "pending")
    return {"rating": party.risk_rating, "assessment": _row(s, cur) if cur else None, "bands": bands(s), "review_months": months.get(party.risk_rating),
            "reasons": reference.codes(s, "risk_override_reasons"), "stale": bool(cur and cur.model != _fingerprint(s)),
            "can_assess": persona.can("work") and not persona.read_only, "can_override": persona.can("work") and not persona.read_only and cur is not None and not pending,
            "can_decide": pending and persona.can(APPROVE) and not persona.read_only and persona.id != cur.override["by"],
            "history": [{"id": r.id, "at": r.at.isoformat(), "by": r.by, "score": r.score, "band": r.band, "final_band": r.final_band,
                         "override": (r.override or {}).get("status")} for r in rows[:12]]}


@system_work
def pending(s: Session) -> list[dict]:
    """Every lower rating waiting for a second person."""
    out = []
    for a in s.scalars(select(RiskAssessment).where(RiskAssessment.current.is_(True)).order_by(RiskAssessment.at)):
        if (a.override or {}).get("status") == "pending":
            p = s.scalars(select(Party).where(Party.id == a.party_id)).one()
            out.append({"party_id": p.id, "party_name": p.name} | _row(s, a))
    return out
