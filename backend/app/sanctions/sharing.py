"""Searches for law enforcement: a list of named subjects arrives, the client searches its records and answers.

In the United States this is section 314(a): FinCEN sends a subject list, the institution searches accounts held in
the last twelve months and transactions in the last six, and reports any match within fourteen days. The request and
the search are confidential: only the people the client names may see them, and a match here opens no case and tips
off nobody. This module keeps the request, what was searched, each possible match and who decided it, and the answer.

Customers have a name and a country on this platform, so the comparison is by name. Dates of birth and identity
numbers on a request are shown to the person deciding; they are not matched automatically.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..domain.models import InfoShare, Party, Transaction, new_id, now
from ..domain.personas import Persona
from ..entities import names

HANDLE = "info_sharing"
MAX_SUBJECTS = 2000


class SharingError(ValueError):
    pass


def _may(persona: Persona, write: bool = True) -> None:
    if not persona.can(HANDLE) or (write and persona.read_only):
        raise SharingError("Law enforcement requests are confidential. Handling them needs the information sharing permission.")


def _rule(s: Session, code: str, default: float) -> float:
    return float(reference.setting(s, "info_share_rules", code, "value", default))


def _get(s: Session, share_id: str) -> InfoShare:
    r = s.scalars(select(InfoShare).where(InfoShare.id == share_id)).first()
    if r is None:
        raise SharingError("Request not found")
    return r


def _subjects(rows: list) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise SharingError("A request names at least one subject")
    if len(rows) > MAX_SUBJECTS:
        raise SharingError(f"A request can hold at most {MAX_SUBJECTS:,} subjects")
    out = []
    for i, r in enumerate(rows, 1):
        name = str((r or {}).get("name") or "").strip() if isinstance(r, dict) else ""
        if not name:
            raise SharingError(f"Subject {i} has no name")
        aliases = r.get("aliases") or []
        aliases = [a.strip() for a in (aliases if isinstance(aliases, list) else str(aliases).split(";")) if str(a).strip()]
        out.append({"no": i, "name": name[:200], "kind": "business" if str(r.get("kind") or "").lower().startswith("b") else "person", "aliases": aliases[:10],
                    "dob": str(r.get("dob") or "")[:10], "identifiers": str(r.get("identifiers") or "")[:120], "country": str(r.get("country") or "").upper()[:2]})
    return out


def receive(s: Session, reference_no: str, subjects: list, persona: Persona, received: datetime | None = None) -> InfoShare:
    _may(persona)
    reference_no = reference_no.strip()
    if not reference_no:
        raise SharingError("Give the tracking number on the request")
    if s.scalars(select(InfoShare.id).where(InfoShare.reference == reference_no)).first():
        raise SharingError("A request with that tracking number is already on record")
    received = received or now()
    r = InfoShare(id=new_id("lea"), reference=reference_no[:60], received=received, due=received + timedelta(days=int(_rule(s, "response_days", 14))),
                  loaded_by=persona.id, subjects=_subjects(subjects), matches=[], scope={})
    s.add(r)
    s.flush()
    return r


def search(s: Session, share_id: str, persona: Persona, at: datetime | None = None) -> InfoShare:
    """Search customers and recent payment counterparties for every subject. Decisions already made on a match are kept."""
    _may(persona)
    r = _get(s, share_id)
    if r.status in ("responded", "no_match"):
        raise SharingError("This request has been answered")
    at = at or now()
    threshold = _rule(s, "match_at", 0.9)
    txn_since = at - timedelta(days=round(_rule(s, "transaction_months", 6) * 30.4375))
    # Every customer the client holds, whoever is searching: the duty is the institution's, not one team's. Customers and
    # payments are not narrowed by case visibility, so these queries already see them all.
    parties = [(p.id, p.name) for p in s.scalars(select(Party))]
    payees = s.execute(select(Transaction.counterparty, Transaction.party_id).where(Transaction.ts >= txn_since, Transaction.counterparty != "").distinct()).all()
    by_payee: dict[str, set[str]] = {}
    for cp, pid in payees:
        by_payee.setdefault(cp, set()).add(pid)
    kept = {(m["subject_no"], m["where"], m["key"]): m for m in r.matches}
    found = []
    for subj in r.subjects:
        for alias in [subj["name"], *subj["aliases"]]:
            for pid, pname in parties:
                score = names.similarity(alias, pname)
                if score >= threshold:
                    found.append((subj["no"], "customer", pid, pname, score, f"Customer name against '{alias}'", [pid]))
            for cp, pids in by_payee.items():
                score = names.similarity(alias, cp)
                if score >= threshold:
                    found.append((subj["no"], "transaction", names.key(cp), cp, score, f"Named on payments of {len(pids)} customer{'s' if len(pids) != 1 else ''}, against '{alias}'", sorted(pids)[:20]))
    matches, seen = [], set()
    for no, where, key, name, score, basis, pids in sorted(found, key=lambda f: (f[0], -f[4])):
        if (no, where, key) in seen:
            continue
        seen.add((no, where, key))
        prior = kept.get((no, where, key))
        matches.append(prior | {"score": round(score, 2)} if prior else {"id": new_id("m"), "subject_no": no, "where": where, "key": key, "name": name, "score": round(score, 2),
                                                                          "basis": basis, "party_ids": pids, "decision": "pending", "decided_by": None, "decided_at": None, "note": ""})
    r.matches, r.status, r.searched_by, r.searched_at = matches, "searched", persona.id, at
    r.scope = {"customers": len(parties), "counterparty_names": len(by_payee), "transactions_since": txn_since.date().isoformat(), "match_at": threshold,
               "subjects": len(r.subjects)}
    s.flush()
    return r


def decide_match(s: Session, share_id: str, match_id: str, confirmed: bool, note: str, persona: Persona, at: datetime | None = None) -> InfoShare:
    _may(persona)
    r = _get(s, share_id)
    if r.status != "searched":
        raise SharingError("Matches are decided after a search and before the answer")
    if not any(m["id"] == match_id for m in r.matches):
        raise SharingError("Match not found")
    if len(note.strip()) < 5:
        raise SharingError("Say what the decision rests on, such as the date of birth or the address")
    at = at or now()
    r.matches = [m | {"decision": "confirmed" if confirmed else "dismissed", "decided_by": persona.id, "decided_at": at.isoformat(), "note": note.strip()[:500]}
                 if m["id"] == match_id else m for m in r.matches]
    s.flush()
    return r


def respond(s: Session, share_id: str, persona: Persona, at: datetime | None = None) -> InfoShare:
    """Close the request: record the answer if anything matched, or that the search found nothing to report."""
    _may(persona)
    r = _get(s, share_id)
    if r.status != "searched":
        raise SharingError("Search the records first" if r.status == "received" else "This request has been answered")
    waiting = sum(1 for m in r.matches if m["decision"] == "pending")
    if waiting:
        raise SharingError(f"{waiting} possible match{'es are' if waiting != 1 else ' is'} still undecided")
    at = at or now()
    confirmed = [m for m in r.matches if m["decision"] == "confirmed"]
    by_no = {x["no"]: x for x in r.subjects}
    r.response = {"positive": bool(confirmed), "late": at > r.due,
                  "subjects": [{"no": no, "name": by_no[no]["name"], "customers": sum(1 for m in confirmed if m["subject_no"] == no and m["where"] == "customer"),
                                "transactions": sum(1 for m in confirmed if m["subject_no"] == no and m["where"] == "transaction")}
                               for no in sorted({m["subject_no"] for m in confirmed})]}
    r.status, r.responded_by, r.responded_at = ("responded" if confirmed else "no_match"), persona.id, at
    s.flush()
    return r


def row(r: InfoShare, at: datetime | None = None, full: bool = False) -> dict:
    at = at or now()
    out = {"id": r.id, "reference": r.reference, "received": r.received.isoformat(), "due": r.due.isoformat(), "status": r.status, "loaded_by": r.loaded_by,
           "subjects_count": len(r.subjects), "overdue": r.status in ("received", "searched") and r.due < at,
           "possible": len(r.matches), "pending": sum(1 for m in r.matches if m["decision"] == "pending"),
           "confirmed": sum(1 for m in r.matches if m["decision"] == "confirmed"), "searched_by": r.searched_by,
           "searched_at": r.searched_at.isoformat() if r.searched_at else None, "responded_by": r.responded_by,
           "responded_at": r.responded_at.isoformat() if r.responded_at else None, "response": r.response, "scope": r.scope}
    return out | ({"subjects": r.subjects, "matches": r.matches} if full else {})


def overview(s: Session, persona: Persona) -> dict:
    """What the person may see of these requests: everything, or only that they are not theirs to see."""
    if not persona.can(HANDLE):
        return {"allowed": False, "requests": [], "can_handle": False, "rules": {}}
    rows = list(s.scalars(select(InfoShare).order_by(InfoShare.received.desc(), InfoShare.id)))
    return {"allowed": True, "can_handle": not persona.read_only, "requests": [row(r) for r in rows],
            "rules": {c["code"]: c["value"] for c in reference.codes(s, "info_share_rules")}}


def detail(s: Session, share_id: str, persona: Persona) -> dict:
    _may(persona, write=False)
    return row(_get(s, share_id), full=True)
