"""Day-to-day casework at volume: code lists, closing reasons, bulk steps, merges, QA scorecards and alerts that wait."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

import app.db as appdb
from app import reference
from app.alerts.hub import ingest_external_alert, sweep_hibernated, wake
from app.cases import bulk, merge, scorecard
from app.cases.case_types import load_case_types
from app.cases.service import TransitionError, allowed_transitions, complete_stage_tasks, create_case, transition
from app.cases.timers import run_timers
from app.domain.models import Alert, Case, CaseEvent, CodeList, InfoRequest, Party
from support import PERSONAS, T
from test_client_api import CLIENTS, EVERYONE, _permissions, h
from test_workflows import make_case

P = PERSONAS
T0 = datetime(2026, 5, 1, 9)
ALL_MET = {"decision_supported": {"mark": "met"}, "rationale_clear": {"mark": "met"}, "procedure_followed": {"mark": "met"},
           "evidence_complete": {"mark": "met"}, "timely": {"mark": "met"}}


def _review(s, monkeypatch, pid="P1"):
    """A closed case and the QA review opened on it."""
    monkeypatch.setattr("app.cases.qa.sampled", lambda case_id, rate: True)
    c = make_case(s, pid=pid)
    complete_stage_tasks(s, c, P["ana"])
    transition(s, c, "closed_no_sar", P["ana"], "benign", reason="consistent_with_history")
    s.flush()
    return c, s.query(Case).filter(Case.parent_id == c.id).one()


# --- code lists ----------------------------------------------------------------------------------------------------------
def test_every_client_starts_with_the_library_lists_and_can_replace_one(s):
    assert [x["id"] for x in reference.describe(s)][:4] == ["disposition_reasons", "qa_criteria", "qa_root_causes", "alert_hibernation"]
    assert not any(x["own"] for x in reference.describe(s)) and reference.codes(s, "alert_hibernation") == []
    mine = [*reference.codes(s, "qa_root_causes"), {"code": "system_outage", "label": "  A system was unavailable  "}]
    reference.replace(s, "qa_root_causes", mine, P["omar"])
    row = s.scalars(select(CodeList)).one()
    assert row.updated_by == "omar" and reference.codes(s, "qa_root_causes")[-1] == {"code": "system_outage", "label": "A system was unavailable"}
    assert [x["id"] for x in reference.describe(s) if x["own"]] == ["qa_root_causes"]


def test_a_retired_code_stays_readable_but_is_no_longer_offered(s):
    codes = reference.codes(s, "disposition_reasons")
    codes[0]["retired"] = True
    reference.replace(s, "disposition_reasons", codes, P["omar"])
    assert codes[0]["code"] not in {c["code"] for c in reference.codes(s, "disposition_reasons")}
    assert codes[0]["code"] in {c["code"] for c in reference.codes(s, "disposition_reasons", include_retired=True)}
    assert reference.label(s, "disposition_reasons", codes[0]["code"]) == codes[0]["label"]
    assert reference.label(s, "disposition_reasons", "never_existed") == "never existed"


@pytest.mark.parametrize("list_id,items,says", [
    ("nonsense", [{"code": "ab", "label": "x"}], "Unknown code list"),
    ("qa_root_causes", [], "at least one code"),
    ("qa_root_causes", "late", "at least one code"),
    ("qa_root_causes", [{"code": "Late", "label": "x"}], "short id"),
    ("qa_root_causes", [{"code": "9late", "label": "x"}], "short id"),
    ("qa_root_causes", ["late"], "short id"),
    ("qa_root_causes", [{"code": "late", "label": "x"}, {"code": "late", "label": "y"}], "appears twice"),
    ("qa_root_causes", [{"code": "late", "label": "   "}], "needs a label"),
    ("qa_criteria", [{"code": "timely", "label": "x", "weight": "heavy"}], "positive number"),
    ("qa_criteria", [{"code": "timely", "label": "x", "weight": 0}], "positive number"),
    ("qa_criteria", [{"code": "timely", "label": "x", "weight": True}], "positive number"),
    ("disposition_reasons", [{"code": "weak_match", "label": "x", "applies_to": "false_positive"}], "list of names"),
    ("alert_hibernation", [{"code": "weak", "label": "x", "scenario": 4, "below": 0.5, "days": 30}], "must be text"),
    ("qa_root_causes", [{"code": "late", "label": "x", "retired": True}], "must stay in use"),
])
def test_a_bad_code_list_is_refused_with_a_reason(list_id, items, says):
    with pytest.raises(reference.ReferenceError, match=says):
        reference.check(list_id, items)


def test_codes_are_retired_not_removed_except_where_the_list_is_a_set_of_rules(s):
    with pytest.raises(reference.ReferenceError, match="Retire codes instead of removing them.*late"):
        reference.replace(s, "qa_root_causes", [c for c in reference.codes(s, "qa_root_causes") if c["code"] != "late"], P["omar"])
    assert s.scalars(select(CodeList)).first() is None
    rule = {"code": "weak", "label": "Weak", "scenario": "*", "below": 0.5, "days": 10}
    reference.replace(s, "alert_hibernation", [rule], P["omar"])
    assert reference.replace(s, "alert_hibernation", [], P["omar"]) == []  # rules can simply be withdrawn


def test_unknown_fields_never_reach_a_stored_list(s):
    reference.replace(s, "qa_root_causes", [*reference.codes(s, "qa_root_causes"), {"code": "other", "label": "Other", "weight": 5, "script": "x"}], P["omar"])
    assert reference.codes(s, "qa_root_causes")[-1] == {"code": "other", "label": "Other"}


# --- reasons for closing -------------------------------------------------------------------------------------------------
def test_closing_without_a_report_needs_a_reason_the_client_offers_for_that_outcome(s):
    c = make_case(s)
    complete_stage_tasks(s, c, P["ana"])
    offered = next(t for t in allowed_transitions(s, c, P["ana"]) if t["to"] == "closed_no_sar")["reasons"]
    assert {r["code"] for r in offered} == {"explained_by_business", "explained_by_documents", "consistent_with_history", "one_off_event", "data_quality"}
    for bad in ("", "weak_match", "made_up"):  # none, one meant for another outcome, and one that does not exist
        with pytest.raises(TransitionError, match="reason"):
            transition(s, c, "closed_no_sar", P["ana"], "benign", reason=bad)
    assert c.closed is None
    transition(s, c, "closed_no_sar", P["ana"], "benign", reason="one_off_event")
    assert c.data["reason_code"] == "one_off_event"
    closed = [e for e in s.query(CaseEvent).filter(CaseEvent.case_id == c.id) if e.payload.get("reason_code")]
    assert len(closed) == 1 and closed[0].payload["reason"].startswith("One-off event")


def test_a_step_that_closes_nothing_offers_no_reasons_and_takes_none(s):
    c = make_case(s)
    complete_stage_tasks(s, c, P["ana"])
    assert next(t for t in allowed_transitions(s, c, P["ana"]) if t["to"] == "investigation")["reasons"] == []
    transition(s, c, "investigation", P["ana"])
    assert "reason_code" not in (c.data or {})


def test_a_client_that_rewrites_its_reasons_is_held_to_its_own_list(s):
    mine = [dict(r, retired=True) for r in reference.codes(s, "disposition_reasons")] + [
        {"code": "payroll_cycle", "label": "Matches the customer's payroll cycle", "applies_to": ["no_sar"]}]
    reference.replace(s, "disposition_reasons", mine, P["omar"])
    c = make_case(s)
    complete_stage_tasks(s, c, P["ana"])
    assert [r["code"] for r in next(t for t in allowed_transitions(s, c, P["ana"]) if t["to"] == "closed_no_sar")["reasons"]] == ["payroll_cycle"]
    with pytest.raises(TransitionError, match="reason"):
        transition(s, c, "closed_no_sar", P["ana"], "benign", reason="one_off_event")  # retired here
    transition(s, c, "closed_no_sar", P["ana"], "benign", reason="payroll_cycle")
    assert c.closed is not None


# --- many cases at once ---------------------------------------------------------------------------------------------------
def test_a_bulk_step_reports_each_case_and_leaves_refused_ones_untouched(s):
    ready, not_ready, done = make_case(s, pid="P1"), make_case(s, pid="P2"), make_case(s, pid="P3")
    complete_stage_tasks(s, ready, P["ana"])
    complete_stage_tasks(s, done, P["ana"])
    transition(s, done, "closed_no_sar", P["ana"], "benign", reason="one_off_event")
    s.flush()
    before = s.query(CaseEvent).filter(CaseEvent.case_id == not_ready.id).count()
    out = bulk.run(s, [ready.id, not_ready.id, done.id, "case-missing", ready.id], "transition", P["ana"], to="closed_no_sar", note="Month-end sweep",
                   reason="data_quality")
    assert (out["done"], out["refused"]) == (1, 3) and [r["id"] for r in out["results"]] == [ready.id, not_ready.id, done.id, "case-missing"]
    by = {r["id"]: r for r in out["results"]}
    assert by[ready.id]["ok"] and ready.closed is not None and ready.data["reason_code"] == "data_quality"
    assert not by[not_ready.id]["ok"] and "task" in by[not_ready.id]["message"].lower() and not_ready.closed is None
    assert s.query(CaseEvent).filter(CaseEvent.case_id == not_ready.id).count() == before  # nothing half-done
    assert "No transition" in by[done.id]["message"] and by["case-missing"]["message"] == "Not found"
    marks = [e for e in s.query(CaseEvent).filter(CaseEvent.kind == "bulk.applied")]
    assert [(e.case_id, e.payload["batch"], e.payload["of"]) for e in marks] == [(ready.id, out["batch"], 4)]


def test_ticking_tasks_in_bulk_is_undone_for_a_case_that_then_refuses(s):
    a, b = make_case(s, pid="P1"), make_case(s, pid="P2")
    out = bulk.run(s, [a.id, b.id], "transition", P["ana"], to="closed_no_sar", note="benign", reason="weak_match", tick_tasks=True)
    assert out["done"] == 0 and all("reason" in r["message"] for r in out["results"])
    s.expire_all()
    from app.cases.service import stage_tasks

    assert not any(t["done"] for c in (a, b) for t in stage_tasks(c))  # the ticks went with the refusal
    out = bulk.run(s, [a.id, b.id], "transition", P["ana"], to="closed_no_sar", note="benign", reason="data_quality", tick_tasks=True)
    assert out["done"] == 2 and a.closed is not None and b.closed is not None


def test_bulk_claim_and_reassign_follow_the_rules_for_one_case(s):
    a, b = make_case(s, pid="P1"), make_case(s, pid="P2")
    kyc = create_case(s, load_case_types(T)["kyc_refresh"], "P1", "low", created=T0)
    s.flush()
    out = bulk.run(s, [a.id, b.id, kyc.id], "claim", P["ana"])
    assert out["done"] == 2 and a.owner == b.owner == "ana" and kyc.owner != "ana" and "not a member" in out["results"][2]["message"]
    refused = bulk.run(s, [a.id, b.id], "reassign", P["ana"], owner="ana")
    assert refused["done"] == 0 and "team lead" in refused["results"][0]["message"]
    wrong = bulk.run(s, [a.id], "reassign", P["tara"], owner="kai", note="cover")
    assert wrong["done"] == 0 and "member of the case's queue" in wrong["results"][0]["message"] and a.owner == "ana"


def test_bulk_options_are_what_every_chosen_case_has_in_common(s):
    a, b = make_case(s, pid="P1"), make_case(s, pid="P2")
    kyc = create_case(s, load_case_types(T)["kyc_refresh"], "P1", "low", created=T0)
    s.flush()
    both = bulk.options(s, [a.id, b.id, "case-missing"], P["ana"])
    assert both["cases"] == 2 and both["can_claim"] and not both["can_reassign"] and {o["id"] for o in both["owners"]} == {"ana"}
    close = next(t for t in both["transitions"] if t["to"] == "closed_no_sar")
    assert close["needs_note"] and close["needs_reason"] and len(close["reasons"]) == 5 and {t["to"] for t in both["transitions"]} >= {"investigation"}
    mixed = bulk.options(s, [a.id, kyc.id], P["tara"])
    assert mixed["transitions"] == [] and mixed["owners"] == [] and mixed["can_reassign"]  # different workflows share no step and no queue
    assert bulk.options(s, [a.id], P["audra"])["transitions"] == [] and not bulk.options(s, [a.id], P["audra"])["can_claim"]
    assert bulk.options(s, [], P["ana"]) | {"can_claim": 0, "can_reassign": 0} == {"cases": 0, "can_claim": 0, "can_reassign": 0, "transitions": [], "owners": []}


@pytest.mark.parametrize("ids,action,says", [([], "claim", "between 1 and 200"), ([f"c{i}" for i in range(201)], "claim", "between 1 and 200"),
                                               (["c1"], "delete", "Unknown bulk action")])
def test_a_bulk_request_outside_the_limits_is_refused_whole(s, ids, action, says):
    with pytest.raises(TransitionError, match=says):
        bulk.run(s, ids, action, P["ana"])


# --- two cases into one ----------------------------------------------------------------------------------------------------
def _pair(s):
    a = make_case(s, pid="P1")
    b = create_case(s, load_case_types(T)["tm_alert_investigation"], "P1", "high", created=T0 + timedelta(days=1))
    s.add(Alert(id="alr-b", dedupe_key="k-b", scenario="structuring", scenario_version="1", party_id="P1", score=0.9, explanation="x", txn_ids=[],
                case_id=b.id, created=T0))
    s.add(InfoRequest(id="req-b", case_id=b.id, recipient="customer", question="Source of funds?", raised_by="ana", created=T0, due=T0 + timedelta(days=5)))
    s.flush()
    a.owner = "ana"
    return a, b


def test_merging_moves_alerts_and_requests_and_closes_the_other_case_pointing_at_the_survivor(s):
    a, b = _pair(s)
    b.sla_due = a.sla_due - timedelta(days=2)
    merge.merge(s, b, a, P["ana"], "Same pattern, two engines", ts=T0 + timedelta(days=2))
    s.expire_all()
    assert {x.id for x in s.query(Alert).filter(Alert.case_id == a.id)} == {"alr-P1", "alr-b"} and s.query(Alert).filter(Alert.case_id == b.id).count() == 0
    assert s.query(InfoRequest).filter(InfoRequest.id == "req-b").one().case_id == a.id
    assert b.closed is not None and b.disposition == "merged" and b.owner is None and b.data["merged_into"] == a.id
    assert a.closed is None and a.data["merged_from"] == [b.id] and a.priority == "high"  # the higher priority and the earlier deadline carry over
    assert a.sla_due == b.sla_due
    kinds = {e.case_id: e.kind for e in s.query(CaseEvent).filter(CaseEvent.kind.like("case.merged%"))}
    assert kinds == {b.id: "case.merged_into", a.id: "case.merged_from"}


def test_a_merge_is_refused_unless_it_is_one_matter_decided_by_someone_who_may(s):
    a, b = _pair(s)
    other = make_case(s, pid="P2")
    kyc = create_case(s, load_case_types(T)["kyc_refresh"], "P1", "low", created=T0)
    s.flush()
    for source, target, who, note, says in [
            (a, a, P["ana"], "x", "into itself"), (other, a, P["ana"], "x", "same type about the same customer"),
            (kyc, a, P["ana"], "x", "same type about the same customer"), (b, a, P["ivan"], "x", "owner of the surviving case or a team lead"),
            (b, a, P["ana"], "  ", "Say why"), (b, a, P["audra"], "x", "someone who works cases"), (b, a, P["omar"], "x", "someone who works cases")]:
        with pytest.raises(TransitionError, match=says):
            merge.merge(s, source, target, who, note)
    assert b.closed is None and s.query(Alert).filter(Alert.case_id == b.id).count() == 1
    merge.merge(s, b, a, P["tara"], "Lead decision")  # a lead may merge cases they do not own
    with pytest.raises(TransitionError, match="must be open"):
        merge.merge(s, b, a, P["tara"], "again")


def test_a_merged_case_is_not_sampled_for_qa_and_takes_no_further_steps(s, monkeypatch):
    monkeypatch.setattr("app.cases.qa.sampled", lambda case_id, rate: True)
    a, b = _pair(s)
    merge.merge(s, b, a, P["ana"], "One matter")
    assert s.query(Case).filter(Case.parent_id == b.id).count() == 0
    assert allowed_transitions(s, b, P["ana"]) == []
    with pytest.raises(TransitionError):
        transition(s, b, "investigation", P["ana"])


# --- QA scorecards -------------------------------------------------------------------------------------------------------
def test_a_scorecard_is_weighted_and_every_shortfall_needs_a_root_cause(s, monkeypatch):
    _, review = _review(s, monkeypatch)
    assert scorecard.score(s, review, P["quinn"], ALL_MET)["score"] == 100
    card = scorecard.score(s, review, P["quinn"], ALL_MET | {"rationale_clear": {"mark": "not_met", "root_cause": "weak_rationale", "comment": "c" * 900},
                                                              "timely": {"mark": "partly", "root_cause": "late"}})
    assert card["score"] == 70 and card["pass_mark"] == 80 and card["by"] == "quinn"  # 100 - 25 - half of 10
    assert len(card["answers"]["rationale_clear"]["comment"]) == 500 and card["answers"]["decision_supported"]["root_cause"] == ""
    assert review.data["scorecard"] == card
    for answers, says in [({k: v for k, v in ALL_MET.items() if k != "timely"}, "Mark every criterion. Missing: The case was worked"),
                          (ALL_MET | {"timely": {"mark": "excellent"}}, "Mark every criterion"),
                          (ALL_MET | {"timely": {"mark": "partly"}}, "Choose a root cause"),
                          (ALL_MET | {"timely": {"mark": "not_met", "root_cause": "bad_luck"}}, "Choose a root cause")]:
        with pytest.raises(TransitionError, match=says):
            scorecard.score(s, review, P["quinn"], answers)
    assert review.data["scorecard"] == card  # a refused card replaces nothing


def test_only_the_qa_queue_scores_and_only_an_open_review(s, monkeypatch):
    parent, review = _review(s, monkeypatch)
    for who in ("ana", "maya", "tara", "omar", "audra"):
        with pytest.raises(TransitionError, match="QA queue"):
            scorecard.score(s, review, P[who], ALL_MET)
    with pytest.raises(TransitionError, match="open QA review"):
        scorecard.score(s, parent, P["quinn"], ALL_MET)
    scorecard.score(s, review, P["quinn"], ALL_MET)
    complete_stage_tasks(s, review, P["quinn"])
    transition(s, review, "passed", P["quinn"])
    with pytest.raises(TransitionError, match="open QA review"):
        scorecard.score(s, review, P["quinn"], ALL_MET)


def test_a_review_cannot_pass_below_the_pass_mark_but_can_fail_at_any_score(s, monkeypatch):
    parent, review = _review(s, monkeypatch)
    complete_stage_tasks(s, review, P["quinn"])
    with pytest.raises(TransitionError, match="Score the case"):
        transition(s, review, "passed", P["quinn"])
    scorecard.score(s, review, P["quinn"], ALL_MET | {"decision_supported": {"mark": "not_met", "root_cause": "missed_red_flag"}})
    with pytest.raises(TransitionError, match="70%, below the pass mark of 80%"):
        transition(s, review, "passed", P["quinn"])
    blocked = next(t for t in allowed_transitions(s, review, P["quinn"]) if t["to"] == "passed")
    assert "pass mark" in blocked["blocked"]
    transition(s, review, "failed", P["quinn"], "Red flag not addressed")
    assert review.disposition == "qa_failed" and parent.closed is None


def test_the_qa_report_counts_scores_shortfalls_causes_and_how_agent_advice_fared(s, monkeypatch):
    assert scorecard.report(s)["reviews"] == 0 and scorecard.report(s)["average_score"] is None
    p1, r1 = _review(s, monkeypatch, "P1")
    p2, r2 = _review(s, monkeypatch, "P2")
    _, r3 = _review(s, monkeypatch, "P3")  # sampled but not yet scored: outside the report
    p1.agent_recommendation, p2.agent_recommendation = "close", "escalate"
    scorecard.score(s, r1, P["quinn"], ALL_MET)
    scorecard.score(s, r2, P["quinn"], ALL_MET | {"rationale_clear": {"mark": "not_met", "root_cause": "weak_rationale"}, "timely": {"mark": "partly", "root_cause": "late"}})
    for r, to, note in ((r1, "passed", ""), (r2, "failed", "Unclear")):
        complete_stage_tasks(s, r, P["quinn"])
        transition(s, r, to, P["quinn"], note)
    out = scorecard.report(s)
    assert (out["reviews"], out["decided"], out["passed"], out["failed"], out["average_score"], out["pass_mark"]) == (2, 2, 1, 1, 85, 80)
    assert out["by_person"] == [{"person": "ana", "reviews": 2, "average_score": 85, "failed": 1}]
    crit = {c["code"]: (c["met"], c["partly"], c["not_met"]) for c in out["by_criterion"]}
    assert crit["rationale_clear"] == (1, 0, 1) and crit["timely"] == (1, 1, 0) and crit["decision_supported"] == (2, 0, 0)
    assert {c["code"]: c["count"] for c in out["root_causes"]} == {"weak_rationale": 1, "late": 1} and out["root_causes"][0]["label"]
    # Both were closed without a report: that followed the advice to close and overrode the advice to escalate, and the override is the one QA failed.
    assert out["agent"] == {"sampled": 2, "followed": 1, "followed_failed": 0, "overridden": 1, "overridden_failed": 1}


# --- alerts that wait -----------------------------------------------------------------------------------------------------
def _rule(s, **change):
    reference.replace(s, "alert_hibernation", [{"code": "weak", "label": "Weak first alert", "scenario": "*", "below": 0.55, "days": 30} | change], P["omar"])


def _alert(s, n, score, scenario="structuring", party="PH1", created=T0):
    return ingest_external_alert(s, source="legacy", external_id=f"H-{n}", scenario=scenario, party_id=party, score=score, explanation="x", created=created)


def test_without_a_rule_every_alert_opens_a_case(s):
    a, c = _alert(s, 1, 0.2)
    assert a.status == "in_case" and c is not None and a.wake_by is None


def test_a_weak_alert_waits_and_joins_the_case_a_later_alert_opens(s):
    _rule(s)
    weak, none = _alert(s, 1, 0.4)
    assert none is None and weak.status == "hibernated" and weak.case_id is None and weak.wake_by == T0 + timedelta(days=30)
    assert "Weak first alert" in weak.policy_note and "0.40 is below 0.55" in weak.policy_note and s.query(Case).count() == 0
    edge, opened = _alert(s, 2, 0.55, party="PH2")  # the limit itself does not wait
    assert edge.status == "in_case" and opened is not None
    strong, case = _alert(s, 3, 0.8, created=T0 + timedelta(days=4))
    assert case is not None and strong.case_id == weak.case_id == case.id and weak.status == "in_case" and weak.wake_by is None
    assert "Joined" in weak.policy_note
    woken = [e.payload["alert_id"] for e in s.query(CaseEvent).filter(CaseEvent.case_id == case.id, CaseEvent.kind == "alert.woken")]
    assert woken == [weak.id]
    late, same = _alert(s, 4, 0.3, created=T0 + timedelta(days=5))  # once a case is open, even a weak alert joins it
    assert late.status == "in_case" and late.case_id == case.id


def test_a_waiting_alert_only_joins_a_case_of_its_own_kind(s):
    _rule(s)
    weak, _ = _alert(s, 1, 0.4)
    fraud, case = _alert(s, 2, 0.9, scenario="card_fraud")
    assert case is not None and case.case_type != "tm_alert_investigation" and weak.status == "hibernated" and weak.case_id is None


def test_a_rule_can_name_one_scenario(s):
    _rule(s, scenario="structuring")
    held, _ = _alert(s, 1, 0.4)
    other, case = _alert(s, 2, 0.4, scenario="rapid_movement", party="PH2")
    assert held.status == "hibernated" and other.status == "in_case" and case is not None


def test_an_alert_that_waits_its_full_time_closes_with_the_rule_on_record(s):
    _rule(s, days=10)
    a, _ = _alert(s, 1, 0.4)
    assert sweep_hibernated(s, at=T0 + timedelta(days=10)) == 0 and a.status == "hibernated"
    assert run_timers(s, at=T0 + timedelta(days=10, minutes=1))["alerts_expired"] == 1
    assert a.status == "expired" and a.case_id is None and "No further alert arrived" in a.policy_note and "2026-05-11" in a.policy_note
    later, case = _alert(s, 2, 0.9, created=T0 + timedelta(days=12))
    assert case is not None and a.status == "expired" and a.case_id is None  # a closed alert is not pulled back by a later one
    assert sweep_hibernated(s, at=T0 + timedelta(days=40)) == 0


def test_a_person_can_wake_a_waiting_or_expired_alert_into_a_case(s):
    _rule(s, days=10)
    a, _ = _alert(s, 1, 0.4)
    b, _ = _alert(s, 2, 0.4, party="PH2")
    sweep_hibernated(s, at=T0 + timedelta(days=11))
    b2, _ = _alert(s, 3, 0.45, party="PH2", created=T0 + timedelta(days=11))
    assert (a.status, b.status, b2.status) == ("expired", "expired", "hibernated")
    case = wake(s, a, "ana")
    assert case is not None and a.status == "in_case" and a.case_id == case.id and a.wake_by is None and "Woken by ana" in a.policy_note
    assert wake(s, a, "ana") is None  # already in a case
    case2 = wake(s, b2, "ivan")
    assert b2.case_id == case2.id and b.status == "expired"  # waking one alert does not revive one that already closed


def test_withdrawing_the_rule_stops_new_alerts_waiting_but_leaves_those_already_waiting(s):
    _rule(s)
    a, _ = _alert(s, 1, 0.4)
    reference.replace(s, "alert_hibernation", [], P["omar"])
    b, case = _alert(s, 2, 0.4, party="PH2")
    assert a.status == "hibernated" and b.status == "in_case" and case is not None


# --- over HTTP, against the seeded clients ----------------------------------------------------------------------------------
def _who(tenant, permission, has=True):
    return next(p for t, p in EVERYONE if t == tenant and (permission in _permissions(t, p)) is has)


@pytest.mark.parametrize("tenant", CLIENTS)
def test_reference_lists_are_readable_by_all_and_editable_only_by_those_who_configure(client, tenant):
    for t, who in EVERYONE:
        if t != tenant:
            continue
        r = client.get("/api/reference", headers=h(tenant, who))
        assert r.status_code == 200 and len(r.json()["lists"]) == len(reference.library())
        can = "publish_workflow" in _permissions(tenant, who)
        assert r.json()["can_edit"] is can
        codes = next(x for x in r.json()["lists"] if x["id"] == "qa_root_causes")["codes"]
        put = client.put("/api/reference/qa_root_causes", headers=h(tenant, who), json={"codes": codes})
        assert put.status_code == (200 if can else 403), (who, put.text)


def test_one_clients_lists_and_waiting_alerts_are_its_own(client):
    admin = _who("meridian", "publish_workflow")
    codes = next(x for x in client.get("/api/reference", headers=h("meridian", admin)).json()["lists"] if x["id"] == "qa_root_causes")["codes"]
    r = client.put("/api/reference/qa_root_causes", headers=h("meridian", admin), json={"codes": [*codes, {"code": "meridian_only", "label": "Only here"}]})
    assert r.status_code == 200
    bad = client.put("/api/reference/qa_root_causes", headers=h("meridian", admin), json={"codes": []})
    assert bad.status_code == 400 and "at least one code" in bad.json()["detail"]
    assert client.put("/api/reference/nonsense", headers=h("meridian", admin), json={"codes": codes}).status_code == 400
    for other in (c for c in CLIENTS if c != "meridian"):
        lists = {x["id"]: x for x in client.get("/api/reference", headers=h(other)).json()["lists"]}
        assert "meridian_only" not in {c["code"] for c in lists["qa_root_causes"]["codes"]} and lists["alert_hibernation"]["codes"] == []
        intake = client.get("/api/alerts/intake", headers=h(other)).json()
        assert intake["alerts"] == [] and intake["rules"] == [] and "hibernated" not in intake["counts"]


def test_the_seeded_client_shows_alerts_waiting_expired_and_joined(client):
    intake = client.get("/api/alerts/intake", headers=h("meridian", "ana")).json()
    assert intake["rules"][0]["code"] == "weak_first_alert" and intake["can_wake"]
    by = {}
    for a in intake["alerts"]:
        by.setdefault(a["status"], []).append(a)
    assert len(by["hibernated"]) == 3 and len(by["expired"]) == 1 and all(a["score"] < 0.55 and a["note"] and a["party_name"] for a in intake["alerts"])
    assert all(a["wake_by"] for a in by["hibernated"]) and "No further alert arrived" in by["expired"][0]["note"]
    with appdb.session_scope("meridian") as s:
        joined = [a for a in s.scalars(select(Alert).where(Alert.policy_note.like("%Joined%")))]
        assert len(joined) == 1 and joined[0].status == "in_case" and s.scalars(select(Alert).where(Alert.case_id == joined[0].case_id)).all().__len__() == 2
        assert s.get(Party, {"tenant_id": "meridian", "id": joined[0].party_id}) is not None
    assert not client.get("/api/alerts/intake", headers=h("meridian", "audra")).json()["can_wake"]


def test_waking_an_alert_over_http_opens_its_case_once(client):
    waiting = [a for a in client.get("/api/alerts/intake", headers=h("meridian", "ana")).json()["alerts"] if a["status"] == "hibernated"]
    for who in ("audra", "omar"):  # read-only, and an administrator who does not work cases
        assert client.post(f"/api/alerts/{waiting[0]['id']}/wake", headers=h("meridian", who)).status_code == 403
    r = client.post(f"/api/alerts/{waiting[0]['id']}/wake", headers=h("meridian", "ana"))
    assert r.status_code == 200 and r.json()["case_id"]
    case = client.get(f"/api/cases/{r.json()['case_id']}", headers=h("meridian", "ana")).json()
    assert case["case"]["party_id"] == waiting[0]["party_id"] and case["case"]["open"]
    assert client.post(f"/api/alerts/{waiting[0]['id']}/wake", headers=h("meridian", "ana")).status_code == 409
    assert client.post("/api/alerts/alr-nope/wake", headers=h("meridian", "ana")).status_code == 404
    assert client.post(f"/api/alerts/{waiting[1]['id']}/wake", headers=h("kestrel", _who("kestrel", "work"))).status_code == 404  # another client's alert


@pytest.mark.parametrize("tenant", CLIENTS)
def test_the_qa_report_is_for_people_who_see_the_team(client, tenant):
    for t, who in EVERYONE:
        if t != tenant:
            continue
        r = client.get("/api/qa/report", headers=h(tenant, who))
        perms = _permissions(tenant, who)
        assert r.status_code == (200 if "view_team" in perms else 403), who


def test_the_seeded_qa_report_shows_scores_shortfalls_and_causes(client):
    out = client.get("/api/qa/report", headers=h("meridian", "tara")).json()
    assert (out["reviews"], out["passed"], out["failed"]) == (4, 3, 1) and 80 <= out["average_score"] <= 100
    assert {c["code"] for c in out["by_criterion"]} == {"decision_supported", "rationale_clear", "procedure_followed", "evidence_complete", "timely"}
    assert out["root_causes"] and all(c["label"] and c["count"] for c in out["root_causes"])
    assert out["by_person"] and out["by_person"][0]["average_score"] <= out["by_person"][-1]["average_score"]


def test_every_seeded_closure_that_needs_a_reason_has_one_the_client_offers(client):
    for tenant in CLIENTS:
        with appdb.session_scope(tenant) as s:
            offered = {c["code"]: set(c.get("applies_to", [])) for c in reference.codes(s, "disposition_reasons", include_retired=True)}
            closed = [c for c in s.scalars(select(Case).where(Case.closed.is_not(None))) if c.disposition in {"no_sar", "genuine", "false_positive", "claim_declined", "declined", "exit"}]
            assert len(closed) >= 3
            for c in closed:
                assert c.disposition in offered[(c.data or {}).get("reason_code")], (tenant, c.id, c.disposition)


def test_bulk_and_merge_over_http_respect_who_is_asking(client):
    cases = client.get("/api/cases", headers=h("meridian", "tara")).json()
    rows = cases["cases"] if isinstance(cases, dict) else cases
    queue = [c for c in rows if c["open"] and c["queue"] == "aml-l1"][:3]
    assert len(queue) == 3
    ids = [c["id"] for c in queue]
    assert client.post("/api/cases/bulk", headers=h("meridian", "audra"), json={"action": "claim", "case_ids": ids}).status_code == 403
    assert client.post("/api/cases/bulk", headers=h("meridian", "ana"), json={"action": "claim", "case_ids": []}).status_code == 400
    r = client.post("/api/cases/bulk", headers=h("meridian", "ana"), json={"action": "claim", "case_ids": ids})
    assert r.status_code == 200 and r.json()["done"] == 3
    fran = client.post("/api/cases/bulk", headers=h("meridian", "fran"), json={"action": "claim", "case_ids": ids}).json()
    assert fran["done"] == 0 and fran["refused"] == 3
    hidden = client.post("/api/cases/bulk", headers=h("kestrel", _who("kestrel", "work")), json={"action": "claim", "case_ids": ids}).json()
    assert hidden["done"] == 0 and {x["message"] for x in hidden["results"]} == {"Not found"}  # another client's cases do not exist here
    extras = client.get(f"/api/cases/{ids[0]}/extras", headers=h("meridian", "ana")).json()
    assert extras["qa"] is None and extras["merged_into"] is None and isinstance(extras["merge_candidates"], list)
    assert client.post(f"/api/cases/{ids[0]}/merge", headers=h("meridian", "ana"), json={"into": ids[1], "note": "x"}).status_code == 409  # different customers
    assert client.post(f"/api/cases/{ids[0]}/merge", headers=h("meridian", "ana"), json={"into": "case-nope", "note": "x"}).status_code == 404


def test_scoring_over_http_and_the_extras_a_review_shows(client):
    rows = client.get("/api/cases", headers=h("meridian", "quinn")).json()
    rows = rows["cases"] if isinstance(rows, dict) else rows
    review = next(c for c in rows if c["case_type"] == "qa_review" and c["open"])
    extras = client.get(f"/api/cases/{review['id']}/extras", headers=h("meridian", "quinn")).json()["qa"]
    assert extras["can_score"] and extras["pass_mark"] == 80 and len(extras["criteria"]) == 5 and extras["root_causes"] and extras["scorecard"] is None
    assert not client.get(f"/api/cases/{review['id']}/extras", headers=h("meridian", "tara")).json()["qa"]["can_score"]
    assert client.put(f"/api/cases/{review['id']}/scorecard", headers=h("meridian", "tara"), json={"answers": ALL_MET}).status_code == 409
    assert client.put(f"/api/cases/{review['id']}/scorecard", headers=h("meridian", "quinn"), json={"answers": {}}).status_code == 409
    r = client.put(f"/api/cases/{review['id']}/scorecard", headers=h("meridian", "quinn"), json={"answers": ALL_MET})
    assert r.status_code == 200 and r.json()["score"] == 100
    assert client.get(f"/api/cases/{review['id']}/extras", headers=h("meridian", "quinn")).json()["qa"]["scorecard"]["score"] == 100


def test_the_seeded_duplicate_case_can_be_merged_by_the_owner_of_the_survivor(client):
    rows = client.get("/api/cases?status=open", headers=h("meridian", "audra")).json()
    twin = next(c for c in rows if c["case_type"] == "tm_alert_investigation" and c["owner"] is None
                and any(o["id"] != c["id"] and o["party_id"] == c["party_id"] and o["case_type"] == c["case_type"] for o in rows))
    extras = client.get(f"/api/cases/{twin['id']}/extras", headers=h("meridian", "ana")).json()
    keep = extras["merge_candidates"][0]["id"]
    before = sum(len(client.get(f"/api/cases/{cid}", headers=h("meridian", "audra")).json()["alerts"]) for cid in (keep, twin["id"]))
    assert client.post(f"/api/cases/{keep}/claim", headers=h("meridian", "ana")).status_code == 200
    assert client.get(f"/api/cases/{twin['id']}/extras", headers=h("meridian", "ana")).json()["can_merge"]
    assert client.post(f"/api/cases/{twin['id']}/merge", headers=h("meridian", "audra"), json={"into": keep, "note": "x"}).status_code == 403
    assert client.post(f"/api/cases/{twin['id']}/merge", headers=h("meridian", "ana"), json={"into": keep, "note": ""}).status_code == 409
    r = client.post(f"/api/cases/{twin['id']}/merge", headers=h("meridian", "ana"), json={"into": keep, "note": "Carried over twice"})
    assert r.status_code == 200 and r.json()["into"] == keep
    gone = client.get(f"/api/cases/{twin['id']}", headers=h("meridian", "ana")).json()
    assert not gone["case"]["open"] and gone["case"]["disposition"] == "merged" and gone["transitions"] == [] and gone["alerts"] == []
    after = client.get(f"/api/cases/{keep}/extras", headers=h("meridian", "ana")).json()
    assert after["merged_from"] == [twin["id"]] and after["merge_candidates"] == []
    assert len(client.get(f"/api/cases/{keep}", headers=h("meridian", "ana")).json()["alerts"]) == before
