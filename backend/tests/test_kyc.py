"""KYC beyond the case: beneficial ownership, risk rating and overrides, the evidence locker, flow of funds and review triggers."""
import base64
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

import app.db as appdb
from app import reference
from app.cases.case_types import load_case_types
from app.cases.service import complete_stage_tasks, create_case, transition
from app.cases.timers import run_timers
from app.domain.models import Account, Case, CaseEvent, Evidence, Filing, KycTrigger, MediaItem, OwnershipStructure, Party, RiskAssessment, Transaction
from app.kyc import desk, evidence, flows, ownership, risk, triggers
from app.kyc.demo import pdf
from app.kyc.ownership import OwnershipError, analyse, check
from support import PERSONAS, T
from test_client_api import CLIENTS, EVERYONE, _permissions, h

P = PERSONAS
NOW = datetime(2026, 6, 1, 9)


def person(key, name=None, **kw):
    return {"key": key, "name": name or key.title(), "kind": "person"} | kw


def firm(key, kind="company", **kw):
    return {"key": key, "name": f"{key.title()} Ltd", "kind": kind} | kw


def own(owner, owned, percent, control=False):
    return {"owner": owner, "owned": owned, "percent": percent, "control": control}


def study(nodes, links, **kw):
    return analyse(*check(nodes, links), **kw)


def party(s, pid="K1", **kw):
    p = Party(id=pid, name=kw.pop("name", "Test Customer"), onboarded=kw.pop("onboarded", NOW - timedelta(days=400)), **kw)
    s.add_all([p, Account(id=f"A-{pid}", party_id=pid)])
    s.flush()
    return p


def pay(s, pid, n, amount, direction="out", channel="wire", cp="", country="US", ts=None):
    s.add(Transaction(id=f"t-{pid}-{n}", account_id=f"A-{pid}", party_id=pid, ts=ts or NOW - timedelta(days=30 - n), amount=float(amount), direction=direction,
                      channel=channel, counterparty=cp, counterparty_country=country))
    s.flush()


# --- ownership: the arithmetic --------------------------------------------------------------------------------------------------
def test_shares_multiply_down_a_chain_and_add_across_chains():
    out = study([person("ann"), person("bob"), firm("hold")],
                [own("ann", "customer", 20), own("hold", "customer", 80), own("ann", "hold", 50), own("bob", "hold", 50)])
    eff = {o["key"]: o["effective"] for o in out["owners"]}
    assert eff == {"ann": 60.0, "bob": 40.0} and out["unidentified"] == 0 and out["layers"] == 1
    assert [o["key"] for o in out["beneficial_owners"]] == ["ann", "bob"]


@pytest.mark.parametrize("percent,is_owner", [(25, True), (24.99, False), (100, True)])
def test_the_threshold_is_reached_at_exactly_the_clients_figure(percent, is_owner):
    out = study([person("ann"), person("zed")], [own("ann", "customer", percent), own("zed", "customer", 100 - percent)] if percent < 100 else [own("ann", "customer", 100), own("zed", "customer", 0, True)])
    assert next(o for o in out["owners"] if o["key"] == "ann")["beneficial_owner"] is is_owner


def test_a_different_threshold_changes_who_counts():
    nodes, links = [person("ann"), person("bob")], [own("ann", "customer", 85), own("bob", "customer", 15)]
    assert [o["key"] for o in study(nodes, links)["beneficial_owners"]] == ["ann"]
    assert [o["key"] for o in study(nodes, links, threshold=10)["beneficial_owners"]] == ["ann", "bob"]


def test_control_without_shares_makes_a_beneficial_owner_only_through_an_unbroken_chain():
    nodes = [person("ann"), person("dir"), person("far"), firm("hold")]
    out = study(nodes, [own("ann", "customer", 60), own("dir", "customer", 0, True), own("hold", "customer", 40), own("far", "hold", 10, True)])
    by = {o["key"]: o for o in out["owners"]}
    assert by["dir"]["beneficial_owner"] and by["dir"]["control"] and by["dir"]["effective"] == 0
    assert not by["far"]["beneficial_owner"] and not by["far"]["control"]  # controls the holding company, which does not control the customer
    out = study(nodes[:1] + nodes[2:], [own("ann", "customer", 60), own("hold", "customer", 40, True), own("far", "hold", 10, True)])
    assert next(o for o in out["owners"] if o["key"] == "far")["beneficial_owner"]  # 4% of the shares, but control all the way down


def test_whatever_is_not_traced_to_a_person_is_reported_as_unidentified():
    out = study([person("ann"), firm("shell")], [own("ann", "customer", 30), own("shell", "customer", 50)])
    assert out["unidentified"] == 70.0  # 50% stops at a company with nobody behind it, 20% is held by nobody named
    assert [f["code"] for f in out["flags"]][0] == "opaque" and "70%" in out["flags"][0]["text"]
    clear = study([person("ann"), firm("plc", "listed_company"), firm("state", "government")], [own("ann", "customer", 30), own("plc", "customer", 50), own("state", "customer", 20)])
    assert clear["unidentified"] == 0 and "opaque" not in [f["code"] for f in clear["flags"]]  # a listed company or a government ends a chain openly
    assert not any(o["beneficial_owner"] for o in clear["owners"] if o["kind"] != "person")


def test_a_circle_of_companies_ends_and_is_flagged():
    out = study([firm("a"), firm("b"), person("ann")], [own("a", "customer", 100), own("b", "a", 60), own("a", "b", 100), own("ann", "a", 40)])
    assert "circular" in [f["code"] for f in out["flags"]] and out["unidentified"] == 60.0
    assert next(o for o in out["owners"] if o["key"] == "ann")["effective"] == 40.0


def test_flags_name_what_makes_a_structure_a_concern():
    out = study([firm("l1"), firm("l2"), firm("l3"), person("ann", pep=True, country="IR"), firm("trust", "trust")],
                [own("l1", "customer", 100), own("l2", "l1", 100), own("l3", "l2", 100), own("ann", "l3", 70), own("trust", "l3", 30)])
    codes = [f["code"] for f in out["flags"]]
    assert codes == ["opaque", "complex", "nominee_or_trust", "pep", "high_risk_country", "unverified"] and out["layers"] == 4
    assert next(o for o in out["owners"] if o["key"] == "ann")["layers"] == 3
    nobody = study([person("a"), person("b"), person("c"), person("d"), person("e")], [own(k, "customer", 20) for k in "abcde"])
    assert [f["code"] for f in nobody["flags"]] == ["unverified"] or "no_owner" in [f["code"] for f in nobody["flags"]]
    assert nobody["beneficial_owners"] == []


@pytest.mark.parametrize("nodes,links,says", [
    ([], [], "at least one owner"),
    ([person("ann")], [], "at least one owner"),
    ([person("Ann")], [own("Ann", "customer", 10)], "short key"),
    ([person("customer")], [own("customer", "customer", 10)], "short key"),
    ([person("ann"), person("ann")], [own("ann", "customer", 10)], "appears twice"),
    ([{"key": "ann", "name": " ", "kind": "person"}], [own("ann", "customer", 10)], "needs a name"),
    ([{"key": "ann", "name": "Ann", "kind": "wizard"}], [own("ann", "customer", 10)], "kind must be"),
    ([person("ann", country="Iran")], [own("ann", "customer", 10)], "two-letter"),
    ([person("ann")], [own("bob", "customer", 10)], "names an owner"),
    ([person("ann")], [own("ann", "nowhere", 10)], "names an owner"),
    ([person("ann"), firm("h")], [own("h", "customer", 10), own("ann", "h", 10), own("h", "h", 5)], "cannot own itself"),
    ([person("ann")], [own("ann", "customer", 10), own("ann", "customer", 20)], "appears twice"),
    ([person("ann")], [own("ann", "customer", 0)], "share between"),
    ([person("ann")], [own("ann", "customer", 101)], "share between"),
    ([person("ann")], [own("ann", "customer", -5)], "share between"),
    ([person("ann")], [own("ann", "customer", "half")], "share between"),
    ([person("ann"), person("bob")], [own("ann", "customer", 60), own("bob", "customer", 50)], "More than 100% of customer"),
    ([person("ann"), firm("h")], [own("ann", "h", 50)], "No holding in the customer"),
    ([person("ann"), person("idle")], [own("ann", "customer", 50)], "hold nothing: idle"),
    ([person(f"p{i}") for i in range(61)], [own("p0", "customer", 1)], "at most 60"),
])
def test_a_structure_that_cannot_be_real_is_refused(nodes, links, says):
    with pytest.raises(OwnershipError, match=says):
        check(nodes, links)


# --- ownership: the record ---------------------------------------------------------------------------------------------------
def test_each_change_is_a_new_version_and_only_a_change_of_owners_triggers_a_review(s):
    party(s)
    first = ownership.record(s, "K1", [person("ann"), person("bob")], [own("ann", "customer", 70), own("bob", "customer", 30)], P["kai"], "Register", ts=NOW)
    assert first.version == 1 and first.current and s.query(KycTrigger).count() == 0  # a first record is a baseline, not a change
    same = ownership.record(s, "K1", [person("ann"), person("bob")], [own("ann", "customer", 60), own("bob", "customer", 40)], P["kai"], ts=NOW + timedelta(days=1))
    assert same.version == 2 and not first.current and s.query(KycTrigger).count() == 0  # shares moved, the owners did not
    ownership.record(s, "K1", [person("ann"), person("cy", "Cy Moss")], [own("ann", "customer", 60), own("cy", "customer", 40)], P["kai"], ts=NOW + timedelta(days=2))
    trig = s.query(KycTrigger).one()
    assert trig.kind == "ownership_changed" and "new: Cy Moss" in trig.detail and "no longer: Bob" in trig.detail and trig.outcome == "opened_review"
    view = ownership.view(s, "K1")
    assert view["structure"]["version"] == 3 and [v["version"] for v in view["history"]] == [3, 2, 1]
    assert view["history"][2]["beneficial_owners"] == ["Ann", "Bob"] and s.query(OwnershipStructure).filter_by(current=True).count() == 1


def test_recording_ownership_needs_someone_who_works_cases_and_a_real_customer(s):
    party(s)
    args = ([person("ann")], [own("ann", "customer", 100)])
    for who, says in (("omar", "works cases"), ("audra", "works cases")):
        with pytest.raises(OwnershipError, match=says):
            ownership.record(s, "K1", *args, P[who])
    with pytest.raises(OwnershipError, match="Customer not found"):
        ownership.record(s, "nobody", *args, P["kai"])
    with pytest.raises(OwnershipError, match="More than 100%"):
        ownership.record(s, "K1", [person("ann"), person("bob")], [own("ann", "customer", 80), own("bob", "customer", 80)], P["kai"])
    assert s.query(OwnershipStructure).count() == 0 and ownership.view(s, "K1")["structure"] is None


def test_the_clients_own_threshold_is_applied(s):
    party(s)
    ownership.record(s, "K1", [person("ann"), person("bob")], [own("ann", "customer", 85), own("bob", "customer", 15)], P["kai"])
    assert [o["name"] for o in ownership.view(s, "K1")["analysis"]["beneficial_owners"]] == ["Ann"]
    rules = reference.codes(s, "ownership_rules")
    rules[0]["value"] = 10
    reference.replace(s, "ownership_rules", rules, P["omar"])
    assert [o["name"] for o in ownership.view(s, "K1")["analysis"]["beneficial_owners"]] == ["Ann", "Bob"]
    with pytest.raises(reference.ReferenceError, match="fixed by the platform"):
        reference.replace(s, "ownership_rules", [*rules, {"code": "my_rule", "label": "Mine", "value": 5}], P["omar"])


# --- risk: the model -----------------------------------------------------------------------------------------------------------
def _applies(s, p):
    return {f["code"] for f in risk.score(s, p, NOW)["factors"] if f["applies"]}


def test_a_customer_with_nothing_on_record_scores_only_for_the_missing_identity_document(s):
    p = party(s)
    out = risk.score(s, p, NOW)
    assert _applies(s, p) == {"unverified_identity"} and out["score"] == 10 and out["band"] == "low" and len(out["factors"]) == 12
    assert all(f["evidence"] for f in out["factors"] if f["applies"]) and not any(f["evidence"] for f in out["factors"] if not f["applies"])
    assert s.query(RiskAssessment).count() == 0  # scoring reads; only assessing stores


@pytest.mark.parametrize("attrs,factor", [({"pep": True}, "pep"), ({"country": "IR"}, "high_risk_country"), ({"occupation": "Crypto trader"}, "high_risk_business"),
                                          ({"occupation": "teacher"}, None)])
def test_what_is_recorded_about_the_customer_counts(s, attrs, factor):
    assert _applies(s, party(s, **attrs)) - {"unverified_identity"} == ({factor} if factor else set())


def test_ownership_flags_feed_the_score(s):
    p = party(s)
    ownership.record(s, "K1", [firm("l1"), firm("l2"), firm("l3"), person("ann", pep=True, country="KP"), firm("nom", "nominee")],
                     [own("l1", "customer", 100), own("l2", "l1", 100), own("l3", "l2", 100), own("ann", "l3", 60), own("nom", "l3", 40)], P["kai"])
    assert _applies(s, p) == {"pep", "high_risk_country", "opaque_ownership", "complex_ownership", "nominee_or_trust", "unverified_identity"}
    out = risk.score(s, p, NOW)
    assert out["score"] == 30 + 25 + 25 + 10 + 15 + 10 and out["band"] == "high"
    assert "Ann" in next(f["evidence"] for f in out["factors"] if f["code"] == "pep")


def test_activity_shares_need_enough_payments_to_mean_anything(s):
    p = party(s)
    for n in range(4):
        pay(s, "K1", n, 1000, channel="cash", direction="in")
    assert "cash_intensive" not in _applies(s, p)  # four payments are not a pattern
    pay(s, "K1", 4, 1000, channel="cash", direction="in")
    assert "cash_intensive" in _applies(s, p) and "cross_border" not in _applies(s, p)
    for n in range(5, 9):
        pay(s, "K1", n, 3000, country="DE")
    assert _applies(s, p) >= {"cross_border"} and "cash_intensive" not in _applies(s, p)  # cash is now under half by value
    pay(s, "K1", 99, 10_000_000, channel="cash", ts=NOW - timedelta(days=500))
    assert "cash_intensive" not in _applies(s, p)  # older than a year: outside the model's window


def test_reports_confirmed_cases_and_adverse_media_count(s):
    p = party(s, name="Dana Whitlock")
    s.add(Filing(id="f1", party_id="K1", kind="SAR", status="prepared", filed_by="maya", sha256="0" * 64, filed_at=NOW))
    s.add(MediaItem(id="m1", party_id="K1", source="Daily Ledger", published=NOW, title="Local bakery wins award", body="Dana Whitlock won a baking award."))
    c = create_case(s, load_case_types(T)["sanctions_hit"], "K1", "high", created=NOW - timedelta(days=500))
    c.disposition, c.closed = "true_match", NOW - timedelta(days=400)
    s.flush()
    assert _applies(s, p) == {"unverified_identity"}  # a report not sent, good news, and a case confirmed more than a year ago
    s.query(Filing).one().status = "submitted"
    s.add(MediaItem(id="m2", party_id="K1", source="Court Wire", published=NOW, title="Fraud charges", body="Dana Whitlock was charged with fraud and money laundering."))
    s.add(MediaItem(id="m3", party_id="K1", source="Court Wire", published=NOW, title="Fraud charges", body="Another person entirely was charged with fraud."))
    c.closed = NOW - timedelta(days=30)
    s.flush()
    assert _applies(s, p) == {"prior_report", "adverse_media", "confirmed_alerts", "unverified_identity"}
    assert next(f["evidence"] for f in risk.score(s, p, NOW)["factors"] if f["code"] == "adverse_media").startswith("1 item")


def test_a_verified_identity_document_clears_the_factor_until_it_expires(s):
    p = party(s)
    e = evidence.add(s, "K1", kind="passport", title="Passport", filename="p.pdf", media_type="application/pdf", data=pdf("P", ["x"]), persona=P["kai"],
                     expires=NOW + timedelta(days=10), at=NOW - timedelta(days=5))
    assert "unverified_identity" in _applies(s, p)  # held, but nobody has checked it
    evidence.verify(s, e.id, P["ana"], at=NOW - timedelta(days=4))
    assert "unverified_identity" not in _applies(s, p)
    assert "unverified_identity" in {f["code"] for f in risk.score(s, p, NOW + timedelta(days=11))["factors"] if f["applies"]}


def test_the_client_tunes_points_bands_and_switches_factors_off(s):
    p = party(s, pep=True)
    assert risk.score(s, p, NOW)["score"] == 40 and risk.score(s, p, NOW)["band"] == "medium"
    factors = reference.codes(s, "risk_factors")
    next(f for f in factors if f["code"] == "pep")["points"] = 60
    next(f for f in factors if f["code"] == "unverified_identity")["retired"] = True
    before = risk.score(s, p, NOW)["model"]
    reference.replace(s, "risk_factors", factors, P["omar"])
    out = risk.score(s, p, NOW)
    assert out["score"] == 60 and out["band"] == "high" and len(out["factors"]) == 11 and out["model"] != before
    bands = reference.codes(s, "risk_bands")
    next(b for b in bands if b["code"] == "high")["from_score"] = 80
    reference.replace(s, "risk_bands", bands, P["omar"])
    assert risk.score(s, p, NOW)["band"] == "medium"


# --- risk: assessments and overrides ---------------------------------------------------------------------------------------------
def test_assessing_sets_the_rating_keeps_history_and_only_a_later_rise_triggers_a_review(s):
    p = party(s, pep=True)
    a = risk.assess(s, "K1", "kai", at=NOW)
    assert (a.score, a.band, a.final_band, p.risk_rating, a.current) == (40, "medium", "medium", "medium", True)
    assert s.query(KycTrigger).count() == 0  # the first assessment is the baseline
    p.country = "IR"
    b = risk.assess(s, "K1", "kai", at=NOW + timedelta(days=1))
    assert (b.band, p.risk_rating, a.current, b.current) == ("high", "high", False, True)
    trig = s.query(KycTrigger).one()
    assert trig.kind == "risk_increased" and "medium to high" in trig.detail and trig.outcome == "opened_review"
    p.country, p.pep = "US", False
    risk.assess(s, "K1", "kai", at=NOW + timedelta(days=2))
    assert p.risk_rating == "low" and s.query(KycTrigger).count() == 1  # a fall needs no review
    view = risk.view(s, "K1", P["kai"])
    assert [x["band"] for x in view["history"]] == ["low", "high", "medium"] and view["review_months"] == 60 and view["can_assess"] and not view["stale"]
    with pytest.raises(risk.RiskError, match="not found"):
        risk.assess(s, "nobody", "kai")


def test_raising_a_rating_applies_at_once_and_triggers_a_review(s):
    p = party(s)
    risk.assess(s, "K1", "kai", at=NOW)
    a = risk.propose_override(s, "K1", "high", "emerging_concern", "Branch reports large unexplained cash requests.", P["kai"], at=NOW)
    assert a.override["status"] == "applied" and a.final_band == "high" and a.band == "low" and p.risk_rating == "high"
    assert s.query(KycTrigger).one().kind == "risk_increased"
    view = risk.view(s, "K1", P["maya"])
    assert view["assessment"]["override"]["reason"] == "Concern not yet reflected in the record" and not view["can_decide"]


def test_lowering_a_rating_waits_for_a_second_person_with_the_permission(s):
    p = party(s, pep=True)
    risk.assess(s, "K1", "kai", at=NOW)
    a = risk.propose_override(s, "K1", "low", "regulated_entity", "The customer is a listed utility with a public register.", P["maya"], at=NOW)
    assert a.override["status"] == "pending" and a.final_band == "medium" and p.risk_rating == "medium"  # nothing changes yet
    assert [x["party_id"] for x in risk.pending(s)] == ["K1"]
    for who, says in (("maya", "person who proposed it"), ("kai", "approve risk"), ("tara", "approve risk"), ("audra", "approve risk")):
        with pytest.raises(risk.RiskError, match=says):
            risk.decide_override(s, "K1", True, "", P[who])
    assert risk.view(s, "K1", P["hugo"])["can_decide"] and not risk.view(s, "K1", P["maya"])["can_decide"] and not risk.view(s, "K1", P["kai"])["can_override"]
    with pytest.raises(risk.RiskError, match="already waiting"):
        risk.propose_override(s, "K1", "high", "emerging_concern", "A second proposal while one waits.", P["kai"])
    risk.decide_override(s, "K1", True, "Agreed", P["hugo"], at=NOW)
    assert a.override["status"] == "applied" and a.override["decided_by"] == "hugo" and a.final_band == "low" and p.risk_rating == "low"
    assert risk.pending(s) == [] and s.query(KycTrigger).count() == 0
    with pytest.raises(risk.RiskError, match="No override is waiting"):
        risk.decide_override(s, "K1", True, "", P["hugo"])


def test_a_refused_override_changes_nothing_and_needs_a_reason(s):
    p = party(s, pep=True)
    risk.assess(s, "K1", "kai", at=NOW)
    risk.propose_override(s, "K1", "low", "relationship_knowledge", "Known to the branch for twenty years.", P["kai"])
    with pytest.raises(risk.RiskError, match="Say why"):
        risk.decide_override(s, "K1", False, " ", P["maya"])
    a = risk.decide_override(s, "K1", False, "Familiarity is not a control.", P["maya"])
    assert a.override["status"] == "refused" and a.final_band == "medium" and p.risk_rating == "medium"
    assert risk.view(s, "K1", P["kai"])["can_override"]  # a new proposal can be made


@pytest.mark.parametrize("band,reason,note,who,says", [
    ("low", "regulated_entity", "A proper explanation.", "omar", "works cases"), ("low", "regulated_entity", "A proper explanation.", "audra", "works cases"),
    ("medium", "regulated_entity", "A proper explanation.", "kai", "already rates this customer medium"), ("urgent", "regulated_entity", "A proper explanation.", "kai", "Unknown rating"),
    ("low", "because", "A proper explanation.", "kai", "Choose a reason"), ("low", "regulated_entity", "ok", "kai", "Explain the override")])
def test_a_bad_override_is_refused(s, band, reason, note, who, says):
    party(s, pep=True)
    risk.assess(s, "K1", "kai", at=NOW)
    with pytest.raises(risk.RiskError, match=says):
        risk.propose_override(s, "K1", band, reason, note, P[who])


def test_an_override_needs_an_assessment_to_depart_from(s):
    party(s)
    with pytest.raises(risk.RiskError, match="Assess the customer first"):
        risk.propose_override(s, "K1", "high", "emerging_concern", "A proper explanation.", P["kai"])


def test_an_override_stands_only_while_the_model_still_gives_the_same_answer(s):
    p = party(s, pep=True)
    risk.assess(s, "K1", "kai", at=NOW)
    risk.propose_override(s, "K1", "high", "emerging_concern", "Awaiting source of wealth evidence.", P["kai"], at=NOW)
    again = risk.assess(s, "K1", "kai", at=NOW + timedelta(days=1))
    assert again.band == "medium" and again.final_band == "high" and again.override["carried_from"] and p.risk_rating == "high"
    p.pep = False
    moved = risk.assess(s, "K1", "kai", at=NOW + timedelta(days=2))
    assert moved.band == "low" and moved.override is None and moved.final_band == "low" and p.risk_rating == "low"  # the facts changed, so the override lapsed


def test_a_pending_override_lapses_when_the_customer_is_assessed_again(s):
    party(s, pep=True)
    first = risk.assess(s, "K1", "kai", at=NOW)
    risk.propose_override(s, "K1", "low", "regulated_entity", "The customer is a listed utility.", P["kai"])
    risk.assess(s, "K1", "kai", at=NOW + timedelta(days=1))
    assert first.override["status"] == "lapsed" and risk.pending(s) == []


def test_an_override_can_be_withdrawn_by_its_author_or_an_approver(s):
    p = party(s)
    risk.assess(s, "K1", "kai", at=NOW)
    risk.propose_override(s, "K1", "high", "emerging_concern", "Branch reports unexplained cash requests.", P["kai"])
    with pytest.raises(risk.RiskError, match="Only the person"):
        risk.withdraw_override(s, "K1", P["ana"])
    a = risk.withdraw_override(s, "K1", P["hugo"], "Concern resolved")
    assert a.override["status"] == "withdrawn" and a.final_band == "low" and p.risk_rating == "low"
    with pytest.raises(risk.RiskError, match="No override is in force"):
        risk.withdraw_override(s, "K1", P["kai"])


# --- evidence ----------------------------------------------------------------------------------------------------------------
TEXT = b"Statement\nAccount 004417720931 and card 4111111111111111.\nSSN 512-44-9087. Email dana@example.com, phone +1 415 555 0134.\nDate of birth: 14 March 1979\nNothing else.\n"


def _doc(s, data=TEXT, kind="bank_statement", media_type="text/plain", filename="statement.txt", who="kai", **kw):
    return evidence.add(s, "K1", kind=kind, title=kw.pop("title", "A document"), filename=filename, media_type=media_type, data=data, persona=P[who], **kw)


def test_a_document_is_stored_fingerprinted_and_its_custody_chain_opens(s):
    party(s)
    e = _doc(s, at=NOW)
    import hashlib

    assert e.sha256 == hashlib.sha256(TEXT).hexdigest() and e.size == len(TEXT) and e.key == f"K1/{e.id}/statement.txt" and e.status == "current"
    assert [c["action"] for c in e.custody] == ["received"] and e.custody[0]["prev"] == e.sha256 and evidence.custody_intact(e)
    got, data = evidence.read(s, e.id, P["ana"])
    assert data == TEXT and [(c["action"], c["by"]) for c in e.custody] == [("received", "kai"), ("viewed", "ana")] and evidence.custody_intact(e)
    row = evidence.row(s, e, NOW)
    assert row["kind_label"] == "Bank statement" and row["redactable"] and row["custody_intact"] and not row["expired"]


def test_a_file_changed_in_storage_is_caught_and_not_shown(s):
    party(s)
    e = _doc(s)
    evidence.store(T).write(e.key, TEXT.replace(b"004417720931", b"004417720932"))
    with pytest.raises(evidence.EvidenceError, match="no longer matches its fingerprint"):
        evidence.read(s, e.id, P["ana"])
    s.refresh(e)
    assert e.custody[-1]["action"] == "integrity_failure" and evidence.custody_intact(e)


def test_rewriting_the_custody_record_is_detected(s):
    party(s)
    e = _doc(s)
    evidence.read(s, e.id, P["ana"])
    evidence.read(s, e.id, P["maya"])
    for change in (lambda c: c[1].update(by="nobody"), lambda c: c.pop(1), lambda c: c[0].update(detail="0 bytes")):
        chain = [dict(x) for x in e.custody]
        change(chain)
        e.custody = chain
        assert not evidence.custody_intact(e)
        s.refresh(e)
        assert evidence.custody_intact(e)


@pytest.mark.parametrize("kw,says", [
    ({"kind": "selfie"}, "document type"), ({"data": b""}, "between 1 byte"), ({"data": b"x" * (5 * 1024 * 1024 + 1)}, "between 1 byte"),
    ({"media_type": "application/zip"}, "Only text, PDF"), ({"media_type": "application/x-msdownload"}, "Only text, PDF"),
    ({"media_type": "application/pdf"}, "not what its type says"), ({"media_type": "image/png"}, "not what its type says"), ({"media_type": "image/jpeg"}, "not what its type says"),
    ({"data": b"\xff\xfe\x00bad"}, "UTF-8"), ({"kind": "passport"}, "needs its expiry date"), ({"case_id": "case-nope"}, "not about this customer"),
    ({"who": "omar"}, "works cases"), ({"who": "audra"}, "works cases"), ({"replaces": "ev-nope"}, "not a current document")])
def test_a_document_that_should_not_be_taken_is_refused(s, kw, says):
    party(s)
    with pytest.raises(evidence.EvidenceError, match=says):
        _doc(s, **kw)
    assert s.query(Evidence).count() == 0


def test_a_document_cannot_be_filed_on_another_customers_case_and_names_cannot_escape_the_store(s):
    party(s)
    party(s, "K2")
    other = create_case(s, load_case_types(T)["kyc_refresh"], "K2", "low")
    s.flush()
    with pytest.raises(evidence.EvidenceError, match="not about this customer"):
        _doc(s, case_id=other.id)
    with pytest.raises(evidence.EvidenceError, match="Customer not found"):
        evidence.add(s, "nobody", kind="bank_statement", title="t", filename="f.txt", media_type="text/plain", data=b"x", persona=P["kai"])
    e = _doc(s, filename="../../etc/pass wd?.txt")
    assert e.filename == ".._.._etc_pass_wd_.txt" and e.key.startswith(f"K1/{e.id}/") and "/../" not in e.key


def test_verification_is_by_a_second_person_who_has_opened_the_document(s):
    party(s)
    e = _doc(s)
    with pytest.raises(evidence.EvidenceError, match="someone other than"):
        evidence.verify(s, e.id, P["kai"])
    with pytest.raises(evidence.EvidenceError, match="works cases"):
        evidence.verify(s, e.id, P["audra"])
    evidence.verify(s, e.id, P["ana"], at=NOW)
    assert e.verified_by == "ana" and e.verified_at == NOW and [c["action"] for c in e.custody] == ["received", "checked", "verified"]


def test_redaction_writes_a_second_copy_and_leaves_the_original_alone(s):
    party(s)
    e = _doc(s)
    before = e.sha256
    evidence.redact(s, e.id, P["kai"], ["account_number", "national_id", "email", "phone", "date_of_birth"], ["Statement"])
    _, clean = evidence.read(s, e.id, P["ana"], redacted=True)
    text = clean.decode()
    for secret in ("004417720931", "4111111111111111", "512-44-9087", "dana@example.com", "415 555 0134", "14 March 1979", "Statement"):
        assert secret not in text
    assert "Nothing else." in text and "[REDACTED account number]" in text
    assert {r["kind"]: r["count"] for r in e.redactions} == {"term": 1, "account_number": 2, "national_id": 1, "email": 1, "phone": 1, "date_of_birth": 1}
    assert e.sha256 == before and evidence.read(s, e.id, P["ana"])[1] == TEXT and e.redacted_sha256 != before
    assert [c["action"] for c in e.custody][1:3] == ["opened for redaction", "redacted"] and evidence.custody_intact(e)


def test_redaction_refuses_what_it_cannot_do_honestly(s):
    party(s)
    scan = _doc(s, data=pdf("Scan", ["Account 004417720931"]), media_type="application/pdf", filename="scan.pdf")
    with pytest.raises(evidence.EvidenceError, match="Only text documents"):
        evidence.redact(s, scan.id, P["kai"], ["account_number"])
    e = _doc(s, data=b"No secrets here.\n")
    for kinds, terms, says in (([], [], "Choose what to remove"), (["everything"], [], "Choose what to remove"), (["email"], [], "Nothing of that kind"), ([], ["zz"], "Nothing of that kind")):
        with pytest.raises(evidence.EvidenceError, match=says):
            evidence.redact(s, e.id, P["kai"], kinds, terms)
    with pytest.raises(evidence.EvidenceError, match="no redacted copy"):
        evidence.read(s, e.id, P["ana"], redacted=True)
    with pytest.raises(evidence.EvidenceError, match="works cases"):
        evidence.redact(s, e.id, P["audra"], ["email"])


@pytest.mark.parametrize("text,kinds,terms,out", [
    ("IBAN DE89370400440532013000 ok", ["account_number"], [], "IBAN [REDACTED account number] ok"),
    ("Order 1234567 shipped", ["account_number"], [], "Order 1234567 shipped"),  # seven digits is not an account number
    ("Call 555 0134", ["phone"], [], "Call 555 0134"),  # too short to be sure
    ("born 1 Jan 1980.", ["date_of_birth"], [], "[REDACTED date of birth]"),
    ("Anna and ANNA and Annabel", [], ["anna"], "[REDACTED] and [REDACTED] and [REDACTED]bel"),
    ("a.b@c.io", [], ["ab"], "a.b@c.io"),  # terms under three letters are ignored
])
def test_redaction_patterns_are_narrow(text, kinds, terms, out):
    assert evidence.redact_text(text, kinds, terms)[0] == out


def test_replacing_and_withdrawing_keep_the_old_document(s):
    party(s)
    old = _doc(s, kind="passport", data=pdf("P", ["old"]), media_type="application/pdf", filename="p.pdf", expires=NOW)
    new = _doc(s, kind="passport", data=pdf("P", ["new"]), media_type="application/pdf", filename="p.pdf", expires=NOW + timedelta(days=3650), replaces=old.id)
    assert old.status == "superseded" and old.custody[-1]["detail"] == f"replaced by {new.id}" and new.custody[-1]["action"] == "replaces"
    with pytest.raises(evidence.EvidenceError, match="Say why"):
        evidence.withdraw(s, new.id, P["kai"], " ")
    evidence.withdraw(s, new.id, P["kai"], "Uploaded to the wrong customer")
    assert new.status == "withdrawn" and evidence.read(s, new.id, P["ana"])[1].startswith(b"%PDF")  # still held, still readable
    for fn in (lambda: evidence.withdraw(s, old.id, P["kai"], "x"), lambda: evidence.verify(s, old.id, P["ana"])):
        with pytest.raises(evidence.EvidenceError, match="Only a current document"):
            fn()


# --- triggers ----------------------------------------------------------------------------------------------------------------
def test_an_event_fires_once_opens_a_review_and_later_events_join_it(s):
    party(s)
    a = triggers.fire(s, "K1", "report_filed", "report:1", "A report was filed", NOW)
    assert a.outcome == "opened_review" and triggers.fire(s, "K1", "report_filed", "report:1", "again", NOW) is None
    case = s.query(Case).one()
    assert case.case_type == "kyc_refresh" and case.party_id == "K1" and a.case_id == case.id
    b = triggers.fire(s, "K1", "true_match", "match:1", "A match was confirmed", NOW)
    assert b.outcome == "joined_review" and b.case_id == case.id and s.query(Case).count() == 1
    notes = [e.payload["trigger"] for e in s.query(CaseEvent).filter(CaseEvent.case_id == case.id, CaseEvent.kind == "kyc.trigger")]
    assert notes == ["report_filed", "true_match"]
    c = triggers.fire(s, "K1", "adverse_media", "media:1", "New article", NOW)
    assert c.outcome == "noted" and c.case_id is None  # this client's list only notes adverse media
    assert [t["kind"] for t in triggers.for_party(s, "K1")] and len(triggers.for_party(s, "K1")) == 3


def test_the_client_decides_what_each_event_does(s):
    party(s)
    rules = reference.codes(s, "kyc_triggers")
    next(r for r in rules if r["code"] == "report_filed")["retired"] = True
    next(r for r in rules if r["code"] == "adverse_media")["action"] = "open_review"
    reference.replace(s, "kyc_triggers", rules, P["omar"])
    assert triggers.fire(s, "K1", "report_filed", "report:1", "x", NOW) is None and s.query(KycTrigger).count() == 0
    assert triggers.fire(s, "K1", "adverse_media", "media:1", "x", NOW).outcome == "opened_review"
    assert triggers.fire(s, "K1", "made_up", "x:1", "x", NOW) is None


def test_history_being_loaded_fires_nothing_unless_asked(s):
    party(s)
    with triggers.suspended():
        assert triggers.fire(s, "K1", "report_filed", "report:1", "x", NOW) is None
        with triggers.resumed():
            assert triggers.fire(s, "K1", "report_filed", "report:2", "x", NOW) is not None
        assert triggers.fire(s, "K1", "report_filed", "report:3", "x", NOW) is None
    assert triggers.fire(s, "K1", "report_filed", "report:1", "x", NOW) is not None


def test_the_clock_sweep_finds_documents_expiring_and_expired(s):
    party(s)
    party(s, "K2")
    soon = _doc(s, kind="passport", data=pdf("P", ["a"]), media_type="application/pdf", filename="a.pdf", expires=NOW + timedelta(days=20), title="Soon")
    gone = evidence.add(s, "K2", kind="national_id", title="Gone", filename="b.pdf", media_type="application/pdf", data=pdf("P", ["b"]), persona=P["kai"],
                        expires=NOW - timedelta(days=2))
    _doc(s, kind="passport", data=pdf("P", ["c"]), media_type="application/pdf", filename="c.pdf", expires=NOW + timedelta(days=400), title="Fine")
    old = _doc(s, kind="passport", data=pdf("P", ["d"]), media_type="application/pdf", filename="d.pdf", expires=NOW - timedelta(days=900), title="Replaced")
    old.status = "superseded"
    assert run_timers(s, at=NOW)["kyc_triggers"] == 2 and run_timers(s, at=NOW)["kyc_triggers"] == 0
    got = {t.key: (t.kind, t.outcome) for t in s.query(KycTrigger)}
    assert got == {f"expiring:{soon.id}": ("document_expiring", "noted"), f"expired:{gone.id}": ("document_expired", "opened_review")}
    assert "National identity card 'Gone' expired on 2026-05-30" in s.query(KycTrigger).filter_by(kind="document_expired").one().detail
    assert run_timers(s, at=NOW + timedelta(days=21))["kyc_triggers"] == 1  # the one that was expiring has now expired


def test_a_confirmed_sanctions_match_makes_perpetual_kyc_look_again(s):
    from test_workflows import make_case

    c = make_case(s, case_type="sanctions_hit")
    complete_stage_tasks(s, c, P["sofia"])
    transition(s, c, "escalated", P["sofia"])
    complete_stage_tasks(s, c, P["sofia"])
    transition(s, c, "blocked", P["maya"], "Confirmed against the list entry")
    trig = s.query(KycTrigger).one()
    assert trig.kind == "true_match" and trig.outcome == "opened_review" and s.query(Case).filter(Case.case_type == "kyc_refresh").count() == 1


# --- flow of funds -----------------------------------------------------------------------------------------------------------
def test_money_in_and_out_is_grouped_by_who_it_was_with(s):
    party(s)
    pay(s, "K1", 1, 5000, "in", cp="Acme Payroll")
    pay(s, "K1", 2, 3000, "in", cp="ACME PAYROLL")  # the same name in another case is the same source
    pay(s, "K1", 3, 2000, "in", channel="cash")
    pay(s, "K1", 4, 4000, "out", cp="Rent Co")
    pay(s, "K1", 5, 1000, "out", cp="Far Trader", country="IR")
    out = flows.flow(s, "K1")
    assert (out["total_in"], out["total_out"], out["net"], out["txns"]) == (10000, 5000, 5000, 5)
    assert [(x["name"], x["amount"], x["txns"]) for x in out["sources"]] == [("Acme Payroll", 8000, 2), ("Cash", 2000, 1)]
    far = next(x for x in out["destinations"] if x["name"] == "Far Trader")
    assert far["abroad"] and far["countries"] == ["IR"]
    m = out["measures"]
    assert m["cash_share"] == round(2000 / 15000, 4) and m["abroad_share"] == round(1000 / 15000, 4) and m["high_risk_share"] == m["abroad_share"]
    assert flows.flow(s, "nobody") is None and flows.flow(s, "K1", txn_ids=[])["txns"] == 0


def test_small_counterparties_fold_into_others_but_a_risky_one_never_does(s):
    party(s)
    for n in range(9):
        pay(s, "K1", n, 1000 * (9 - n), cp=f"Payee {n}")
    pay(s, "K1", 20, 5, cp="Tiny Risky", country="KP")
    out = flows.flow(s, "K1")["destinations"]
    assert len(out) == 7 and out[-1]["name"] == "4 others" and out[-1]["other"] and out[-1]["amount"] == 4000 + 3000 + 2000 + 1000
    assert "Tiny Risky" in [x["name"] for x in out] and sum(x["amount"] for x in out) == 45005


def test_the_share_that_left_within_days_matches_outflows_to_the_oldest_money(s):
    party(s)
    pay(s, "K1", 1, 10000, "in", ts=NOW - timedelta(days=20))
    pay(s, "K1", 2, 4000, "out", ts=NOW - timedelta(days=19))  # next day: rapid
    pay(s, "K1", 3, 3000, "out", ts=NOW - timedelta(days=10))  # ten days later: not rapid
    pay(s, "K1", 4, 10000, "in", ts=NOW - timedelta(days=5))
    pay(s, "K1", 5, 9000, "out", ts=NOW - timedelta(days=4))  # 3,000 is the old money, 6,000 is yesterday's
    assert flows.flow(s, "K1")["measures"]["rapid_out_share"] == 0.5
    assert flows.flow(s, "K1", since=NOW - timedelta(days=6))["measures"]["rapid_out_share"] == 0.9
    assert flows.flow(s, "K1", txn_ids=["t-K1-1", "t-K1-2"])["total_out"] == 4000


def test_an_outflow_with_no_inflow_before_it_is_not_called_rapid(s):
    party(s)
    pay(s, "K1", 1, 5000, "out", ts=NOW - timedelta(days=9))
    pay(s, "K1", 2, 5000, "in", ts=NOW - timedelta(days=8))
    assert flows.flow(s, "K1")["measures"]["rapid_out_share"] == 0.0


# --- the desk ------------------------------------------------------------------------------------------------------------------
def test_the_review_schedule_follows_the_rating_and_the_last_review(s):
    at = NOW
    party(s, "LOW-OLD", risk_rating="low", onboarded=at - timedelta(days=365 * 6))
    party(s, "LOW-NEW", risk_rating="low", onboarded=at - timedelta(days=365 * 2))
    party(s, "HIGH-DUE", risk_rating="high", onboarded=at - timedelta(days=400))
    party(s, "HIGH-SOON", risk_rating="high", onboarded=at - timedelta(days=330))
    party(s, "MED-DONE", risk_rating="medium", onboarded=at - timedelta(days=365 * 8))
    done = create_case(s, load_case_types(T)["kyc_refresh"], "MED-DONE", "low", created=at - timedelta(days=200))
    done.disposition, done.closed = "refreshed", at - timedelta(days=190)
    s.flush()
    out = desk.schedule(s, at)
    assert {r["party_id"]: r["state"] for r in out["rows"]} == {"HIGH-DUE": "overdue", "LOW-OLD": "overdue", "HIGH-SOON": "due_soon"}
    assert [r["party_id"] for r in out["rows"]] == ["HIGH-DUE", "LOW-OLD", "HIGH-SOON"] and out["counts"] == {"overdue": 2, "due_soon": 1, "in_review": 0, "customers": 5}
    assert out["cycle"] == {"low": 60, "medium": 36, "high": 12}
    case = desk.start_review(s, "LOW-OLD", P["kai"], "Overdue")
    assert case.case_type == "kyc_refresh" and next(r for r in desk.schedule(s, at)["rows"] if r["party_id"] == "LOW-OLD")["state"] == "in_review"
    for pid, who, says in (("LOW-OLD", "kai", "already open"), ("nobody", "kai", "not found"), ("HIGH-DUE", "audra", "works cases"), ("HIGH-DUE", "omar", "works cases")):
        with pytest.raises(desk.DeskError, match=says):
            desk.start_review(s, pid, P[who])


# --- over HTTP, against the seeded clients -------------------------------------------------------------------------------------
def _company(client, name, tenant="meridian"):
    desk_ = client.get("/api/kyc", headers=h(tenant, "audra" if tenant == "meridian" else None)).json()
    return next(x for x in desk_["structures"] if x["party_name"] == name)["party_id"]


def _who(tenant, permission, has=True):
    return next(p for t, p in EVERYONE if t == tenant and (permission in _permissions(t, p)) is has)


@pytest.mark.parametrize("tenant", CLIENTS)
def test_everyone_can_read_the_desk_and_it_holds_only_their_clients_customers(client, tenant):
    for t, who in EVERYONE:
        if t != tenant:
            continue
        r = client.get("/api/kyc", headers=h(tenant, who))
        perms = _permissions(tenant, who)
        assert r.status_code == 200 and r.json()["can_approve"] is ("approve_risk" in perms), who
    out = client.get("/api/kyc", headers=h(tenant)).json()
    with appdb.session_scope(tenant) as s:
        has_kyc = "kyc_refresh" in load_case_types(tenant)
        mine = set(s.scalars(select(Party.id)))
    assert {r["party_id"] for r in out["schedule"]["rows"]} <= mine and out["schedule"]["counts"]["customers"] == len(mine)
    assert len(out["structures"]) == (4 if has_kyc else 0) and len(out["pending_overrides"]) == (1 if has_kyc else 0)
    if has_kyc:
        kinds = {t["kind"]: t["outcome"] for t in out["triggers"]}
        assert kinds == {"document_expired": "opened_review", "document_expiring": "noted", "ownership_changed": "opened_review", "risk_increased": "opened_review"} \
            or kinds["risk_increased"] in ("opened_review", "joined_review")
        assert {d["title"] for d in out["documents"]} == {"Passport of Eleanor Ward", "Passport of Margaret Finch"}


def test_the_seeded_structures_show_what_each_was_built_to_show(client):
    by = {x["party_name"]: x for x in client.get("/api/kyc", headers=h("meridian", "kai")).json()["structures"]}
    assert by["Halcyon Imports Ltd"]["beneficial_owners"] == ["Eleanor Ward", "Daniel Osei"] and by["Halcyon Imports Ltd"]["flags"] == []
    north = by["Northwind Holdings LLC"]
    assert north["beneficial_owners"] == [] and north["unidentified"] == 80 and {f["code"] for f in north["flags"]} == {"opaque", "nominee_or_trust", "no_owner"} and north["version"] == 2
    assert by["Castellan Marine Services Inc"]["beneficial_owners"] == ["Viktor Halberg"] and {f["code"] for f in by["Castellan Marine Services Inc"]["flags"]} == {"pep", "unverified"}
    kyc = client.get(f"/api/customers/{north['party_id']}/kyc", headers=h("meridian", "kai")).json()
    assert kyc["risk"]["rating"] == "medium" and [x["band"] for x in kyc["risk"]["history"]] == ["medium", "low"] and len(kyc["ownership"]["history"]) == 2
    assert [t["kind"] for t in kyc["triggers"]] == ["risk_increased", "ownership_changed"] and kyc["triggers"][0]["case_id"] == kyc["triggers"][1]["case_id"]
    castellan = client.get(f"/api/customers/{by['Castellan Marine Services Inc']['party_id']}/kyc", headers=h("meridian", "kai")).json()["risk"]
    assert castellan["rating"] == "high" and castellan["assessment"]["band"] == "medium" and castellan["assessment"]["override"]["status"] == "applied"
    assert client.get("/api/customers/nobody/kyc", headers=h("meridian", "kai")).status_code == 404


def test_a_lower_rating_is_decided_over_http_by_the_right_person_only(client):
    pid = _company(client, "Halcyon Imports Ltd")
    view = client.get(f"/api/customers/{pid}/kyc", headers=h("meridian", "kai")).json()["risk"]
    assert view["rating"] == "medium" and view["assessment"]["override"]["status"] == "pending" and not view["can_decide"] and not view["can_override"]
    assert client.post(f"/api/customers/{pid}/risk/decision", headers=h("meridian", "kai"), json={"approve": True}).status_code == 403  # proposed it, and lacks the permission
    assert client.post(f"/api/customers/{pid}/risk/decision", headers=h("meridian", "tara"), json={"approve": True}).status_code == 403
    assert client.post(f"/api/customers/{pid}/risk/decision", headers=h("meridian", "audra"), json={"approve": True}).status_code == 403
    assert client.get(f"/api/customers/{pid}/kyc", headers=h("meridian", "maya")).json()["risk"]["can_decide"]
    r = client.post(f"/api/customers/{pid}/risk/decision", headers=h("meridian", "maya"), json={"approve": True, "note": "Controls evidenced"})
    assert r.status_code == 200 and r.json()["rating"] == "low" and r.json()["assessment"]["override"]["decided_by"] == "maya"
    assert client.post(f"/api/customers/{pid}/risk/decision", headers=h("meridian", "hugo"), json={"approve": True}).status_code == 409
    assert client.get("/api/kyc", headers=h("meridian", "maya")).json()["pending_overrides"] == []
    assert client.get(f"/api/customers/{pid}", headers=h("meridian", "maya")).json()["party"]["risk_rating"] == "low"


def test_assessing_and_overriding_over_http_follow_the_same_rules(client):
    pid = _company(client, "Sable and Finch Jewellers Inc")
    for who in ("omar", "audra"):
        assert client.post(f"/api/customers/{pid}/risk/assess", headers=h("meridian", who)).status_code == 403
    r = client.post(f"/api/customers/{pid}/risk/assess", headers=h("meridian", "kai"))
    assert r.status_code == 200 and r.json()["assessment"]["score"] == 15 and r.json()["rating"] == "low"  # the certificate of incorporation still proves who the company is
    bad = client.post(f"/api/customers/{pid}/risk/override", headers=h("meridian", "kai"), json={"band": "high", "reason": "nonsense", "note": "A long enough note."})
    assert bad.status_code == 409 and "Choose a reason" in bad.json()["detail"]
    ok = client.post(f"/api/customers/{pid}/risk/override", headers=h("meridian", "kai"), json={"band": "high", "reason": "emerging_concern", "note": "Trade press reports a supplier investigation."})
    assert ok.status_code == 200 and ok.json()["rating"] == "high"
    back = client.post(f"/api/customers/{pid}/risk/withdraw", headers=h("meridian", "kai"), json={"why": "Report retracted"})
    assert back.status_code == 200 and back.json()["rating"] == "low"
    assert client.post("/api/customers/nobody/risk/assess", headers=h("meridian", "kai")).status_code == 404


def test_ownership_can_be_tried_before_it_is_recorded(client):
    pid = _company(client, "Sable and Finch Jewellers Inc")
    draft = {"nodes": [person("finch", "Margaret Finch", verified=True), firm("holding")], "links": [own("holding", "customer", 100, True), own("finch", "holding", 49)]}
    preview = client.post(f"/api/customers/{pid}/ownership/check", headers=h("meridian", "audra"), json=draft)
    assert preview.status_code == 200 and preview.json()["unidentified"] == 51 and preview.json()["beneficial_owners"][0]["effective"] == 49
    assert client.get(f"/api/customers/{pid}/kyc", headers=h("meridian", "kai")).json()["ownership"]["structure"]["version"] == 1  # a check saves nothing
    broken = client.post(f"/api/customers/{pid}/ownership/check", headers=h("meridian", "kai"), json={"nodes": draft["nodes"], "links": [own("finch", "holding", 49)]})
    assert broken.status_code == 409 and "No holding in the customer" in broken.json()["detail"]
    for who in ("audra", "omar"):
        assert client.put(f"/api/customers/{pid}/ownership", headers=h("meridian", who), json=draft).status_code == 403
    saved = client.put(f"/api/customers/{pid}/ownership", headers=h("meridian", "kai"), json=draft | {"source": "Annual return"})
    assert saved.status_code == 200 and saved.json()["structure"]["version"] == 2 and saved.json()["structure"]["recorded_by"] == "kai"
    assert client.put("/api/customers/nobody/ownership", headers=h("meridian", "kai"), json=draft).status_code == 404


def test_documents_over_http_upload_download_redact_and_stay_inside_the_client(client):
    pid = _company(client, "Halcyon Imports Ltd")
    body = {"kind": "correspondence", "title": "Letter from the customer", "filename": "letter.txt", "media_type": "text/plain",
            "content_base64": base64.b64encode(b"Please use account 004417720931. Regards, E. Ward (eward@example.com)").decode()}
    for who in ("audra", "omar"):
        assert client.post(f"/api/customers/{pid}/evidence", headers=h("meridian", who), json=body).status_code == 403
    assert client.post(f"/api/customers/{pid}/evidence", headers=h("meridian", "kai"), json=body | {"content_base64": "not base64!"}).status_code == 409
    assert client.post(f"/api/customers/{pid}/evidence", headers=h("meridian", "kai"), json=body | {"expires": "soon"}).status_code == 400
    assert client.post(f"/api/customers/{pid}/evidence", headers=h("meridian", "kai"), json=body | {"case_id": "case-nope"}).status_code == 404
    made = client.post(f"/api/customers/{pid}/evidence", headers=h("meridian", "kai"), json=body)
    assert made.status_code == 200 and made.json()["uploaded_by"] == "kai" and made.json()["redactable"]
    eid = made.json()["id"]
    got = client.get(f"/api/evidence/{eid}/content", headers=h("meridian", "audra"))
    assert got.status_code == 200 and b"004417720931" in got.content and got.headers["x-evidence-sha256"] == made.json()["sha256"]
    assert "attachment" in got.headers["content-disposition"] and got.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/evidence/{eid}/content?redacted=true", headers=h("meridian", "audra")).status_code == 409
    assert client.post(f"/api/evidence/{eid}/redact", headers=h("meridian", "audra"), json={"kinds": ["email"]}).status_code == 403
    red = client.post(f"/api/evidence/{eid}/redact", headers=h("meridian", "kai"), json={"kinds": ["account_number", "email"]})
    assert red.status_code == 200 and red.json()["redacted"] and {r["kind"] for r in red.json()["redactions"]} == {"account_number", "email"}
    clean = client.get(f"/api/evidence/{eid}/content?redacted=true", headers=h("meridian", "audra"))
    assert b"004417720931" not in clean.content and b"eward@" not in clean.content and "redacted-letter.txt" in clean.headers["content-disposition"]
    assert client.post(f"/api/evidence/{eid}/verify", headers=h("meridian", "kai")).status_code == 409  # not by the uploader
    assert client.post(f"/api/evidence/{eid}/verify", headers=h("meridian", "ana")).json()["verified_by"] == "ana"
    doc = next(d for d in client.get(f"/api/customers/{pid}/kyc", headers=h("meridian", "kai")).json()["evidence"]["documents"] if d["id"] == eid)
    assert [c["action"] for c in doc["custody"]] == ["received", "downloaded", "opened for redaction", "redacted", "downloaded", "checked", "verified"] and doc["custody_intact"]
    for other in (c for c in CLIENTS if c != "meridian"):  # another client cannot reach the document by its id
        assert client.get(f"/api/evidence/{eid}/content", headers=h(other)).status_code == 404
        assert client.post(f"/api/evidence/{eid}/withdraw", headers=h(other, _who(other, "work")), json={"why": "x"}).status_code == 404
    assert client.post(f"/api/evidence/{eid}/withdraw", headers=h("meridian", "kai"), json={"why": "Sent in error"}).json()["status"] == "withdrawn"
    assert client.get("/api/evidence/ev-nope/content", headers=h("meridian", "kai")).status_code == 404


def test_a_document_filed_on_a_restricted_case_is_hidden_with_the_case(client):
    with appdb.session_scope("meridian") as s:
        doc = s.scalars(select(Evidence).where(Evidence.case_id.is_not(None))).first()
        eid, cid, pid = doc.id, doc.case_id, doc.party_id
    assert client.get(f"/api/evidence/{eid}/content?redacted=true", headers=h("meridian", "kai")).status_code == 200
    r = client.post(f"/api/cases/{cid}/restrict", headers=h("meridian", "maya"), json={"restricted": True, "reason": "Subject is a member of staff"})
    assert r.status_code == 200
    assert client.get(f"/api/evidence/{eid}/content", headers=h("meridian", "ana")).status_code == 404
    assert client.post(f"/api/evidence/{eid}/redact", headers=h("meridian", "ana"), json={"kinds": ["email"]}).status_code == 404
    seen = client.get(f"/api/customers/{pid}/kyc", headers=h("meridian", "ana")).json()["evidence"]
    assert eid not in [d["id"] for d in seen["documents"]] and seen["hidden"] == 1
    cleared = client.get(f"/api/customers/{pid}/kyc", headers=h("meridian", "maya")).json()["evidence"]
    assert eid in [d["id"] for d in cleared["documents"]] and cleared["hidden"] == 0
    client.post(f"/api/cases/{cid}/restrict", headers=h("meridian", "maya"), json={"restricted": False, "reason": "Connection disproved"})


def test_flow_of_funds_over_http_for_a_customer_and_for_one_case(client):
    pid = _company(client, "Halcyon Imports Ltd")
    out = client.get(f"/api/customers/{pid}/flow", headers=h("meridian", "kai")).json()
    held = 48200  # the payment the sanctions demo holds on a possible match: counted, because it was instructed
    assert out["txns"] == 8 and out["total_out"] == 48200 + 31750 + 27900 + 39900 + held and out["destinations"][0]["name"] == "Rheinhafen Spedition GmbH"
    assert out["destinations"][0]["txns"] == 2 and out["measures"]["abroad_share"] > 0.4
    assert client.get(f"/api/customers/{pid}/flow?days=30", headers=h("meridian", "kai")).json()["txns"] < 8
    rows = client.get("/api/cases?status=all", headers=h("meridian", "audra")).json()
    case = next(c for c in rows if c["case_type"] == "tm_alert_investigation" and c["alerts"] and not c["restricted"])
    one = client.get(f"/api/customers/{case['party_id']}/flow?case_id={case['id']}", headers=h("meridian", "audra")).json()
    full = client.get(f"/api/customers/{case['party_id']}/flow", headers=h("meridian", "audra")).json()
    assert 0 < one["txns"] < full["txns"]
    assert client.get(f"/api/customers/{pid}/flow?case_id={case['id']}", headers=h("meridian", "audra")).status_code == 404  # not this customer's case
    assert client.get("/api/customers/nobody/flow", headers=h("meridian", "kai")).status_code == 404


def test_a_review_is_started_from_the_desk_once(client):
    out = client.get("/api/kyc", headers=h("meridian", "kai")).json()
    assert out["can_start"] and out["schedule"]["counts"]["overdue"] > 0
    due = next(r for r in out["schedule"]["rows"] if r["state"] == "overdue")
    for who in ("audra", "omar"):
        assert client.post(f"/api/customers/{due['party_id']}/review", headers=h("meridian", who), json={}).status_code == 403
    r = client.post(f"/api/customers/{due['party_id']}/review", headers=h("meridian", "kai"), json={"why": "Periodic review overdue"})
    assert r.status_code == 200 and client.get(f"/api/cases/{r.json()['case_id']}", headers=h("meridian", "kai")).json()["case"]["case_type"] == "kyc_refresh"
    assert client.post(f"/api/customers/{due['party_id']}/review", headers=h("meridian", "kai"), json={}).status_code == 409
    fintech = next(c for c in CLIENTS if c not in ("meridian", "alder"))
    assert not client.get("/api/kyc", headers=h(fintech)).json()["can_start"]
