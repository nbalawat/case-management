"""Fraud and sanctions beyond the case: screening lists, rescreening, exemptions, holds, disputes, reports to the authority,
and searches for law enforcement."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import holds, reference
from app.alerts.hub import route_alerts
from app.cases.case_types import load_case_types
from app.cases.service import TransitionError, complete_stage_tasks, create_case, reasons_for, transition
from app.cases.timers import run_timers
from app.detection.engine import run_detection
from app.detection.scenarios import DEMO_WATCHLIST
from app.domain.models import Account, Alert, Case, CaseEvent, Dispute, Filing, Hold, InfoShare, Party, ScreeningExemption, Transaction, Watchlist, WatchlistEntry, now
from app.filing import service as filing
from app.fraud import disputes
from app.holds import HoldError
from app.sanctions import lists, reports, screening, sharing
from app.sanctions.lists import ListError
from app.sanctions.screening import ScreeningError
from app.sanctions.sharing import SharingError
from app.workdays import add_business_days
from app.tenancy import tenant_of
from support import PERSONAS, T
from test_client_api import CLIENTS, EVERYONE, _permissions, h

P = PERSONAS
ANA, SOFIA, MAYA, HUGO, FRAN, OMAR, AUDRA, IVAN, TARA = (P[k] for k in ("ana", "sofia", "maya", "hugo", "fran", "omar", "audra", "ivan", "tara"))


def party(s, pid="S1", name="Test Customer", **kw):
    p = Party(id=pid, name=name, **kw)
    s.add_all([p, Account(id=f"A-{pid}", party_id=pid)])
    s.flush()
    return p


def pay(s, pid, n, amount, cp="", channel="wire", direction="out", country="US", ts=None):
    t = Transaction(id=f"t-{pid}-{n}", account_id=f"A-{pid}", party_id=pid, ts=ts or now() - timedelta(days=2, minutes=-n), amount=float(amount), direction=direction,
                    channel=channel, counterparty=cp, counterparty_country=country)
    s.add(t)
    s.flush()
    return t


def hit(s, pid="S1", payee="Volkov Maritime Trading Co", amount=25000):
    """A customer with one payment to a listed name, screened and routed: the sanctions case and the payment."""
    if s.scalars(select(Party).where(Party.id == pid)).first() is None:
        party(s, pid)
    t = pay(s, pid, len(list(s.scalars(select(Transaction.id).where(Transaction.party_id == pid)))), amount, cp=payee)
    cases = route_alerts(s, run_detection(s, {pid}, only={"sanctions_name"}), screened=True)
    return cases[0], t


def fraud_case(s, pid="F1", payments=((400, "card"), (350, "card"))):
    party(s, pid)
    txns = [pay(s, pid, n, amount, cp="Nova Electronics Online", channel=channel) for n, (amount, channel) in enumerate(payments)]
    case = create_case(s, load_case_types(tenant_of(s))["fraud_claim"], pid, "high", actor="test")
    return case, txns


def entry(ref, name, **kw):
    return {"ref": ref, "name": name} | kw


# --- lists ----------------------------------------------------------------------------------------------------------------------
def test_every_client_starts_with_the_same_small_list_and_screens_against_it(s):
    assert lists.names(s) == sorted(DEMO_WATCHLIST)
    wl = s.scalars(select(Watchlist)).one()
    assert (wl.version, wl.kind, wl.active) == (1, "sanctions", True) and len(wl.history) == 1
    case, _ = hit(s)
    assert case.case_type == "sanctions_hit"


def test_a_load_is_compared_with_what_is_held_and_nothing_is_deleted(s):
    lists.ensure(s)
    first = lists.load(s, "eu_list", [entry("E1", "Alpha Trading"), entry("E2", "Beta Shipping", aliases="Beta Ship; BS Lines", countries="de;nl")], MAYA, name="EU list")
    assert (first["version"], first["added"], first["changed"], first["removed"]) == (1, ["Alpha Trading", "Beta Shipping"], [], [])
    second = lists.load(s, "eu_list", [entry("E2", "Beta Shipping", aliases=["Beta Ship"], countries=["DE"]), entry("E3", "Gamma Oil")], MAYA, note="Issue 2")
    assert (second["version"], second["added"], second["changed"], second["removed"]) == (2, ["Gamma Oil"], ["Beta Shipping"], ["Alpha Trading"])
    rows = {e.ref: e for e in s.scalars(select(WatchlistEntry).where(WatchlistEntry.list_id == s.scalars(select(Watchlist.id).where(Watchlist.code == "eu_list")).one()))}
    assert rows["E1"].removed_version == 2 and rows["E3"].added_version == 2 and rows["E2"].aliases == ["Beta Ship"] and rows["E2"].countries == ["DE"]
    assert "Alpha Trading" not in lists.names(s) and "Gamma Oil" in lists.names(s)
    wl = s.scalars(select(Watchlist).where(Watchlist.code == "eu_list")).one()
    assert [x["version"] for x in wl.history] == [1, 2] and wl.history[1]["note"] == "Issue 2" and wl.updated_by == "maya"


def test_loading_the_same_list_again_changes_nothing(s):
    rows = [entry("E1", "Alpha Trading")]
    lists.load(s, "eu_list", rows, MAYA, name="EU list")
    again = lists.load(s, "eu_list", rows, MAYA)
    assert again["unchanged"] and again["version"] == 1 and len(s.scalars(select(Watchlist).where(Watchlist.code == "eu_list")).one().history) == 1


@pytest.mark.parametrize("rows,why", [
    ([{"name": "No Ref"}], "no reference"),
    ([{"ref": "A"}], "no name"),
    ([entry("A", "One"), entry("A", "Two")], "appears twice"),
    ([entry("A", "One", kind="spaceship")], "kind must be"),
    ([entry("A", "One", countries="Germany")], "two-letter"),
    ("not a list", "set of entries"),
    ([], "at least one entry"),
])
def test_a_malformed_list_is_refused_whole(s, rows, why):
    with pytest.raises(ListError, match=why):
        lists.load(s, "bad_list", rows, MAYA, name="Bad")
    assert s.scalars(select(Watchlist).where(Watchlist.code == "bad_list")).first() is None


@pytest.mark.parametrize("code", ["", "Has Space", "9starts", "x"])
def test_a_list_needs_a_proper_code(s, code):
    with pytest.raises(ListError, match="short code"):
        lists.load(s, code, [entry("A", "One")], MAYA, name="X")


def test_a_new_list_needs_a_name_and_a_known_kind(s):
    with pytest.raises(ListError, match="needs a name"):
        lists.load(s, "new_list", [entry("A", "One")], MAYA)
    with pytest.raises(ListError, match="one of"):
        lists.load(s, "new_list", [entry("A", "One")], MAYA, name="N", kind="rumours")


@pytest.mark.parametrize("who", [ANA, SOFIA, IVAN, FRAN, TARA, AUDRA])
def test_only_people_who_manage_lists_can_change_one(s, who):
    with pytest.raises(ListError, match="needs the permission"):
        lists.load(s, "eu_list", [entry("A", "One")], who, name="EU")
    lists.load(s, "own_list", [entry("A", "One")], OMAR, name="Own", kind="internal")
    for change in (lambda: lists.put_entry(s, "own_list", entry("B", "Two"), who), lambda: lists.remove_entry(s, "own_list", "A", who, "why"),
                   lambda: lists.set_active(s, "own_list", False, who, "why")):
        with pytest.raises(ListError, match="needs the permission"):
            change()


def test_csv_and_json_files_parse_to_the_same_entries():
    csv = "ref,name,aliases,kind,countries,programs,identifiers\nX1,Orion Freight,Orion; OF Ltd,entity,pa;cy,SDGT,imo:9123456;reg:HE1\n"
    js = '{"entries": [{"ref": "X1", "name": "Orion Freight", "aliases": ["Orion", "OF Ltd"], "kind": "entity", "countries": ["PA", "CY"], "programs": ["SDGT"], ' \
         '"identifiers": [{"type": "imo", "value": "9123456"}, {"type": "reg", "value": "HE1"}]}]}'
    assert lists._clean(lists.parse(csv)) == lists._clean(lists.parse(js))
    assert lists._clean(lists.parse(csv))[0]["identifiers"] == [{"type": "imo", "value": "9123456"}, {"type": "reg", "value": "HE1"}]


@pytest.mark.parametrize("text,why", [("", "empty"), ("{not json", "not valid JSON"), ("name\nOrion", "header row")])
def test_an_unreadable_file_says_what_is_wrong(text, why):
    with pytest.raises(ListError, match=why):
        lists.parse(text)


def test_the_clients_own_list_is_edited_entry_by_entry_and_a_published_one_is_not(s):
    lists.load(s, "own_list", [entry("A", "One Ltd")], MAYA, name="Own", kind="internal")
    assert lists.put_entry(s, "own_list", entry("B", "Two Ltd"), MAYA)["added"] == ["Two Ltd"]
    assert lists.put_entry(s, "own_list", entry("B", "Two Limited"), MAYA)["changed"] == ["Two Limited"]
    with pytest.raises(ListError, match="Say why"):
        lists.remove_entry(s, "own_list", "A", MAYA)
    assert lists.remove_entry(s, "own_list", "A", MAYA, "Relationship ended")["removed"] == ["One Ltd"]
    with pytest.raises(ListError, match="Entry not found"):
        lists.remove_entry(s, "own_list", "A", MAYA, "again")
    with pytest.raises(ListError, match="loading its next version"):
        lists.put_entry(s, "demo_sanctions", entry("Z", "Zed"), MAYA)
    with pytest.raises(ListError, match="loading its next version"):
        lists.remove_entry(s, "demo_sanctions", "DEMO-001", MAYA, "no")


def test_the_only_sanctions_list_cannot_be_switched_off(s):
    lists.ensure(s)
    with pytest.raises(ListError, match="only sanctions list"):
        lists.set_active(s, "demo_sanctions", False, MAYA, "replacing it")
    lists.load(s, "eu_list", [entry("E1", "Alpha Trading")], MAYA, name="EU")
    with pytest.raises(ListError, match="Say why"):
        lists.set_active(s, "demo_sanctions", False, MAYA, " ")
    lists.set_active(s, "demo_sanctions", False, MAYA, "Replaced by the EU list")
    assert lists.names(s) == ["Alpha Trading"]
    lists.set_active(s, "demo_sanctions", True, MAYA, "Back on")
    assert len(lists.names(s)) == 5


def test_lists_are_each_clients_own(db):
    db.onboard("one")
    db.onboard("two")
    with db.tenant("one") as a:
        lists.load(a, "own_list", [entry("A", "Secret Name")], MAYA, name="Own", kind="internal")
        a.commit()
    with db.tenant("two") as b:
        assert "Secret Name" not in lists.names(b) and [x["code"] for x in lists.view(b, MAYA)["lists"]] == ["demo_sanctions"]


# --- screening ------------------------------------------------------------------------------------------------------------------
def test_a_payment_is_screened_against_aliases_and_the_clients_own_list(s):
    lists.load(s, "own_list", [entry("A", "Harbourline Brokers", aliases=["HLB Trading House"])], MAYA, name="Own", kind="internal")
    party(s)
    pay(s, "S1", 1, 900, cp="HLB Trading House")
    pay(s, "S1", 2, 900, cp="Corner Cafe")
    alerts = run_detection(s, {"S1"}, only={"sanctions_name"})
    assert [a.txn_ids for a in alerts] == [["t-S1-1"]] and "HLB Trading House" in alerts[0].explanation


def test_a_list_switched_off_is_not_screened_against(s):
    lists.load(s, "own_list", [entry("A", "Harbourline Brokers")], MAYA, name="Own", kind="internal")
    lists.set_active(s, "own_list", False, MAYA, "Under review")
    party(s)
    pay(s, "S1", 1, 900, cp="Harbourline Brokers")
    assert run_detection(s, {"S1"}, only={"sanctions_name"}) == []


def test_the_clients_threshold_decides_how_close_is_close_enough(s):
    party(s)
    pay(s, "S1", 1, 900, cp="Volkov Maritime Trade")  # close, not very close
    assert len(run_detection(s, {"S1"}, only={"sanctions_name"})) == 1
    rules = [c | ({"value": 0.99} if c["code"] == "match_at" else {}) for c in reference.codes(s, "screening_rules")]
    reference.replace(s, "screening_rules", rules, OMAR)
    party(s, "S2")
    pay(s, "S2", 1, 900, cp="Volkov Maritime Trade")
    assert run_detection(s, {"S2"}, only={"sanctions_name"}) == []


def test_rescreening_after_a_list_change_finds_customers_and_payments_already_on_the_books(s):
    party(s, "S1", name="Castellan Marine Services Inc")
    party(s, "S2", name="Ordinary Person")
    pay(s, "S2", 1, 5000, cp="Kestrel Offshore Logistics")
    assert screening.rescreen(s)["customer_hits"] == 0
    lists.load(s, "own_list", [entry("A", "Castellan Marine Services"), entry("B", "Kestrel Offshore Logistics")], MAYA, name="Own", kind="internal")
    out = screening.rescreen(s)
    assert (out["customer_hits"], out["payment_hits"], len(out["cases"])) == (1, 1, 2)
    by_party = {c.party_id: c for c in s.scalars(select(Case))}
    assert by_party["S1"].case_type == by_party["S2"].case_type == "sanctions_hit"
    name_hit = s.scalars(select(Alert).where(Alert.scenario == "sanctions_customer")).one()
    assert name_hit.party_id == "S1" and name_hit.txn_ids == [] and "customer's own name" in name_hit.explanation
    again = screening.rescreen(s)
    assert (again["customer_hits"], again["payment_hits"], again["cases"]) == (0, 0, [])  # the same hit is never raised twice


def test_rescreening_runs_only_the_sanctions_scenario(s):
    party(s)
    for n in range(4):  # structuring, which rescreening must not raise
        pay(s, "S1", n, 9500, channel="cash", direction="in", ts=now() - timedelta(days=3, hours=n))
    screening.rescreen(s)
    assert list(s.scalars(select(Alert))) == []
    assert {a.scenario for a in run_detection(s)} == {"structuring"}


# --- exemptions -----------------------------------------------------------------------------------------------------------------
def test_an_exemption_needs_two_people_and_then_stops_the_same_false_match(s):
    case, _ = hit(s)
    match = screening.candidates(s, case.id)[0]
    assert match["subject"] == "Volkov Maritime Trading Co" and match["entry_name"] == "Volkov Maritime Trading" and 0.86 <= match["score"] < 1
    x = screening.propose_exemption(s, match["subject"], match["entry_id"], "Different company: registered in Malta, other directors.", SOFIA, case.id)
    assert x.status == "pending" and x.expires is None
    pay(s, "S1", 7, 100, cp="volkov  maritime trading co")
    assert len(run_detection(s, {"S1"}, only={"sanctions_name"})) == 1  # pending changes nothing
    with pytest.raises(ScreeningError, match="someone other than"):
        screening.decide_exemption(s, x.id, True, "", SOFIA)
    with pytest.raises(ScreeningError, match="needs the permission"):
        screening.decide_exemption(s, x.id, True, "", ANA)
    screening.decide_exemption(s, x.id, True, "Registry extract seen", MAYA)
    assert x.status == "active" and x.decided_by == "maya" and 360 <= (x.expires - now()).days <= 366
    pay(s, "S1", 8, 100, cp="VOLKOV MARITIME   TRADING CO")  # case and spacing do not matter
    pay(s, "S1", 9, 100, cp="Volkov Maritime Trading")  # a different name, the listed one itself
    assert [a.txn_ids for a in run_detection(s, {"S1"}, only={"sanctions_name"})] == [["t-S1-9"]]
    with pytest.raises(ScreeningError, match="already been decided"):
        screening.decide_exemption(s, x.id, False, "changed my mind", HUGO)


def test_an_exemption_lapses_on_its_date_and_can_be_ended_early(s):
    case, _ = hit(s)
    m = screening.candidates(s, case.id)[0]
    x = screening.propose_exemption(s, m["subject"], m["entry_id"], "Different company: registered in Malta.", SOFIA, case.id)
    screening.decide_exemption(s, x.id, True, "", MAYA, at=now() - timedelta(days=400))
    assert lists.watch(s).exempt == frozenset() and screening.exemptions(s)[0]["status"] == "expired"
    y = screening.propose_exemption(s, "Darya Shipping Line LLC", next(e.id for e in lists.entries(s) if e.name.startswith("Darya")), "Different company entirely, checked.", SOFIA)
    screening.decide_exemption(s, y.id, True, "", MAYA)
    assert len(lists.watch(s).exempt) == 1
    with pytest.raises(ScreeningError, match="Say why"):
        screening.revoke_exemption(s, y.id, "", MAYA)
    with pytest.raises(ScreeningError, match="needs the permission"):
        screening.revoke_exemption(s, y.id, "no longer true", ANA)
    screening.revoke_exemption(s, y.id, "The firm was bought by the listed party", MAYA)
    assert lists.watch(s).exempt == frozenset() and y.status == "revoked"
    with pytest.raises(ScreeningError, match="in force"):
        screening.revoke_exemption(s, y.id, "again", MAYA)


def test_a_refusal_needs_a_reason_and_leaves_screening_as_it_was(s):
    case, _ = hit(s)
    m = screening.candidates(s, case.id)[0]
    x = screening.propose_exemption(s, m["subject"], m["entry_id"], "Different company: registered in Malta.", SOFIA, case.id)
    with pytest.raises(ScreeningError, match="Say why"):
        screening.decide_exemption(s, x.id, False, "  ", MAYA)
    screening.decide_exemption(s, x.id, False, "Same directors as the listed firm", MAYA)
    assert x.status == "refused" and lists.watch(s).exempt == frozenset()
    screening.propose_exemption(s, m["subject"], m["entry_id"], "New evidence: the directors differ after all.", SOFIA, case.id)  # may be proposed again


@pytest.mark.parametrize("subject,reason,who,why", [
    ("Volkov Maritime Trading Co", "too short", SOFIA, "tells this name apart"),
    ("ab", "Different company: registered in Malta.", SOFIA, "as it was screened"),
    ("Volkov Maritime Trading Co", "Different company: registered in Malta.", AUDRA, "works cases"),
    ("Volkov Maritime Trading Co", "Different company: registered in Malta.", OMAR, "works cases"),
])
def test_a_proposed_exemption_must_be_complete_and_from_someone_who_works_cases(s, subject, reason, who, why):
    lists.ensure(s)
    with pytest.raises(ScreeningError, match=why):
        screening.propose_exemption(s, subject, lists.entries(s)[0].id, reason, who)


def test_one_exemption_per_name_and_entry_and_none_for_an_entry_that_is_gone(s):
    lists.load(s, "own_list", [entry("A", "Harbourline Brokers")], MAYA, name="Own", kind="internal")
    e = next(x for x in lists.entries(s) if x.ref == "A")
    screening.propose_exemption(s, "Harborline Brokers", e.id, "A different firm with a similar name.", SOFIA)
    with pytest.raises(ScreeningError, match="already exists"):
        screening.propose_exemption(s, "harborline  brokers", e.id, "A different firm with a similar name.", SOFIA)
    lists.remove_entry(s, "own_list", "A", MAYA, "Gone")
    with pytest.raises(ScreeningError, match="List entry not found"):
        screening.propose_exemption(s, "Another Name", e.id, "A different firm with a similar name.", SOFIA)
    with pytest.raises(ScreeningError, match="List entry not found"):
        screening.propose_exemption(s, "Another Name", "nope", "A different firm with a similar name.", SOFIA)


def test_a_changed_or_removed_entry_ends_the_exemptions_made_against_it(s):
    lists.load(s, "eu_list", [entry("E1", "Alpha Trading"), entry("E2", "Beta Shipping"), entry("E3", "Gamma Oil", programs=["P1"])], MAYA, name="EU")
    by_ref = {e.ref: e for e in lists.entries(s)}
    made = {}
    for ref, subject in (("E1", "Alfa Trading"), ("E2", "Beta Shipping Co"), ("E3", "Gama Oil")):
        x = screening.propose_exemption(s, subject, by_ref[ref].id, "A different firm with a similar name.", SOFIA)
        if ref != "E2":
            screening.decide_exemption(s, x.id, True, "", MAYA)
        made[ref] = x
    out = lists.load(s, "eu_list", [entry("E1", "Alpha Trading", aliases=["Alfa Trading"]), entry("E3", "Gamma Oil", programs=["P1", "P2"])], MAYA)
    assert out["exemptions_ended"] == 2  # E1 changed in substance, E2 removed; E3 only gained a programme
    assert (made["E1"].status, made["E2"].status, made["E3"].status) == ("revoked", "revoked", "active")
    assert "Ended automatically" in made["E1"].decision_note


def test_an_exemption_proposed_against_an_entry_that_then_changed_cannot_be_approved(s):
    lists.load(s, "own_list", [entry("A", "Harbourline Brokers")], MAYA, name="Own", kind="internal")
    e = next(x for x in lists.entries(s) if x.ref == "A")
    x = screening.propose_exemption(s, "Harborline Brokers", e.id, "A different firm with a similar name.", SOFIA)
    e.aliases = ["Harborline Brokers"]  # as if changed underneath, without the load that would have ended the proposal
    s.flush()
    with pytest.raises(ScreeningError, match="has changed since"):
        screening.decide_exemption(s, x.id, True, "", MAYA)


# --- holds ----------------------------------------------------------------------------------------------------------------------
def test_a_sanctions_hold_takes_two_people_to_end(s):
    case, t = hit(s)
    h_ = holds.place(s, case, "payment", t.id, "sanctions_potential_match", SOFIA, "Held on the hit")
    assert (h_.status, h_.amount, h_.placed_by) == ("active", 25000.0, "sofia") and 3.9 < (h_.review_by - h_.placed_at).total_seconds() / 3600 < 4.1
    holds.decide(s, h_.id, "release", "Different firm, different country.", SOFIA)
    assert h_.status == "active" and h_.proposal["outcome"] == "release"
    with pytest.raises(HoldError, match="already proposed"):
        holds.decide(s, h_.id, "block", "On second thoughts, block it.", SOFIA)
    with pytest.raises(HoldError, match="someone other than"):
        holds.decide_proposal(s, h_.id, True, "", SOFIA)
    with pytest.raises(HoldError, match="needs the permission"):
        holds.decide_proposal(s, h_.id, True, "", ANA)
    holds.decide_proposal(s, h_.id, True, "Agreed", MAYA)
    assert (h_.status, h_.decided_by, h_.proposal) == ("released", "maya", None)
    kinds = [e.kind for e in s.scalars(select(CaseEvent).where(CaseEvent.case_id == case.id, CaseEvent.kind.like("hold.%")).order_by(CaseEvent.id))]
    assert kinds == ["hold.placed", "hold.proposed", "hold.released"]
    with pytest.raises(HoldError, match="no longer active"):
        holds.decide(s, h_.id, "block", "Too late to change it now.", SOFIA)


def test_a_refused_proposal_leaves_the_hold_in_place_for_another(s):
    case, t = hit(s)
    h_ = holds.place(s, case, "payment", t.id, "sanctions_potential_match", SOFIA)
    holds.decide(s, h_.id, "release", "Different firm, different country.", SOFIA)
    with pytest.raises(HoldError, match="Say why"):
        holds.decide_proposal(s, h_.id, False, "", MAYA)
    holds.decide_proposal(s, h_.id, False, "The registration number matches the list entry", MAYA)
    assert h_.status == "active" and h_.proposal is None
    holds.decide(s, h_.id, "block", "Registration number matches. Funds stay frozen.", MAYA)
    holds.decide_proposal(s, h_.id, True, "", SOFIA)
    assert h_.status == "blocked"
    with pytest.raises(HoldError, match="Nothing is waiting"):
        holds.decide_proposal(s, h_.id, True, "", MAYA)


def test_a_fraud_hold_is_ended_by_one_person(s):
    case, _ = fraud_case(s)
    h_ = holds.place(s, case, "card", "", "suspected_fraud", FRAN, "Card blocked while we check")
    assert 23.9 < (h_.review_by - h_.placed_at).total_seconds() / 3600 < 24.1 and h_.amount == 0
    holds.decide(s, h_.id, "release", "Customer confirmed the payments were theirs.", FRAN)
    assert h_.status == "released" and h_.decided_by == "fran"


def test_a_step_up_check_gates_the_release(s):
    case, _ = fraud_case(s)
    h_ = holds.place(s, case, "online_access", "", "account_takeover", FRAN)
    with pytest.raises(HoldError, match="No step-up check is waiting"):
        holds.challenge_result(s, h_.id, True, FRAN)
    with pytest.raises(HoldError, match="one of"):
        holds.challenge(s, h_.id, "ask_nicely", FRAN)
    holds.challenge(s, h_.id, "one_time_code", FRAN)
    with pytest.raises(HoldError, match="already waiting"):
        holds.challenge(s, h_.id, "call_back", FRAN)
    with pytest.raises(HoldError, match="still waiting for the customer"):
        holds.decide(s, h_.id, "release", "Customer says it was them.", FRAN)
    holds.challenge_result(s, h_.id, False, FRAN, "Wrong code three times")
    with pytest.raises(HoldError, match="failed the step-up check"):
        holds.decide(s, h_.id, "release", "Customer says it was them.", FRAN)
    holds.challenge(s, h_.id, "call_back", FRAN)  # a new check may be sent
    holds.challenge_result(s, h_.id, True, FRAN, "Answered security questions on the number on file")
    holds.decide(s, h_.id, "release", "Identity confirmed by call back.", FRAN)
    assert h_.status == "released" and h_.challenge["status"] == "passed"


def test_a_failed_check_does_not_stop_a_block(s):
    case, _ = fraud_case(s)
    h_ = holds.place(s, case, "online_access", "", "account_takeover", FRAN)
    holds.challenge(s, h_.id, "one_time_code", FRAN)
    holds.challenge_result(s, h_.id, False, FRAN)
    holds.decide(s, h_.id, "block", "Not the customer. Access stays off until they visit a branch.", FRAN)
    assert h_.status == "blocked"


@pytest.mark.parametrize("target,target_id,reason,why", [
    ("payment", "t-OTHER-0", "suspected_fraud", "not one of this customer's"),
    ("payment", "", "suspected_fraud", "not one of this customer's"),
    ("account", "A-OTHER", "suspected_fraud", "not one of this customer's"),
    ("card", "A-OTHER", "suspected_fraud", "Name one of this customer's accounts"),
    ("vault", "", "suspected_fraud", "A hold is on one of"),
    ("card", "", "because", "Choose a reason"),
])
def test_a_hold_must_name_something_of_this_customers_and_a_reason_the_client_offers(s, target, target_id, reason, why):
    case, _ = fraud_case(s)
    party(s, "OTHER")
    pay(s, "OTHER", 0, 10, cp="x")
    with pytest.raises(HoldError, match=why):
        holds.place(s, case, target, target_id, reason, FRAN)


def test_the_same_thing_is_not_held_twice_and_only_a_payment_can_be_rejected(s):
    case, txns = fraud_case(s)
    holds.place(s, case, "account", "A-F1", "suspected_fraud", FRAN)
    with pytest.raises(HoldError, match="already on hold"):
        holds.place(s, case, "account", "A-F1", "account_takeover", FRAN)
    card = holds.place(s, case, "card", "", "suspected_fraud", FRAN)
    with pytest.raises(HoldError, match="Only a payment"):
        holds.decide(s, card.id, "reject", "Send it back to where it came from.", FRAN)
    with pytest.raises(HoldError, match="released, blocked or rejected"):
        holds.decide(s, card.id, "forget", "Send it back to where it came from.", FRAN)
    with pytest.raises(HoldError, match="Say why"):
        holds.decide(s, card.id, "release", "ok", FRAN)
    p = holds.place(s, case, "payment", txns[0].id, "suspected_fraud", FRAN)
    holds.decide(s, p.id, "reject", "Returned to the sender at the customer's request.", FRAN)
    assert p.status == "rejected"


@pytest.mark.parametrize("who", [AUDRA, OMAR])
def test_people_who_do_not_work_cases_cannot_touch_a_hold(s, who):
    case, _ = fraud_case(s)
    with pytest.raises(HoldError, match="works cases"):
        holds.place(s, case, "card", "", "suspected_fraud", who)
    h_ = holds.place(s, case, "card", "", "suspected_fraud", FRAN)
    for act in (lambda: holds.challenge(s, h_.id, "call_back", who), lambda: holds.decide(s, h_.id, "release", "It is all fine really.", who)):
        with pytest.raises(HoldError, match="works cases"):
            act()


def test_a_case_cannot_close_while_it_holds_something(s):
    case, t = hit(s)
    h_ = holds.place(s, case, "payment", t.id, "sanctions_potential_match", SOFIA)
    complete_stage_tasks(s, case, ANA)
    with pytest.raises(TransitionError, match="1 hold is still active"):
        transition(s, case, "discounted", ANA, "Not a match", reason=reasons_for(s, "false_positive")[0]["code"])
    transition(s, case, "escalated", ANA)  # moving on is fine; closing is not
    holds.decide(s, h_.id, "release", "Different firm, different country.", SOFIA)
    holds.decide_proposal(s, h_.id, True, "", MAYA)
    transition(s, case, "discounted", MAYA, "Not a match", reason=reasons_for(s, "false_positive")[0]["code"])
    assert case.closed is not None
    with pytest.raises(HoldError, match="case is closed"):
        holds.place(s, case, "payment", t.id, "sanctions_potential_match", SOFIA)


def test_a_hold_left_too_long_is_flagged_once_and_raises_the_case(s):
    case, t = hit(s)
    before = case.priority
    h_ = holds.place(s, case, "payment", t.id, "sanctions_potential_match", SOFIA, at=now() - timedelta(hours=6))
    assert holds.row(s, h_)["overdue"]
    assert run_timers(s)["holds_overdue"] == 1 and run_timers(s)["holds_overdue"] == 0
    assert h_.overdue_flagged and (case.priority != before or before == "critical")
    assert [x["what"] for x in h_.history] == ["placed", "review_overdue"]


def test_how_long_a_hold_may_sit_and_whether_it_takes_two_are_the_clients_to_set(s):
    codes = [c | ({"review_hours": 2, "two_person": "yes"} if c["code"] == "suspected_fraud" else {}) for c in reference.codes(s, "hold_reasons")]
    reference.replace(s, "hold_reasons", codes, OMAR)
    case, _ = fraud_case(s)
    h_ = holds.place(s, case, "card", "", "suspected_fraud", FRAN)
    assert 1.9 < (h_.review_by - h_.placed_at).total_seconds() / 3600 < 2.1
    holds.decide(s, h_.id, "release", "Customer confirmed the payments.", FRAN)
    assert h_.status == "active" and h_.proposal is not None


# --- reports to the sanctions authority ---------------------------------------------------------------------------------------------
def blocked_hold(s, outcome="block"):
    case, t = hit(s)
    h_ = holds.place(s, case, "payment", t.id, "sanctions_potential_match", SOFIA)
    holds.decide(s, h_.id, outcome, "Confirmed against the list entry by name and country.", SOFIA)
    holds.decide_proposal(s, h_.id, True, "", MAYA)
    return case, h_


@pytest.mark.parametrize("outcome,kind", [("block", "BLOCK"), ("reject", "REJECT")])
def test_a_blocked_or_rejected_payment_is_owed_a_report_within_ten_business_days(s, outcome, kind):
    case, h_ = blocked_hold(s, outcome)
    owed = reports.owed(s)
    assert [(o["hold_id"], o["kind"], o["amount"]) for o in owed] == [(h_.id, kind, 25000.0)]
    assert datetime.fromisoformat(owed[0]["due"]) == add_business_days(h_.decided_at, 10)
    f = reports.prepare(s, h_.id, MAYA)
    assert (f.kind, f.case_id, f.status, f.format) == (kind, case.id, "prepared", "ofac_report") and f.due == add_business_days(h_.decided_at, 10)
    assert f.payload["target"]["name"] == "Volkov Maritime Trading" and f.payload["target"]["reference"] == "DEMO-001"
    assert "Volkov Maritime Trading" in f.document and "25000.00" in f.document and ("DateBlocked" if kind == "BLOCK" else "DateRejected") in f.document
    assert reports.owed(s) == []
    with pytest.raises(reports.ReportError, match="already exists"):
        reports.prepare(s, h_.id, MAYA)


def test_a_report_needs_the_reporting_permission_and_a_sanctions_hold_that_was_kept(s):
    case, h_ = blocked_hold(s)
    for who in (SOFIA, ANA, AUDRA, OMAR):
        with pytest.raises(reports.ReportError, match="needs the permission"):
            reports.prepare(s, h_.id, who)
    with pytest.raises(reports.ReportError, match="Hold not found"):
        reports.prepare(s, "nope", MAYA)
    fcase, _ = fraud_case(s)
    kept = holds.place(s, fcase, "card", "", "suspected_fraud", FRAN)
    holds.decide(s, kept.id, "block", "Card stays blocked; a new one is issued.", FRAN)
    assert [o["hold_id"] for o in reports.owed(s)] == [h_.id]  # a fraud block is not sanctions property
    with pytest.raises(reports.ReportError, match="Only a sanctions hold"):
        reports.prepare(s, kept.id, MAYA)
    released_case, t = hit(s, "S9")
    r = holds.place(s, released_case, "payment", t.id, "sanctions_potential_match", SOFIA)
    with pytest.raises(reports.ReportError, match="Only a sanctions hold"):
        reports.prepare(s, r.id, MAYA)


def test_a_report_with_missing_details_says_so_and_can_be_rebuilt_and_amended(s):
    case, h_ = blocked_hold(s)
    f = reports.prepare(s, h_.id, MAYA)
    assert any("reporting institution" in p for p in f.problems)  # the test client has no institution details
    with pytest.raises(filing.FilingError, match="cannot be sent yet"):
        filing.submit(s, f.id, MAYA)
    filing.recheck(s, f.id, MAYA)
    assert f.kind == "BLOCK" and f.payload["hold_id"] == h_.id and f.format == "ofac_report"
    f.problems, f.status = [], "submitted"
    filing.acknowledge(s, f.id, "OFAC-1", MAYA)
    new = filing.amend(s, f.id, MAYA)
    assert (new.kind, new.amends_id, new.payload["prior_reference"], f.status) == ("BLOCK", f.id, "OFAC-1", "superseded")
    assert "CorrectsReport" in new.document and reports.owed(s) == []


def test_a_sanctions_report_does_not_lock_the_case_narrative(s):
    case, h_ = blocked_hold(s)
    f = reports.prepare(s, h_.id, MAYA)
    f.status = "submitted"
    s.flush()
    assert filing.locked(s, case.id) is False


# --- disputes -------------------------------------------------------------------------------------------------------------------
def test_a_card_dispute_runs_from_claim_to_full_recovery(s):
    case, txns = fraud_case(s)
    reported = datetime(2026, 6, 1, 10)  # a Monday
    d = disputes.open_dispute(s, case, [t.id for t in txns], "card_not_present", FRAN, reported_at=reported)
    assert (d.amount, d.rail, d.status) == (750.0, "card", "open")
    assert d.credit_due == datetime(2026, 6, 15, 23, 59) and d.resolve_due == reported + timedelta(days=45)  # ten business days is two calendar weeks
    assert d.recovery_due == txns[0].ts + timedelta(days=120)
    with pytest.raises(disputes.DisputeError, match="has not been credited"):
        disputes.close(s, d.id, FRAN)
    disputes.credit(s, d.id, 750, FRAN, at=reported + timedelta(days=3))
    assert d.status == "credited" and not d.credit_final and disputes.row(s, d)["exposure"] == 750.0
    with pytest.raises(disputes.DisputeError, match="before recording money back"):
        disputes.record_recovery(s, d.id, 100, FRAN)
    disputes.start_recovery(s, d.id, "CB-1", FRAN)
    with pytest.raises(disputes.DisputeError, match="already on record"):
        disputes.start_recovery(s, d.id, "CB-2", FRAN)
    disputes.record_recovery(s, d.id, 500, FRAN)
    with pytest.raises(disputes.DisputeError, match=r"At most \$250.00"):
        disputes.record_recovery(s, d.id, 300, FRAN)
    disputes.record_recovery(s, d.id, 250, FRAN)
    disputes.close(s, d.id, FRAN)
    assert (d.status, d.outcome, d.loss, d.recovered, d.credit_final) == ("closed", "recovered", 0.0, 750.0, True)
    with pytest.raises(disputes.DisputeError, match="is closed"):
        disputes.credit(s, d.id, 1, FRAN)
    assert [x["what"] for x in d.history] == ["opened", "provisional_credit", "recovery_started", "recovered", "recovered", "closed"]


@pytest.mark.parametrize("recovered,outcome,loss", [(0, "written_off", 600.0), (150, "partly_recovered", 450.0), (600, "recovered", 0.0)])
def test_what_was_credited_and_not_recovered_is_the_loss(s, recovered, outcome, loss):
    case, txns = fraud_case(s)
    d = disputes.open_dispute(s, case, [t.id for t in txns], "unauthorised", FRAN)
    disputes.credit(s, d.id, 600, FRAN)  # a partial credit
    disputes.start_recovery(s, d.id, "CB-9", FRAN)
    if recovered:
        disputes.record_recovery(s, d.id, recovered, FRAN)
    disputes.close(s, d.id, FRAN)
    assert (d.outcome, d.loss) == (outcome, loss)


@pytest.mark.parametrize("outcome,liability", [("customer_liable", 750.0), ("withdrawn", 0.0)])
def test_a_claim_that_fails_takes_the_credit_back_and_costs_nothing(s, outcome, liability):
    case, txns = fraud_case(s)
    d = disputes.open_dispute(s, case, [t.id for t in txns], "unauthorised", FRAN)
    disputes.credit(s, d.id, 750, FRAN)
    with pytest.raises(disputes.DisputeError, match="Say why"):
        disputes.close(s, d.id, FRAN, outcome)
    disputes.close(s, d.id, FRAN, outcome, "Chip and PIN at a shop the customer uses every week.")
    assert (d.outcome, d.loss, d.credit_amount, d.customer_liability) == (outcome, 0.0, 0.0, liability)
    assert "credit_reversed" in [x["what"] for x in d.history]
    with pytest.raises(disputes.DisputeError, match="Leave the outcome to the figures"):
        disputes.close(s, disputes.open_dispute(s, *fraud_case(s, "F2")[:1], ["t-F2-0"], "unauthorised", FRAN).id, FRAN, "recovered", "trust me, it came back")


def test_a_withdrawn_dispute_frees_its_payments_for_a_new_one(s):
    case, txns = fraud_case(s)
    ids = [t.id for t in txns]
    d = disputes.open_dispute(s, case, ids, "unauthorised", FRAN)
    with pytest.raises(disputes.DisputeError, match="already in a dispute"):
        disputes.open_dispute(s, case, ids[:1], "duplicate", FRAN)
    disputes.close(s, d.id, FRAN, "withdrawn", "Customer found the receipt.")
    assert disputes.open_dispute(s, case, ids[:1], "duplicate", FRAN).amount == 400.0


@pytest.mark.parametrize("rail,credit_days,resolve_days,recovery_days,recovery", [
    ("card", 10, 45, 120, "chargeback"), ("ach", 10, 45, 60, "ACH return"), ("p2p", 10, 45, 0, "recall request"), ("wire", 0, 30, 0, "recall request")])
def test_each_kind_of_payment_has_its_own_clocks(s, rail, credit_days, resolve_days, recovery_days, recovery):
    case, txns = fraud_case(s, payments=((1000, rail),))
    reported = datetime(2026, 6, 3, 9)
    d = disputes.open_dispute(s, case, [txns[0].id], "unauthorised", FRAN, reported_at=reported)
    assert d.credit_due == (add_business_days(reported, credit_days) if credit_days else None)
    assert d.resolve_due == reported + timedelta(days=resolve_days)
    assert d.recovery_due == (txns[0].ts + timedelta(days=recovery_days) if recovery_days else None)
    assert disputes.row(s, d)["recovery_name"] == recovery and disputes.row(s, d)["recovery_by_request"] is (recovery_days == 0)


def test_the_clocks_are_the_clients_to_set(s):
    rules = [c | ({"credit_days": 5, "resolve_days": 20} if c["code"] == "card" else {}) for c in reference.codes(s, "dispute_rules")]
    reference.replace(s, "dispute_rules", rules, OMAR)
    case, txns = fraud_case(s)
    reported = datetime(2026, 6, 1, 10)
    d = disputes.open_dispute(s, case, [t.id for t in txns], "unauthorised", FRAN, reported_at=reported)
    assert d.credit_due == datetime(2026, 6, 8, 23, 59) and d.resolve_due == reported + timedelta(days=20)


def test_business_days_skip_weekends():
    friday = datetime(2026, 6, 5, 16)
    assert add_business_days(friday, 1) == datetime(2026, 6, 8, 23, 59)
    assert add_business_days(friday, 5) == datetime(2026, 6, 12, 23, 59)
    assert add_business_days(datetime(2026, 6, 6, 9), 1) == datetime(2026, 6, 8, 23, 59)  # from a Saturday


def test_a_chargeback_cannot_be_filed_after_the_network_window(s):
    party(s, "F1")
    old = pay(s, "F1", 0, 300, cp="Shop", channel="card", ts=now() - timedelta(days=130))
    case = create_case(s, load_case_types(T)["fraud_claim"], "F1", "high", actor="test")
    d = disputes.open_dispute(s, case, [old.id], "unauthorised", FRAN)
    assert disputes.row(s, d)["recovery_open"] is False
    with pytest.raises(disputes.DisputeError, match="window for a chargeback closed"):
        disputes.start_recovery(s, d.id, "CB-1", FRAN)
    with pytest.raises(disputes.DisputeError, match="Give the reference"):
        disputes.start_recovery(s, d.id, " ", FRAN, at=now() - timedelta(days=20))


@pytest.mark.parametrize("build,why", [
    (lambda s, c, t: disputes.open_dispute(s, c, [], "unauthorised", FRAN), "belong to this customer"),
    (lambda s, c, t: disputes.open_dispute(s, c, ["nope"], "unauthorised", FRAN), "belong to this customer"),
    (lambda s, c, t: disputes.open_dispute(s, c, [t[0].id], "bad luck", FRAN), "Choose a reason"),
    (lambda s, c, t: disputes.open_dispute(s, c, [t[0].id], "unauthorised", AUDRA), "works cases"),
    (lambda s, c, t: disputes.open_dispute(s, c, [t[0].id], "unauthorised", OMAR), "works cases"),
    (lambda s, c, t: disputes.open_dispute(s, c, [t[0].id], "unauthorised", FRAN, reported_at=now() + timedelta(days=1)), "in the future"),
    (lambda s, c, t: disputes.credit(s, disputes.open_dispute(s, c, [t[0].id], "unauthorised", FRAN).id, 400.02, FRAN), "between a cent"),
    (lambda s, c, t: disputes.credit(s, disputes.open_dispute(s, c, [t[0].id], "unauthorised", FRAN).id, 0, FRAN), "between a cent"),
])
def test_a_dispute_refuses_what_makes_no_sense(s, build, why):
    case, txns = fraud_case(s)
    with pytest.raises(disputes.DisputeError, match=why):
        build(s, case, txns)


def test_one_dispute_is_one_kind_of_outgoing_payment(s):
    case, txns = fraud_case(s, payments=((400, "card"), (900, "wire")))
    with pytest.raises(disputes.DisputeError, match="one kind of payment"):
        disputes.open_dispute(s, case, [t.id for t in txns], "unauthorised", FRAN)
    incoming = pay(s, "F1", 5, 50, cp="Refund", channel="card", direction="in")
    with pytest.raises(disputes.DisputeError, match="money that left"):
        disputes.open_dispute(s, case, [incoming.id], "unauthorised", FRAN)
    cash = pay(s, "F1", 6, 50, channel="cash")
    with pytest.raises(disputes.DisputeError, match="cannot be disputed"):
        disputes.open_dispute(s, case, [cash.id], "unauthorised", FRAN)


def test_recovery_outlives_the_case_only_when_the_fraud_was_confirmed(s):
    case, txns = fraud_case(s)
    case.closed, case.disposition = now(), "genuine"
    with pytest.raises(disputes.DisputeError, match="nothing to recover"):
        disputes.open_dispute(s, case, [txns[0].id], "unauthorised", FRAN)
    case.disposition = "fraud_confirmed"
    d = disputes.open_dispute(s, case, [txns[0].id], "unauthorised", FRAN)
    disputes.credit(s, d.id, 400, FRAN, final=True)
    disputes.start_recovery(s, d.id, "CB-3", FRAN)
    assert d.status == "recovery" and d.credit_final
    with pytest.raises(disputes.DisputeError, match="already final"):
        disputes.credit(s, d.id, 100, FRAN)


def test_a_missed_credit_and_a_late_resolution_are_each_flagged_once(s):
    case, txns = fraud_case(s)
    d = disputes.open_dispute(s, case, [t.id for t in txns], "unauthorised", FRAN, reported_at=now() - timedelta(days=50))
    row = disputes.row(s, d)
    assert row["credit_overdue"] and row["resolve_overdue"]
    assert run_timers(s)["dispute_deadlines"] == 2 and run_timers(s)["dispute_deadlines"] == 0
    assert [x["what"] for x in d.history][-2:] == ["credit_overdue", "resolution_overdue"]
    disputes.credit(s, d.id, 750, FRAN)
    late = disputes.row(s, d)
    assert late["credit_late"] and not late["credit_overdue"]


def test_the_desk_totals_follow_the_disputes_given_to_it(s):
    a, ta = fraud_case(s, "F1")
    b, tb = fraud_case(s, "F2", payments=((2000, "wire"),))
    d1 = disputes.open_dispute(s, a, [t.id for t in ta], "card_not_present", FRAN)
    disputes.credit(s, d1.id, 750, FRAN)
    disputes.start_recovery(s, d1.id, "CB-1", FRAN)
    disputes.record_recovery(s, d1.id, 250, FRAN)
    disputes.close(s, d1.id, FRAN)
    d2 = disputes.open_dispute(s, b, [tb[0].id], "account_takeover", FRAN)
    disputes.credit(s, d2.id, 2000, FRAN)
    rows = disputes.all_rows(s)
    total = disputes.summary(rows)
    assert (total["open"], total["open_amount"], total["exposure"], total["closed"], total["loss"], total["recovered"]) == (1, 2000.0, 2000.0, 1, 500.0, 250.0)
    assert total["recovery_rate"] == round(250 / 750, 4)
    assert [(r["reason"], r["claims"], r["loss"]) for r in total["by_reason"]] == [("Someone else took control of the account", 1, 0.0),
                                                                                  ("Card details used online or by phone without the card", 1, 500.0)]
    assert disputes.summary([])["recovery_rate"] is None


# --- searches for law enforcement -----------------------------------------------------------------------------------------------
SUBJECTS = [{"name": "Orla Vance", "dob": "1980-01-01"}, {"name": "Tidewater Supply", "kind": "business", "aliases": "Tidewater Supply Co; TWS"},
            {"name": "Nobody Atall"}]


def books(s):
    party(s, "L1", name="Orla Vance")
    party(s, "L2", name="Orla Vance")  # two customers with one name
    party(s, "L3", name="Someone Else")
    pay(s, "L3", 1, 100, cp="Tidewater Supply Company", ts=now() - timedelta(days=20))
    pay(s, "L3", 2, 100, cp="Tidewater Supply", ts=now() - timedelta(days=400))  # too old to count
    pay(s, "L1", 1, 100, cp="Unrelated Shop")


def test_a_request_is_searched_decided_and_answered(s):
    books(s)
    received = now() - timedelta(days=2)
    r = sharing.receive(s, "LE-1", SUBJECTS, MAYA, received=received)
    assert (r.status, r.due, len(r.subjects)) == ("received", received + timedelta(days=14), 3)
    assert r.subjects[1]["kind"] == "business" and r.subjects[1]["aliases"] == ["Tidewater Supply Co", "TWS"]
    with pytest.raises(SharingError, match="Search the records first"):
        sharing.respond(s, r.id, MAYA)
    sharing.search(s, r.id, MAYA)
    found = sorted((m["subject_no"], m["where"], m["name"]) for m in r.matches)
    assert found == [(1, "customer", "Orla Vance"), (1, "customer", "Orla Vance"), (2, "transaction", "Tidewater Supply Company")]
    assert r.scope["customers"] == 3 and r.scope["subjects"] == 3 and r.status == "searched"
    with pytest.raises(SharingError, match="3 possible matches are still undecided"):
        sharing.respond(s, r.id, MAYA)
    first, second, third = r.matches
    with pytest.raises(SharingError, match="what the decision rests on"):
        sharing.decide_match(s, r.id, first["id"], True, "ok", MAYA)
    sharing.decide_match(s, r.id, first["id"], True, "Date of birth matches the one on file", MAYA)
    sharing.decide_match(s, r.id, second["id"], False, "Born 1994, the subject 1980", MAYA)
    sharing.decide_match(s, r.id, third["id"], True, "Same registered address", HUGO)
    sharing.respond(s, r.id, MAYA)
    assert r.status == "responded" and r.response["positive"] and not r.response["late"]
    assert r.response["subjects"] == [{"no": 1, "name": "Orla Vance", "customers": 1, "transactions": 0},
                                      {"no": 2, "name": "Tidewater Supply", "customers": 0, "transactions": 1}]
    for again in (lambda: sharing.search(s, r.id, MAYA), lambda: sharing.respond(s, r.id, MAYA), lambda: sharing.decide_match(s, r.id, first["id"], False, "changed mind", MAYA)):
        with pytest.raises(SharingError):
            again()


def test_a_search_that_finds_nothing_is_still_recorded(s):
    books(s)
    r = sharing.receive(s, "LE-2", [{"name": "Nobody Atall"}], MAYA)
    sharing.search(s, r.id, MAYA)
    sharing.respond(s, r.id, MAYA)
    assert (r.status, r.matches, r.response["positive"], r.searched_by, r.responded_by) == ("no_match", [], False, "maya", "maya")
    assert r.scope["customers"] == 3 and r.scope["counterparty_names"] == 2


def test_searching_again_keeps_the_decisions_already_made(s):
    books(s)
    r = sharing.receive(s, "LE-3", SUBJECTS[:1], MAYA)
    sharing.search(s, r.id, MAYA)
    sharing.decide_match(s, r.id, r.matches[0]["id"], False, "Different date of birth", MAYA)
    party(s, "L9", name="Orla Vance")
    sharing.search(s, r.id, MAYA)
    assert sorted(m["decision"] for m in r.matches) == ["dismissed", "pending", "pending"]


def test_a_late_answer_is_marked_late_and_an_open_one_shows_overdue(s):
    books(s)
    r = sharing.receive(s, "LE-4", [{"name": "Nobody Atall"}], MAYA, received=now() - timedelta(days=20))
    assert sharing.row(r)["overdue"]
    sharing.search(s, r.id, MAYA)
    sharing.respond(s, r.id, MAYA)
    assert r.response["late"] and not sharing.row(r)["overdue"]


def test_how_long_and_how_far_back_are_the_clients_to_set(s):
    books(s)
    rules = [c | ({"value": 7} if c["code"] == "response_days" else {"value": 24} if c["code"] == "transaction_months" else {}) for c in reference.codes(s, "info_share_rules")]
    reference.replace(s, "info_share_rules", rules, OMAR)
    r = sharing.receive(s, "LE-5", SUBJECTS[1:2], MAYA)
    assert (r.due - r.received).days == 7
    sharing.search(s, r.id, MAYA)
    assert [m["name"] for m in r.matches] == ["Tidewater Supply"]  # the old payment now counts, and the two spellings are one firm


@pytest.mark.parametrize("reference_no,subjects,why", [("", SUBJECTS, "tracking number"), ("LE-9", [], "at least one subject"), ("LE-9", [{"dob": "1980"}], "no name"),
                                                        ("LE-9", "Orla", "at least one subject")])
def test_a_request_must_be_complete(s, reference_no, subjects, why):
    with pytest.raises(SharingError, match=why):
        sharing.receive(s, reference_no, subjects, MAYA)


def test_the_same_request_is_not_recorded_twice(s):
    sharing.receive(s, "LE-1", SUBJECTS, MAYA)
    with pytest.raises(SharingError, match="already on record"):
        sharing.receive(s, "LE-1", SUBJECTS, HUGO)


@pytest.mark.parametrize("who", [ANA, SOFIA, IVAN, FRAN, TARA, OMAR, AUDRA])
def test_requests_are_confidential_to_the_people_named_to_handle_them(s, who):
    books(s)
    r = sharing.receive(s, "LE-1", SUBJECTS, MAYA)
    sharing.search(s, r.id, MAYA)
    assert sharing.overview(s, who) == {"allowed": False, "requests": [], "can_handle": False, "rules": {}}
    for act in (lambda: sharing.detail(s, r.id, who), lambda: sharing.receive(s, "LE-2", SUBJECTS, who), lambda: sharing.search(s, r.id, who),
                lambda: sharing.decide_match(s, r.id, r.matches[0]["id"], True, "a good reason", who), lambda: sharing.respond(s, r.id, who)):
        with pytest.raises(SharingError, match="confidential"):
            act()


def test_a_search_opens_no_case_and_leaves_no_trace_on_the_customer(s):
    books(s)
    r = sharing.receive(s, "LE-1", SUBJECTS, MAYA)
    sharing.search(s, r.id, MAYA)
    sharing.decide_match(s, r.id, r.matches[0]["id"], True, "Date of birth matches", MAYA)
    assert list(s.scalars(select(Case))) == [] and list(s.scalars(select(Alert))) == [] and list(s.scalars(select(CaseEvent))) == []


# --- each client's own, and the seeded demo ---------------------------------------------------------------------------------------
def test_holds_disputes_exemptions_and_requests_are_each_clients_own(db):
    db.onboard("one")
    db.onboard("two")
    with db.tenant("one") as a:
        case, t = hit(a)
        holds.place(a, case, "payment", t.id, "sanctions_potential_match", SOFIA)
        screening.propose_exemption(a, "Some Name", lists.entries(a)[0].id, "A different firm with a similar name.", SOFIA)
        fcase, txns = fraud_case(a)
        disputes.open_dispute(a, fcase, [txns[0].id], "unauthorised", FRAN)
        sharing.receive(a, "LE-1", SUBJECTS, MAYA)
        a.commit()
    with db.tenant("two") as b:
        for model in (Hold, Dispute, ScreeningExemption, InfoShare):
            assert list(b.scalars(select(model))) == []
        assert holds.active(b) == [] and disputes.all_rows(b) == [] and screening.exemptions(b) == [] and sharing.overview(b, MAYA)["requests"] == []
        sharing.receive(b, "LE-1", SUBJECTS, MAYA)  # the same tracking number at another client is no clash


def test_the_demo_shows_every_stage(client):
    d = client.get("/api/sanctions", headers=h("meridian", "maya")).json()
    assert {x["code"] for x in d["lists"]} == {"demo_sanctions", "internal_watch"}
    c = d["counts"]
    assert c["held"] == 1 and c["awaiting_second"] == 1 and c["exemptions_pending"] == 1 and c["exemptions_active"] == 1 and c["reports_owed"] == 2
    assert {o["kind"] for o in d["reports_owed"]} == {"BLOCK", "REJECT"}
    assert [(r["reference"], r["status"]) for r in d["requests"]["requests"]] == [("LE-2026-0427", "searched"), ("LE-2026-0412", "no_match")]
    kinds = [f["kind"] for f in client.get("/api/filings", headers=h("meridian", "maya")).json()["filings"]]
    assert kinds.count("BLOCK") >= 2
    f = client.get("/api/fraud", headers=h("meridian", "fran")).json()
    assert f["summary"]["closed"] == 2 and f["summary"]["open"] == 1 and f["summary"]["loss"] > 0 and len(f["holds"]) == 2
    assert {x["challenge"]["status"] for x in f["holds"]} == {"sent", "failed"}


# --- over HTTP: who may do what -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tenant,who", EVERYONE)
def test_everyone_can_open_the_desks_and_sees_only_what_their_permissions_allow(client, tenant, who):
    perms = _permissions(tenant, who)
    d = client.get("/api/sanctions", headers=h(tenant, who))
    assert d.status_code == 200
    d = d.json()
    assert d["can_manage"] is ("manage_lists" in perms) and d["can_decide"] is ("sanctions_decisions" in perms)
    assert d["requests"]["allowed"] is ("info_sharing" in perms)
    if "info_sharing" not in perms:
        assert d["requests"]["requests"] == []
    assert client.get("/api/fraud", headers=h(tenant, who)).status_code == 200


@pytest.mark.parametrize("tenant,who", EVERYONE)
def test_changing_a_list_over_http_needs_the_permission(client, tenant, who):
    r = client.post("/api/sanctions/lists", headers=h(tenant, who), json={"code": f"probe_{who}", "name": "Probe", "kind": "internal", "rescreen": False,
                                                                           "entries": [{"ref": "P1", "name": "Zzyzx Probe Holdings"}]})
    assert r.status_code == (200 if "manage_lists" in _permissions(tenant, who) else 403), r.text
    assert client.post("/api/sanctions/rescreen", headers=h(tenant, who)).status_code == (200 if "manage_lists" in _permissions(tenant, who) else 403)


@pytest.mark.parametrize("tenant,who", EVERYONE)
def test_law_enforcement_requests_over_http_are_closed_to_everyone_else(client, tenant, who):
    allowed = "info_sharing" in _permissions(tenant, who)
    listed = client.get("/api/sanctions", headers=h(tenant, "maya" if tenant == "meridian" else who)).json()["requests"]["requests"]
    r = client.post("/api/sanctions/requests", headers=h(tenant, who), json={"reference": f"LE-{tenant}-{who}", "subjects": [{"name": "Nobody Atall"}]})
    assert r.status_code == (200 if allowed else 403), r.text
    if tenant == "meridian" and listed:
        assert client.get(f"/api/sanctions/requests/{listed[0]['id']}", headers=h(tenant, who)).status_code == (200 if allowed else 403)


def test_a_request_from_another_client_cannot_be_read(client):
    rid = client.get("/api/sanctions", headers=h("meridian", "maya")).json()["requests"]["requests"][0]["id"]
    for tenant in CLIENTS:
        if tenant != "meridian":
            head = next(w for t, w in EVERYONE if t == tenant and "info_sharing" in _permissions(t, w))
            assert client.get(f"/api/sanctions/requests/{rid}", headers=h(tenant, head)).status_code == 404


def test_the_second_person_decides_a_held_payment_over_http(client):
    held = client.get("/api/sanctions", headers=h("meridian", "maya")).json()["holds"][0]
    assert held["proposal"]["by"] == "sofia" and held["two_person"]
    assert client.post(f"/api/holds/{held['id']}/decision", headers=h("meridian", "sofia"), json={"approve": True}).status_code == 409  # her own proposal
    assert client.post(f"/api/holds/{held['id']}/decision", headers=h("meridian", "ana"), json={"approve": True}).status_code == 403
    assert client.post(f"/api/holds/{held['id']}/decision", headers=h("meridian", "audra"), json={"approve": True}).status_code == 403
    assert client.post(f"/api/holds/{held['id']}/decision", headers=h("meridian", "fran"), json={"approve": True}).status_code == 404  # she cannot see sanctions cases
    done = client.post(f"/api/holds/{held['id']}/decision", headers=h("meridian", "maya"), json={"approve": True, "note": "Agreed"})
    assert done.status_code == 200 and done.json()["status"] == "released"
    assert client.get("/api/sanctions", headers=h("meridian", "maya")).json()["counts"]["held"] == 0


def test_preparing_the_owed_report_over_http(client):
    owed = client.get("/api/sanctions", headers=h("meridian", "maya")).json()["reports_owed"]
    one = next(o for o in owed if o["kind"] == "REJECT")
    assert client.post(f"/api/holds/{one['hold_id']}/report", headers=h("meridian", "sofia")).status_code == 403
    r = client.post(f"/api/holds/{one['hold_id']}/report", headers=h("meridian", "maya"))
    assert r.status_code == 200 and r.json()["kind"] == "REJECT" and r.json()["problems"] == []
    assert client.post(f"/api/holds/{one['hold_id']}/report", headers=h("meridian", "maya")).status_code == 409
    doc = client.get(f"/api/filings/{r.json()['filing_id']}", headers=h("meridian", "maya")).json()
    assert doc["kind"] == "REJECT" and "ReportOfRejectedTransaction" in doc["document"] and "Zarin Exchange House" in doc["document"]


def test_a_fraud_case_over_http_from_hold_to_dispute(client):
    cases = client.get("/api/cases?status=open", headers=h("meridian", "fran")).json()
    case = next(c for c in cases if c["case_type"] == "fraud_claim" and c["state"] == "triage" and not client.get(f"/api/cases/{c['id']}/holds", headers=h("meridian", "fran")).json()["holds"])
    view = client.get(f"/api/cases/{case['id']}/disputes", headers=h("meridian", "fran")).json()
    assert view["can_work"] and view["can_open"] and view["disputes"] == [] and len(view["payments"]) >= 2
    assert client.get(f"/api/cases/{case['id']}/disputes", headers=h("meridian", "sofia")).status_code == 404  # not a case she can see
    assert client.post(f"/api/cases/{case['id']}/holds", headers=h("meridian", "audra"), json={"target": "card", "reason": "suspected_fraud"}).status_code == 403
    hold = client.post(f"/api/cases/{case['id']}/holds", headers=h("meridian", "fran"), json={"target": "card", "reason": "suspected_fraud", "note": "Blocked"})
    assert hold.status_code == 200 and hold.json()["kind"] == "fraud" and not hold.json()["two_person"]
    card = [p["id"] for p in view["payments"] if p["channel"] == "card"][:2]
    d = client.post(f"/api/cases/{case['id']}/disputes", headers=h("meridian", "fran"), json={"txn_ids": card, "reason": "card_not_present"})
    assert d.status_code == 200 and d.json()["status"] == "open" and d.json()["recovery_name"] == "chargeback"
    did = d.json()["id"]
    assert client.post(f"/api/disputes/{did}", headers=h("meridian", "sofia"), json={"step": "credit", "amount": 1}).status_code == 404
    assert client.post(f"/api/disputes/{did}", headers=h("meridian", "audra"), json={"step": "credit", "amount": 1}).status_code == 403
    assert client.post(f"/api/disputes/{did}", headers=h("meridian", "fran"), json={"step": "dance"}).status_code == 400
    credited = client.post(f"/api/disputes/{did}", headers=h("meridian", "fran"), json={"step": "credit", "amount": d.json()["amount"]})
    assert credited.status_code == 200 and credited.json()["status"] == "credited"
    released = client.post(f"/api/holds/{hold.json()['id']}/outcome", headers=h("meridian", "fran"), json={"outcome": "release", "note": "New card issued to the customer."})
    assert released.status_code == 200 and released.json()["status"] == "released"


def test_the_sweep_reports_holds_and_disputes(client):
    out = client.post("/api/timers/run", headers=h("meridian", "tara")).json()
    assert "holds_overdue" in out and "dispute_deadlines" in out
