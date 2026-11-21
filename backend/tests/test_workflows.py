"""Workflow engine: conditions, stage tasks, information requests, timers, QA sampling, versions."""
from datetime import datetime, timedelta

import pytest

from app.cases import assignments, conditions, publishing, requests
from app.cases.case_types import CaseTypeError, get_definition, library, load_case_types
from app.cases.qa import sampled
from app.cases.service import (TransitionError, allowed_transitions, complete_stage_tasks, create_case, save_narrative,
                               set_task, stage_tasks, transition)
from app.cases.timers import run_timers
from app.domain.models import Account, Alert, Case, CaseEvent, Party, Transaction
from app.tenants import registry
from app.tenants.onboarding import hydrate
from support import PERSONAS, T

P = PERSONAS
T0 = datetime(2026, 5, 1, 9)


def make_case(s, case_type="tm_alert_investigation", amount=20_000.0, pep=False, risk="medium", pid="P1"):
    s.add(Party(id=pid, name="Test Person", occupation="teacher", pep=pep, risk_rating=risk))
    s.add(Account(id=f"A-{pid}", party_id=pid))
    s.add(Transaction(id=f"t-{pid}", account_id=f"A-{pid}", party_id=pid, ts=T0, amount=amount, direction="out", channel="wire"))
    s.flush()
    c = create_case(s, load_case_types(T)[case_type], pid, "medium", created=T0)
    s.add(Alert(id=f"alr-{pid}", dedupe_key=f"k-{pid}", scenario="rapid_movement", scenario_version="1", party_id=pid, score=0.8,
                explanation="x", txn_ids=[f"t-{pid}"], case_id=c.id, created=T0))
    s.flush()
    return c


def to_mlro_review(s, c):
    complete_stage_tasks(s, c, P["ana"])
    transition(s, c, "investigation", P["ana"])
    complete_stage_tasks(s, c, P["ivan"])
    transition(s, c, "sar_drafting", P["ivan"])
    complete_stage_tasks(s, c, P["ivan"])
    save_narrative(s, c, "Narrative", P["ivan"])
    transition(s, c, "mlro_review", P["ivan"])


# --- conditions ------------------------------------------------------------------
@pytest.mark.parametrize("when,facts,expected", [
    (["alerted_amount >= 100000"], {"alerted_amount": 100000.0}, True),
    (["alerted_amount >= 100000"], {"alerted_amount": 99999.99}, False),
    (["pep == true"], {"pep": True}, True),
    (["pep == false"], {"pep": True}, False),
    (["risk_rating != high"], {"risk_rating": "HIGH"}, False),
    (["risk_rating == high", "pep == false"], {"risk_rating": "high", "pep": False}, True),
    ([{"any": ["alerted_amount >= 100000", "pep == true"]}], {"alerted_amount": 5.0, "pep": True}, True),
    ([{"any": ["alerted_amount >= 100000", "pep == true"]}], {"alerted_amount": 5.0, "pep": False}, False),
    (["alerts > 2"], {}, False),  # a missing fact never satisfies a condition
    (None, {}, True),
])
def test_conditions(when, facts, expected):
    assert conditions.evaluate(when, facts) is expected


@pytest.mark.parametrize("bad", ["__import__('os')", "amount >= 5", "pep is true", "alerted_amount >="])
def test_conditions_reject_anything_but_the_small_grammar(bad):
    with pytest.raises(conditions.ConditionError):
        conditions.check([bad])


def test_high_value_filing_needs_a_second_approval(s):
    c = make_case(s, amount=250_000)
    to_mlro_review(s, c)
    options = {t["to"] for t in allowed_transitions(s, c, P["maya"])}
    assert "senior_approval" in options and "filed" not in options
    with pytest.raises(TransitionError, match="does not apply"):
        transition(s, c, "filed", P["maya"])
    transition(s, c, "senior_approval", P["maya"])
    assert c.queue == "senior-approval"
    with pytest.raises(TransitionError):
        transition(s, c, "filed", P["maya"])  # wrong role for the final step
    transition(s, c, "filed", P["hugo"])
    assert c.disposition == "sar_filed"


def test_ordinary_filing_goes_straight_from_the_mlro(s):
    c = make_case(s, amount=20_000)
    to_mlro_review(s, c)
    assert {t["to"] for t in allowed_transitions(s, c, P["maya"])} == {"filed", "sar_drafting"}


def test_pep_forces_second_approval_even_at_low_value(s):
    c = make_case(s, amount=500, pep=True)
    to_mlro_review(s, c)
    assert "senior_approval" in {t["to"] for t in allowed_transitions(s, c, P["maya"])}


# --- tasks, four-eyes, hand-offs -------------------------------------------------
def test_optional_tasks_do_not_block_and_unknown_tasks_are_refused(s):
    c = make_case(s)
    complete_stage_tasks(s, c, P["ana"])
    transition(s, c, "investigation", P["ana"])
    for t in stage_tasks(c):
        if t["required"]:
            set_task(s, c, t["id"], P["ivan"])
    assert any(not t["done"] for t in stage_tasks(c))  # the optional adverse media check is still open
    transition(s, c, "sar_drafting", P["ivan"])
    with pytest.raises(TransitionError):
        set_task(s, c, "no_such_task", P["ivan"])


def test_task_can_be_reopened_and_then_blocks_again(s):
    c = make_case(s)
    complete_stage_tasks(s, c, P["ana"])
    set_task(s, c, "review_briefing", P["ana"], done=False)
    with pytest.raises(TransitionError, match="Review the alert"):
        transition(s, c, "investigation", P["ana"])


def test_four_eyes_blocks_the_person_who_sent_the_case(s):
    c = make_case(s, case_type="sanctions_hit")
    complete_stage_tasks(s, c, P["sofia"])
    transition(s, c, "escalated", P["sofia"])
    complete_stage_tasks(s, c, P["sofia"])
    with pytest.raises(TransitionError, match="second person"):
        transition(s, c, "blocked", P["sofia"], "match")
    transition(s, c, "blocked", P["maya"], "match")
    assert c.disposition == "true_match"


def test_handoff_changes_queue_and_clears_an_owner_outside_it(s):
    c = make_case(s)
    assignments.claim(s, c, P["ana"])
    complete_stage_tasks(s, c, P["ana"])
    transition(s, c, "investigation", P["ana"])
    assert (c.queue, c.owner) == ("aml-l2", None)
    with pytest.raises(TransitionError):
        assignments.claim(s, c, P["ana"])  # not a member of the L2 queue
    assignments.claim(s, c, P["ivan"])
    with pytest.raises(TransitionError):
        assignments.reassign(s, c, "lena", P["ivan"])  # investigators cannot reassign
    with pytest.raises(TransitionError):
        assignments.reassign(s, c, "kai", P["tara"])  # target must be in the queue
    assignments.reassign(s, c, "lena", P["tara"], "workload")
    assert c.owner == "lena"


# --- information requests and wait states ----------------------------------------
def test_wait_state_needs_an_open_request_and_resumes_only_when_answered(s):
    c = make_case(s)
    complete_stage_tasks(s, c, P["ana"])
    transition(s, c, "investigation", P["ana"])
    with pytest.raises(TransitionError, match="information request"):
        transition(s, c, "awaiting_information", P["ivan"])
    r = requests.raise_request(s, c, P["ivan"], "customer", "Source of funds?", 5)
    transition(s, c, "awaiting_information", P["ivan"])
    with pytest.raises(TransitionError, match="still open"):
        transition(s, c, "investigation", P["ivan"])
    requests.answer_request(s, r, P["ivan"], "Sale of property")
    transition(s, c, "investigation", P["ivan"])
    with pytest.raises(TransitionError):
        requests.answer_request(s, r, P["ivan"], "again")
    with pytest.raises(TransitionError):
        requests.raise_request(s, c, P["audra"], "customer", "q")


# --- timers ----------------------------------------------------------------------
def test_stage_breach_escalates_once_per_stage_entry(s):
    c = make_case(s)  # triage has a 48 hour stage clock
    assert run_timers(s, at=T0 + timedelta(hours=47))["stage_breaches"] == 0
    assert run_timers(s, at=T0 + timedelta(hours=49))["stage_breaches"] == 1
    assert c.priority == "high"
    assert run_timers(s, at=T0 + timedelta(hours=90))["stage_breaches"] == 0  # no repeat for the same entry
    complete_stage_tasks(s, c, P["ana"], ts=T0 + timedelta(hours=91))
    transition(s, c, "investigation", P["ana"], ts=T0 + timedelta(hours=91))
    assert run_timers(s, at=T0 + timedelta(hours=91 + 121))["stage_breaches"] == 1  # a new stage has a new clock
    assert c.priority == "critical"


def test_overdue_request_is_flagged_once(s):
    c = make_case(s)
    requests.raise_request(s, c, P["ana"], "branch", "q", 2, ts=T0)
    assert run_timers(s, at=T0 + timedelta(days=3))["requests_overdue"] == 1
    assert run_timers(s, at=T0 + timedelta(days=4))["requests_overdue"] == 0


# --- QA sampling -----------------------------------------------------------------
def _close_no_sar(s, c):
    complete_stage_tasks(s, c, P["ana"])
    transition(s, c, "closed_no_sar", P["ana"], "benign")


def test_qa_sampling_is_deterministic_and_close_to_the_declared_rate():
    ids = [f"case-{i}" for i in range(4000)]
    share = sum(sampled(i, 0.35) for i in ids) / len(ids)
    assert 0.32 < share < 0.38 and [sampled(i, 0.35) for i in ids[:50]] == [sampled(i, 0.35) for i in ids[:50]]
    assert not any(sampled(i, 0.0) for i in ids[:200]) and all(sampled(i, 1.0) for i in ids[:200])


def test_failed_qa_reopens_the_case_and_passed_qa_does_not(s, monkeypatch):
    monkeypatch.setattr("app.cases.qa.sampled", lambda case_id, rate: True)
    c = make_case(s)
    _close_no_sar(s, c)
    s.flush()
    review = s.query(Case).filter(Case.parent_id == c.id).one()
    assert review.case_type == "qa_review" and review.queue == "qa"
    with pytest.raises(TransitionError):
        transition(s, review, "failed", P["ivan"], "x")  # only QA decides
    transition(s, review, "failed", P["quinn"], "Rationale incomplete")
    assert c.closed is None and c.state == "investigation" and c.disposition is None and c.queue == "aml-l2"
    complete_stage_tasks(s, c, P["ivan"])
    transition(s, c, "closed_no_sar", P["ivan"], "documented")
    s.flush()
    assert s.query(Case).filter(Case.parent_id == c.id).count() == 1  # a case is sampled once
    kinds = [e.kind for e in s.query(CaseEvent).filter(CaseEvent.case_id == c.id)]
    assert {"qa.sampled", "qa.failed", "case.reopened"} <= set(kinds)


def test_filed_sars_are_outside_the_qa_sample(s, monkeypatch):
    monkeypatch.setattr("app.cases.qa.sampled", lambda case_id, rate: True)
    c = make_case(s)
    to_mlro_review(s, c)
    transition(s, c, "filed", P["maya"])
    s.flush()
    assert s.query(Case).filter(Case.parent_id == c.id).count() == 0


# --- versions --------------------------------------------------------------------
def _draft(version="4.1.0", **replace):
    src = library()["tm_alert_investigation"]["source"].replace("version: 4.0.0", f"version: {version}")
    for a, b in replace.items():
        src = src.replace(a, b)
    return src


def test_publish_needs_admin_and_a_higher_version(s):
    with pytest.raises(CaseTypeError, match="permission"):
        publishing.publish(s, _draft(), P["maya"])
    with pytest.raises(CaseTypeError, match="higher"):
        publishing.publish(s, _draft("4.0.0"), P["omar"])
    with pytest.raises(CaseTypeError, match="higher"):
        publishing.publish(s, _draft("3.9.9"), P["omar"])
    publishing.publish(s, _draft("4.1.0"), P["omar"])
    assert load_case_types(T)["tm_alert_investigation"]["version"] == "4.1.0"


def test_open_cases_stay_on_their_version_and_new_cases_get_the_new_one(s, db):
    old = make_case(s, pid="P1")
    src = _draft("4.1.0").replace("sla_hours: 48\n    tasks:\n      - {id: review_briefing", "sla_hours: 12\n    tasks:\n      - {id: review_briefing")
    result = publishing.publish(s, src, P["omar"], "tighter triage clock")
    assert "Changes stage 'Triage'" in result["changes"]
    new = make_case(s, pid="P2")
    assert (old.type_version, new.type_version) == ("4.0.0", "4.1.0")
    assert get_definition(T, old.case_type, old.type_version)["states"]["triage"]["sla_hours"] == 48
    assert run_timers(s, at=T0 + timedelta(hours=13))["stage_breaches"] == 1  # only the case on the new version
    assert (old.priority, new.priority) == ("medium", "high")
    s.commit()
    registry.reset()
    with db.platform() as p:
        hydrate(p)  # a restart reads the client's published version back
    assert load_case_types(T)["tm_alert_investigation"]["version"] == "4.1.0"


@pytest.mark.parametrize("mutate,message", [
    ({"queue: aml-l1": "queue: nowhere"}, "unknown queue"),
    ({"roles: [mlro], requires: [note]}": "roles: [wizard], requires: [note]}"}, "unknown role"),
    ({"requires: [tasks, narrative]": "requires: [tasks, signature]"}, "requires unknown"),
    ({'"alerted_amount < 100000"': '"amount < 100000"'}, "Unknown fact"),
    ({"initial_state: triage": "initial_state: nowhere"}, "initial_state"),
    ({"  - {from: senior_approval, to: filed, label: Final approval and file SAR, roles: [head_fiu], requires: [narrative], four_eyes: true}\n": "",
      "  - {from: senior_approval, to: mlro_review, label: Return to MLRO, roles: [head_fiu], requires: [note]}\n": ""}, "dead end"),
    ({"kind: wait": "kind: paused"}, "kind must be"),
])
def test_bad_definitions_are_rejected_before_they_go_live(s, mutate, message):
    with pytest.raises(CaseTypeError, match=message):
        publishing.check(T, _draft("9.0.0", **mutate))


def test_every_shipped_case_type_is_valid_and_every_stage_can_reach_an_end():
    for ct in library().values():
        closed = {k for k, v in ct["states"].items() if v.get("kind") == "closed"}
        for start in ct["states"]:
            seen, frontier = {start}, [start]
            while frontier:
                cur = frontier.pop()
                for t in ct["transitions"]:
                    if t["from"] == cur and t["to"] not in seen:
                        seen.add(t["to"])
                        frontier.append(t["to"])
            assert seen & closed, f"{ct['case_type']}: {start} can never close"
