"""Synthetic sanctions work for the demo: held payments at each stage, a client list, exemptions, and searches for law enforcement.

Everything here is invented. It goes through the same functions a person's actions do, as the client's own people.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import holds
from ..cases.case_types import load_case_types
from ..domain.models import Account, Alert, Case, Hold, Party, Transaction, repeatable_ids
from ..filing import service as filing
from ..domain.personas import policy_of
from ..filing.report import SUBMIT
from ..tenancy import tenant_of
from . import lists, reports, screening, sharing


def _deciders(s: Session) -> tuple:
    people = [p for p in policy_of(tenant_of(s)).people.values() if not p.read_only and p.can(screening.DECIDE) and p.can("work")]
    return (people[0], people[1]) if len(people) >= 2 else (None, None)


def _payment(s: Session, case: Case) -> str | None:
    return next((t for a in s.scalars(select(Alert).where(Alert.case_id == case.id).order_by(Alert.created)) for t in a.txn_ids), None)


def hold_on_escalation(s: Session, case: Case, at: datetime) -> None:
    """While history is being written: the payment behind a hit is held when the hit is escalated."""
    officer, _ = _deciders(s)
    txn = _payment(s, case)
    if officer is not None and txn is not None:
        with repeatable_ids(f"hold:{case.id}"):  # its own id stream, so adding this history does not shift every id after it
            holds.place(s, case, "payment", txn, "sanctions_potential_match", officer, "Held on the screening hit until the match is decided.", at=at)


def block_before_close(s: Session, case: Case, at: datetime) -> None:
    """While history is being written: two people block the held payment before the case closes as a true match."""
    officer, second = _deciders(s)
    with repeatable_ids(f"decide:{case.id}"):  # its own id stream, like the hold itself
        for h in s.scalars(select(Hold).where(Hold.case_id == case.id, Hold.status == "active")):
            holds.decide(s, h.id, "block", "Confirmed against the list entry by name and country. Funds stay frozen.", officer, at=at - timedelta(minutes=40))
            holds.decide_proposal(s, h.id, True, "Agreed", second, at=at - timedelta(minutes=10))
        s.flush()


def release_before_close(s: Session, case: Case, at: datetime) -> None:
    officer, second = _deciders(s)
    with repeatable_ids(f"decide:{case.id}"):  # its own id stream, like the hold itself
        for h in s.scalars(select(Hold).where(Hold.case_id == case.id, Hold.status == "active")):
            holds.decide(s, h.id, "release", "Not the listed party: the name differs and nothing else matches.", officer, at=at - timedelta(minutes=30))
            holds.decide_proposal(s, h.id, True, "Agreed", second, at=at - timedelta(minutes=5))
        s.flush()


def _fresh_hit(s: Session, party_id: str, payee: str, country: str, amount: float, at: datetime) -> Case | None:
    """A payment made just now to a name on the list, screened and routed like any other."""
    from ..alerts.hub import route_alerts
    from ..detection.engine import run_detection

    account = s.scalars(select(Account.id).where(Account.party_id == party_id)).first()
    if account is None:
        return None
    s.add(Transaction(id=f"T-{party_id}-{at:%H%M}", account_id=account, party_id=party_id, ts=at, amount=amount, direction="out", channel="wire",
                      counterparty=payee, counterparty_country=country))
    s.flush()
    return next(iter(route_alerts(s, run_detection(s, {party_id}, only={"sanctions_name"}), screened=True)), None)


def seed(s: Session, end: datetime) -> dict:
    tenant = tenant_of(s)
    if "sanctions_hit" not in load_case_types(tenant):
        return {"transactions": 0}
    people = [p for p in policy_of(tenant).people.values() if not p.read_only]
    officer, second = _deciders(s)
    if officer is None:
        return {"transactions": 0}
    lists.ensure(s)
    made = 0

    # Reports to the authority for the payments blocked while history was written: all but the latest are prepared and sent.
    filer = next((p for p in people if p.can(SUBMIT)), None)
    blocked = list(s.scalars(select(Hold).where(Hold.status == "blocked").order_by(Hold.decided_at, Hold.id)))
    for n, h in enumerate(blocked[:-1] if len(blocked) > 1 else []):
        if filer is None:
            break
        f = reports.prepare(s, h.id, filer, ts=h.decided_at + timedelta(days=2))
        if not f.problems:
            filing.submit(s, f.id, filer, ts=h.decided_at + timedelta(days=2, hours=1))
            if n == 0:
                filing.acknowledge(s, f.id, f"OFAC-{h.decided_at:%y%m}-{1000 + n}", filer, ts=h.decided_at + timedelta(days=6))

    # Two payments made today to listed names: one held with its release waiting for a second person, one sent back.
    waiting = _fresh_hit(s, "C20001", "Northgate Petrochem FZ LLC", "AE", 48200.0, end - timedelta(hours=5))
    if waiting is not None:
        made += 1
        h = holds.place(s, waiting, "payment", _payment(s, waiting), "sanctions_potential_match", officer, "Held on the screening hit until the match is decided.",
                        at=end - timedelta(hours=3, minutes=30))
        holds.decide(s, h.id, "release", "Name is close but this is a different firm: registered in a different free zone, with no shared address or director.",
                     officer, at=end - timedelta(hours=1))
    returned = _fresh_hit(s, "C20004", "Zarin Exchange House", "AF", 12750.0, end - timedelta(hours=9))
    if returned is not None:
        made += 1
        h = holds.place(s, returned, "payment", _payment(s, returned), "sanctions_potential_match", officer, "Held on the screening hit.", at=end - timedelta(hours=8))
        holds.decide(s, h.id, "reject", "The beneficiary is the listed party. Nothing of theirs is held here, so the payment goes back to the sender.", officer,
                     at=end - timedelta(hours=7))
        holds.decide_proposal(s, h.id, True, "Agreed", second, at=end - timedelta(hours=6))

    # A false match that will not be raised again, and one waiting for a second person.
    discounted = list(s.scalars(select(Case).where(Case.case_type == "sanctions_hit", Case.disposition == "false_positive").order_by(Case.created, Case.id)))
    proposed = 0
    for case in discounted:
        for m in screening.candidates(s, case.id):
            if m["score"] >= 0.999 or proposed >= 2:
                continue  # an exact name is never waved through on the name alone
            try:
                x = screening.propose_exemption(s, m["subject"], m["entry_id"], "Different legal entity: registered in another country, with its own "
                                                "registration number and directors. Checked against the company registry.", officer, case.id,
                                                at=end - timedelta(days=9 - 8 * proposed))
            except screening.ScreeningError:
                continue
            if proposed == 0:
                screening.decide_exemption(s, x.id, True, "Registry extract is on the case.", second, at=end - timedelta(days=8))
            proposed += 1

    # The client's own list, and a rescreen that finds one of its existing customers on it.
    manager = next((p for p in people if p.can(lists.MANAGE)), None)
    named = s.scalars(select(Party).where(Party.id == "C20003")).first()
    if manager is not None and named is not None:
        lists.load(s, "internal_watch", [{"ref": "INT-001", "name": named.name.replace(" Inc", ""), "kind": "entity", "countries": [named.country],
                                         "programs": ["Board decision 2026-14"]},
                                        {"ref": "INT-002", "name": "Kestrel Offshore Logistics", "kind": "entity", "countries": ["PA"], "programs": ["Exited customer"]}],
                   manager, name="Internal do-not-deal list", kind="internal", source="Financial crime committee",
                   note="First issue: names the committee has decided the bank will not deal with.", at=end - timedelta(hours=20))
        screening.rescreen(s, at=end - timedelta(hours=20))

    # Searches for law enforcement: one answered with nothing found, one searched and part decided.
    handler = next((p for p in people if p.can(sharing.HANDLE)), None)
    if handler is not None:
        someone = s.scalars(select(Party).where(Party.kind == "person").order_by(Party.id)).first()
        old = sharing.receive(s, "LE-2026-0412", [{"name": "Ilya Voronkov", "dob": "1975-02-11", "country": "RU"},
                                                  {"name": "Brightline Commodities DMCC", "kind": "business", "country": "AE"},
                                                  {"name": "Tamsin Okonkwo-Reyes", "dob": "1988-09-30"}], handler, received=end - timedelta(days=17))
        sharing.search(s, old.id, handler, at=end - timedelta(days=16))
        for m in list(old.matches):
            sharing.decide_match(s, old.id, m["id"], False, "Different date of birth and address.", handler, at=end - timedelta(days=16))
        sharing.respond(s, old.id, handler, at=end - timedelta(days=15))
        subjects = [{"name": "Northwind Holdings", "kind": "business", "aliases": ["Northwind Holdings LLC"], "identifiers": "EIN 47-2210934", "country": "US"},
                    {"name": "Darya Shipping Lines", "kind": "business", "country": "IR"},
                    {"name": "Radoslav Petrenko", "dob": "1969-06-02", "country": "UA"}]
        if someone is not None:
            subjects.append({"name": someone.name, "dob": "1990-01-01", "country": someone.country})
        new = sharing.receive(s, "LE-2026-0427", subjects, handler, received=end - timedelta(days=3))
        sharing.search(s, new.id, handler, at=end - timedelta(days=2))
        first = next((m for m in new.matches if m["where"] == "customer" and m["subject_no"] == 1), None)
        if first is not None:
            sharing.decide_match(s, new.id, first["id"], True, "Tax number on the request matches the one on file.", handler, at=end - timedelta(days=2))
    s.flush()
    return {"transactions": made}
