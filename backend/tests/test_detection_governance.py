"""Detection governance: each client's own scenario settings, proposed with a backtest and put live by a second person,
and lookback projects that run a past period again and are worked to a conclusion."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import reference
from app.alerts.hub import route_alerts
from app.detection import lookbacks, tuning
from app.detection.engine import run_detection
from app.detection.lookbacks import LookbackError
from app.detection.scenarios import REGISTRY
from app.detection.tuning import TuningError
from app.domain.models import Account, Alert, Case, CaseEvent, Lookback, Party, ScenarioVersion, Transaction, now
from support import PERSONAS, T
from test_client_api import CLIENTS, EVERYONE, _permissions, h

P = PERSONAS
OMAR, MAYA, HUGO, IVAN, AUDRA = (P[k] for k in ("omar", "maya", "hugo", "ivan", "audra"))
WHY = "Three deposits a week catches ordinary cash businesses."
DAY = timedelta(days=1)


def customer(s, pid, deposits, amount=9000, days_ago=30, rating="medium"):
    """A customer with some cash deposits a day apart, each under the reporting threshold."""
    s.add_all([Party(id=pid, name=f"Customer {pid}", risk_rating=rating), Account(id=f"A-{pid}", party_id=pid)])
    start = now() - timedelta(days=days_ago)
    for n in range(deposits):
        s.add(Transaction(id=f"t-{pid}-{n}", account_id=f"A-{pid}", party_id=pid, ts=start + n * DAY, amount=float(amount), direction="in", channel="cash"))
    s.flush()


def detect(s, *pids):
    return route_alerts(s, run_detection(s, set(pids), only={"structuring"}), screened=True)


def end(case, disposition):
    case.closed, case.disposition, case.state = now(), disposition, "closed"


def book(s):
    """Three customers: one whose alert led to a filing, one that was a false alarm, one with four deposits still being worked."""
    customer(s, "A", 3), customer(s, "B", 3), customer(s, "C", 4)
    a, b, c = (detect(s, pid)[0] for pid in "ABC")
    end(a, "sar_filed"), end(b, "no_sar")
    s.flush()
    return a, b, c


def live(s, name="structuring", params=None, enabled=True, note="Agreed after reading the backtest in full."):
    v = tuning.propose(s, name, params or {"count": 4}, enabled, WHY, OMAR)
    return tuning.decide(s, v.id, True, note, MAYA)


def plan(s, who=HUGO, **kw):
    args = {"name": "Deposits from five thousand", "reason": "Audit found smaller deposits were never reviewed.", "scenario": "structuring",
            "params": {"min": 5000}, "period_from": (now() - timedelta(days=90)).date().isoformat(), "period_to": (now() - DAY).date().isoformat()} | kw
    return lookbacks.create(s, who, **args)


# --- settings -------------------------------------------------------------------------------------------------------------------
def test_a_client_that_has_changed_nothing_runs_on_the_scenario_defaults(s):
    assert tuning.live(s).params == {} and tuning.live(s).off == frozenset()
    assert tuning.current(s, "structuring") == (REGISTRY["structuring"]["params"], True, 0)
    view = tuning.view(s, OMAR)
    assert [x["scenario"] for x in view["scenarios"]] == list(REGISTRY) and view["can_propose"] and not view["can_decide"]
    by = {x["scenario"]: x for x in view["scenarios"]}
    assert by["structuring"]["tunable"] and by["structuring"]["limits"]["count"] == {"label": "Deposits needed to raise an alert", "min": 2, "max": 10, "whole": True}
    assert not by["sanctions_name"]["tunable"]  # its threshold is the screening rule on Lists, not a second setting here


@pytest.mark.parametrize("name,params,why", [
    ("nothing", {}, "not found"),
    ("sanctions_name", {"threshold": 0.9}, "not set here"),
    ("structuring", {"max": 12000}, "not a setting"),
    ("structuring", {"count": 1}, "between 2 and 10"),
    ("structuring", {"count": 3.5}, "whole number"),
    ("structuring", {"count": True}, "a number is needed"),
    ("structuring", {"min": "9000"}, "a number is needed"),
    ("rapid_movement", {"out_ratio": 1.2}, "between 0.3 and 1"),
])
def test_settings_outside_what_a_scenario_allows_are_refused(name, params, why):
    with pytest.raises(TuningError, match=why):
        tuning.validate(name, params)


def test_valid_settings_come_back_complete(s):
    assert tuning.validate("structuring", {"count": 4.0}) == {"min": 8000, "max": 9999.99, "count": 4, "days": 7}
    assert tuning.validate("rapid_movement", {"out_ratio": 1}) == {"min_in": 20000, "out_ratio": 1.0, "hours": 48}


def test_a_backtest_says_what_would_have_been_lost_and_changes_nothing(s):
    a, b, c = book(s)
    before = len(list(s.scalars(select(Alert))))
    out = tuning.backtest(s, "structuring", tuning.validate("structuring", {"count": 4}))
    assert (out["alerts_now"], out["alerts_then"], out["kept"]) == (3, 1, 1)
    assert {k: out["removed"][k] for k in ("count", "productive", "unproductive", "undecided")} == {"count": 2, "productive": 1, "unproductive": 1, "undecided": 0}
    assert out["productive_lost"] == 1 and out["productive_now"] == 1 and out["added"]["count"] == 0
    first = out["removed"]["items"][0]  # what mattered is listed first
    assert first["party_id"] == "A" and first["outcome"] == "productive" and first["case_id"] == a.id and first["disposition"] == "sar_filed"
    assert "2 would not have been raised, 1 of which led to a filing" in out["summary"]
    assert len(list(s.scalars(select(Alert)))) == before and s.scalars(select(ScenarioVersion)).first() is None


def test_a_backtest_shows_what_looser_settings_would_have_added(s):
    book(s)
    customer(s, "D", 3, amount=6000)
    out = tuning.backtest(s, "structuring", tuning.validate("structuring", {"min": 5000}))
    assert out["added"]["count"] == 1 and out["added"]["customers"] == 1 and out["removed"]["count"] == 0
    assert out["added"]["items"][0]["party_id"] == "D" and out["added"]["items"][0]["outcome"] == "never_raised"


def test_a_backtest_only_looks_as_far_back_as_asked(s):
    customer(s, "OLD", 3, days_ago=200)
    assert tuning.backtest(s, "structuring", tuning.validate("structuring", {"count": 4}), days=90)["alerts_now"] == 0
    assert tuning.backtest(s, "structuring", tuning.validate("structuring", {"count": 4}), days=365)["alerts_now"] == 1


def test_proposing_a_change_needs_the_permission_a_reason_and_a_real_difference(s):
    with pytest.raises(TuningError, match="needs the 'tune_detection' permission"):
        tuning.propose(s, "structuring", {"count": 4}, True, WHY, IVAN)
    with pytest.raises(TuningError, match="Say why"):
        tuning.propose(s, "structuring", {"count": 4}, True, "noise", OMAR)
    with pytest.raises(TuningError, match="already in force"):
        tuning.propose(s, "structuring", {"count": 3}, True, WHY, OMAR)
    with pytest.raises(TuningError, match="between 2 and 10"):
        tuning.propose(s, "structuring", {"count": 40}, True, WHY, OMAR)
    assert s.scalars(select(ScenarioVersion)).first() is None


def test_a_proposal_carries_its_backtest_and_only_one_can_wait_per_scenario(s):
    book(s)
    v = tuning.propose(s, "structuring", {"count": 4}, True, WHY, OMAR)
    assert (v.number, v.status, v.params["count"], v.backtest["productive_lost"]) == (1, "pending", 4, 1)
    with pytest.raises(TuningError, match="already waiting"):
        tuning.propose(s, "structuring", {"count": 5}, True, WHY, HUGO)
    other = tuning.propose(s, "rapid_movement", {"hours": 24}, True, WHY, HUGO)  # another scenario is another matter
    assert other.number == 1
    row = next(x for x in tuning.view(s, MAYA)["scenarios"] if x["scenario"] == "structuring")
    assert row["pending"]["id"] == v.id and row["pending"]["backtest"]["removed"]["items"] and row["revision"] == 0 and row["params"]["count"] == 3


def test_a_pending_proposal_changes_nothing_that_is_detected(s):
    tuning.propose(s, "structuring", {"count": 4}, True, WHY, OMAR)
    customer(s, "N", 3)
    assert len(run_detection(s, {"N"}, only={"structuring"})) == 1


def test_a_second_person_with_the_permission_decides(s):
    book(s)
    v = tuning.propose(s, "structuring", {"count": 4}, True, WHY, HUGO)
    with pytest.raises(TuningError, match="needs the 'approve_tuning' permission"):
        tuning.decide(s, v.id, True, "ok", OMAR)
    with pytest.raises(TuningError, match="you proposed this change"):
        tuning.decide(s, v.id, True, "This is fine, I have read the backtest.", HUGO)
    with pytest.raises(TuningError, match="Say why it is refused"):
        tuning.decide(s, v.id, False, "", MAYA)
    with pytest.raises(TuningError, match="lost alerts that mattered"):
        tuning.decide(s, v.id, True, "ok", MAYA)  # one filed case would have been missed: approving that takes a written reason
    assert v.status == "pending"
    tuning.decide(s, v.id, True, "The filed case was also caught by rapid movement that week.", MAYA)
    assert (v.status, v.decided_by) == ("live", "maya") and v.decided_at is not None
    with pytest.raises(TuningError, match="already live"):
        tuning.decide(s, v.id, False, "changed my mind", MAYA)
    with pytest.raises(TuningError, match="not found"):
        tuning.decide(s, "scv-none", True, "x", MAYA)


def test_a_change_that_loses_nothing_can_be_approved_without_a_note(s):
    customer(s, "B", 3)
    end(detect(s, "B")[0], "no_sar")
    v = tuning.propose(s, "structuring", {"count": 4}, True, WHY, OMAR)
    assert v.backtest["productive_lost"] == 0
    assert tuning.decide(s, v.id, True, "", MAYA).status == "live"


def test_a_refused_or_withdrawn_proposal_never_takes_effect(s):
    v = tuning.propose(s, "structuring", {"count": 4}, True, WHY, OMAR)
    with pytest.raises(TuningError, match="Only the person who proposed it"):
        tuning.withdraw(s, v.id, HUGO)
    assert tuning.withdraw(s, v.id, OMAR).status == "withdrawn"
    with pytest.raises(TuningError, match="already withdrawn"):
        tuning.withdraw(s, v.id, OMAR)
    w = tuning.propose(s, "structuring", {"count": 4}, True, WHY, OMAR)
    assert w.number == 2 and tuning.decide(s, w.id, False, "Not without a wider study.", MAYA).status == "refused"
    assert tuning.current(s, "structuring")[2] == 0
    history = next(x for x in tuning.view(s, MAYA)["scenarios"] if x["scenario"] == "structuring")["history"]
    assert [(x["number"], x["status"]) for x in history] == [(2, "refused"), (1, "withdrawn")]


def test_live_settings_drive_the_next_run_and_stamp_its_alerts(s):
    live(s)
    customer(s, "N3", 3), customer(s, "N4", 4)
    assert run_detection(s, {"N3"}, only={"structuring"}) == []
    (alert,) = run_detection(s, {"N4"}, only={"structuring"})
    assert alert.scenario_version == "1.2.0/1"  # the scenario's version, then this client's revision of its settings
    assert tuning.current(s, "structuring") == ({"min": 8000, "max": 9999.99, "count": 4, "days": 7}, True, 1)
    assert REGISTRY["structuring"]["params"]["count"] == 3  # the default itself is untouched


def test_a_later_version_supersedes_the_one_before(s):
    first = live(s)
    second = live(s, params={"count": 5})
    assert (first.status, second.status, second.number) == ("superseded", "live", 2)
    assert tuning.current(s, "structuring")[0]["count"] == 5 and tuning.live(s).revisions == {"structuring": 2}
    back = live(s, params={"count": 3})  # going back to the default is a version like any other
    assert back.number == 3 and tuning.current(s, "structuring")[0] == REGISTRY["structuring"]["params"]


def test_switching_a_scenario_off_takes_a_written_reason_and_stops_it(s):
    v = tuning.propose(s, "structuring", {}, False, WHY, OMAR)
    assert v.backtest["alerts_then"] == 0
    with pytest.raises(TuningError, match="switches the scenario off"):
        tuning.decide(s, v.id, True, "", MAYA)
    tuning.decide(s, v.id, True, "Replaced by the incumbent engine's cash rule from this month.", MAYA)
    customer(s, "N", 5)
    assert run_detection(s, {"N"}) == [] and tuning.live(s).off == {"structuring"}
    assert not next(x for x in tuning.view(s, MAYA)["scenarios"] if x["scenario"] == "structuring")["enabled"]
    on = tuning.propose(s, "structuring", {}, True, WHY, OMAR)
    assert on.backtest["alerts_now"] == 0 and on.backtest["alerts_then"] == 1
    tuning.decide(s, on.id, True, "", MAYA)
    assert len(run_detection(s, {"N"}, only={"structuring"})) == 1


def test_one_clients_settings_never_reach_another(db):
    db.onboard("one"), db.onboard("two")
    with db.tenant("one") as s:
        live(s)
        customer(s, "X", 3)
        assert run_detection(s, {"X"}, only={"structuring"}) == []
        s.commit()
    with db.tenant("two") as s:
        assert tuning.current(s, "structuring")[2] == 0 and s.scalars(select(ScenarioVersion)).first() is None
        customer(s, "X", 3)
        assert len(run_detection(s, {"X"}, only={"structuring"})) == 1


# --- lookbacks ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change,why", [
    ({"name": "x"}, "Give the lookback a name"),
    ({"reason": "audit"}, "Say why it is being done"),
    ({"scenario": "sanctions_name"}, "not set here"),
    ({"params": {"min": 5}}, "between 1000 and 9900"),
    ({"period_from": "soon"}, "start of the period is not a date"),
    ({"period_from": "2030-01-01", "period_to": "2029-01-01"}, "must start before it ends"),
    ({"period_to": "2999-01-01"}, "must be in the past"),
    ({"risk_ratings": ["severe"]}, "Risk ratings are"),
    ({"due": "whenever"}, "date it is due is not a date"),
])
def test_a_lookback_that_is_not_properly_scoped_is_refused(s, change, why):
    with pytest.raises(LookbackError, match=why):
        plan(s, **change)
    assert s.scalars(select(Lookback)).first() is None


def test_planning_a_lookback_needs_the_permission(s):
    with pytest.raises(LookbackError, match="needs the 'tune_detection' permission"):
        plan(s, who=IVAN)


def test_a_plan_counts_what_it_would_raise_before_raising_anything(s):
    a, b, c = book(s)
    customer(s, "D", 3, amount=6000), customer(s, "E", 4, amount=5500, rating="high")
    lb = plan(s)
    pv = lb.preview
    assert (pv["found"], pv["already_alerted"], pv["to_raise"], pv["customers"]) == (5, 3, 2, 2) and not pv["over_limit"]
    assert pv["already"] == {"productive": 1, "unproductive": 1, "undecided": 1}
    assert {x["party_id"] for x in pv["sample"]} == {"D", "E"}
    assert lb.status == "planned" and lb.params == {"min": 5000, "max": 9999.99, "count": 3, "days": 7}
    assert len(list(s.scalars(select(Alert)))) == 3  # nothing raised yet
    row = lookbacks.row(s, lb, full=True)
    assert row["progress"]["alerts"] == 0 and row["progress"]["complete"] == 0.0 and row["history"][0]["what"] == "planned"


def test_a_plan_can_be_limited_to_customers_of_a_risk_rating_and_a_period(s):
    customer(s, "D", 3, amount=6000), customer(s, "E", 4, amount=5500, rating="high"), customer(s, "OLD", 3, amount=6000, days_ago=200)
    assert plan(s, risk_ratings=["high"]).preview["to_raise"] == 1
    assert plan(s).preview["to_raise"] == 2  # the last ninety days
    assert plan(s, period_from=(now() - timedelta(days=300)).date().isoformat()).preview["to_raise"] == 3


def test_running_raises_only_what_was_never_alerted_and_sends_all_of_it_to_people(s):
    book(s)
    customer(s, "D", 3, amount=6000), customer(s, "E", 4, amount=5500)
    # A rule that would let these weak alerts wait, and would hold them back on an ordinary day.
    reference.replace(s, "alert_hibernation", [{"code": "weak", "label": "Weak first alert", "scenario": "*", "below": 0.99, "days": 30}], OMAR)
    lb = plan(s)
    with pytest.raises(LookbackError, match="needs the 'tune_detection' permission"):
        lookbacks.run(s, lb.id, IVAN)
    lookbacks.run(s, lb.id, HUGO)
    raised = list(s.scalars(select(Alert).where(Alert.source == f"lookback:{lb.id}")))
    assert {a.party_id for a in raised} == {"D", "E"} and all(a.status == "in_case" and a.case_id for a in raised)
    assert all(a.explanation.startswith("Lookback 'Deposits from five thousand': ") and a.scenario_version == "1.2.0/lb" for a in raised)
    assert all(now() - a.created < timedelta(minutes=1) for a in raised)  # raised today, so today's clocks apply
    cases = [s.get(Case, a.case_id) for a in raised]
    assert all(c.data["lookbacks"] == [lb.id] and c.closed is None for c in cases)
    assert all(s.scalars(select(CaseEvent).where(CaseEvent.case_id == c.id, CaseEvent.kind == "lookback.alerts")).first() for c in cases)
    assert (lb.status, lb.run_by, lb.preview["raised"], lb.preview["cases"]) == ("working", "hugo", 2, 2)
    assert len(list(s.scalars(select(Alert)))) == 5  # the three alerted at the time were not raised again
    with pytest.raises(LookbackError, match="already working"):
        lookbacks.run(s, lb.id, HUGO)
    with pytest.raises(LookbackError, match="count is fixed"):
        lookbacks.refresh(s, lb.id, HUGO)


def test_a_lookback_alert_joins_the_customers_open_case(s):
    a, b, c = book(s)
    customer(s, "D", 3, amount=6000)
    s.add_all([Transaction(id=f"t-C-low-{n}", account_id="A-C", party_id="C", ts=now() - timedelta(days=60 - n), amount=6000.0, direction="in", channel="cash") for n in range(3)])
    s.flush()
    lb = plan(s)
    lookbacks.run(s, lb.id, HUGO)
    mine = list(s.scalars(select(Alert).where(Alert.source == f"lookback:{lb.id}", Alert.party_id == "C")))
    assert [x.case_id for x in mine] == [c.id] and c.data["lookbacks"] == [lb.id]
    assert lookbacks.progress(s, lb)["cases"] == 2


def test_a_run_too_large_to_work_is_refused(s, monkeypatch):
    customer(s, "D", 3, amount=6000), customer(s, "E", 4, amount=5500)
    monkeypatch.setattr(lookbacks, "LIMIT", 1)
    lb = plan(s)
    assert lb.preview["over_limit"]
    with pytest.raises(LookbackError, match="would raise 2 alerts; one run may raise 1"):
        lookbacks.run(s, lb.id, HUGO)
    assert lb.status == "planned" and s.scalars(select(Alert)).first() is None


def test_data_that_arrives_after_planning_is_counted_again_on_request(s):
    lb = plan(s)
    assert lb.preview["to_raise"] == 0
    customer(s, "D", 3, amount=6000)
    assert lookbacks.refresh(s, lb.id, HUGO).preview["to_raise"] == 1
    with pytest.raises(LookbackError, match="needs the"):
        lookbacks.refresh(s, lb.id, IVAN)


def test_a_planned_lookback_can_be_cancelled_and_a_running_one_cannot(s):
    customer(s, "D", 3, amount=6000)
    lb = plan(s)
    with pytest.raises(LookbackError, match="Say why it is cancelled"):
        lookbacks.cancel(s, lb.id, "", HUGO)
    assert lookbacks.cancel(s, lb.id, "Superseded by the wider review.", HUGO).status == "cancelled"
    with pytest.raises(LookbackError, match="already cancelled"):
        lookbacks.run(s, lb.id, HUGO)
    other = plan(s)
    lookbacks.run(s, other.id, HUGO)
    with pytest.raises(LookbackError, match="once alerts are raised they are worked"):
        lookbacks.cancel(s, other.id, "No longer needed at all.", HUGO)


def test_sign_off_waits_for_every_case_and_takes_a_second_person(s):
    customer(s, "D", 3, amount=6000), customer(s, "E", 4, amount=5500)
    lb = plan(s)
    done = "Two customers reviewed. One report filed. The threshold moves to $5,000 from next month."
    with pytest.raises(LookbackError, match="Only a lookback that has been run"):
        lookbacks.close(s, lb.id, done, MAYA)
    lookbacks.run(s, lb.id, HUGO)
    d, e = (s.scalars(select(Case).where(Case.party_id == pid)).one() for pid in "DE")
    with pytest.raises(LookbackError, match="2 of its cases are still open"):
        lookbacks.close(s, lb.id, done, MAYA)
    end(d, "sar_filed"), end(e, "no_sar")
    s.flush()
    assert lookbacks.progress(s, lb) | {"case_ids": []} == {"alerts": 2, "customers": 2, "cases": 2, "open": 0, "closed": 2, "productive": 1, "filings": 0,
                                                            "by_outcome": {"sar_filed": 1, "no_sar": 1}, "case_ids": [], "complete": 1.0}
    with pytest.raises(LookbackError, match="needs the 'approve_tuning' permission"):
        lookbacks.close(s, lb.id, done, OMAR)
    with pytest.raises(LookbackError, match="you ran this lookback"):
        lookbacks.close(s, lb.id, done, HUGO)
    with pytest.raises(LookbackError, match="Write the conclusion"):
        lookbacks.close(s, lb.id, "Done.", MAYA)
    lookbacks.close(s, lb.id, done, MAYA)
    assert (lb.status, lb.closed_by, lb.report["productive"], lb.report["cases"]) == ("closed", "maya", 1, 2)
    d.disposition = "no_sar"  # the signed-off figures are a record: later changes to a case do not rewrite them
    s.flush()
    assert lookbacks.row(s, lb)["progress"]["productive"] == 1
    with pytest.raises(LookbackError, match="already closed"):
        lookbacks.close(s, lb.id, done, MAYA)


def test_the_report_reads_as_a_document(s):
    book(s)
    customer(s, "D", 3, amount=6000)
    lb = plan(s, risk_ratings=["medium"], due=(now() + timedelta(days=30)).date().isoformat())
    lookbacks.run(s, lb.id, HUGO)
    text = lookbacks.report_text(s, lb.id)
    for line in ("# Lookback: Deposits from five thousand", "Status: working", "- Scenario: Cash structuring below reporting threshold",
                 "- Customers: risk rating medium", "- Settings: Smallest cash deposit counted, in dollars 5000 (ordinarily 8000)",
                 "- Found on these settings: 4", "- Already alerted at the time: 3", "- Raised by this lookback: 1 alert(s) on 1 customer(s), in 1 case(s)",
                 "- Cases decided: 0 of 1", "Not yet signed off."):
        assert line in text, line
    assert "hugo: run - 1 alert(s) raised into 1 case(s); 3 had been alerted at the time" in text
    with pytest.raises(LookbackError, match="not found"):
        lookbacks.report_text(s, "lbk-none")


def test_projects_are_listed_with_the_ones_in_hand_first_and_flag_a_missed_date(s):
    customer(s, "D", 3, amount=6000)
    late = plan(s, name="Planned and late", due=(now() - timedelta(days=400)).date().isoformat(), period_to=(now() - timedelta(days=30)).date().isoformat())
    running = plan(s, name="Running now")
    lookbacks.run(s, running.id, HUGO)
    rows = lookbacks.rows(s)
    assert [r["name"] for r in rows] == ["Running now", "Planned and late"] and rows[1]["overdue"] and not rows[0]["overdue"]
    assert late.status == "planned"


def test_one_clients_lookback_never_reaches_another(db):
    db.onboard("one"), db.onboard("two")
    with db.tenant("one") as s:
        customer(s, "D", 3, amount=6000)
        lb = plan(s)
        lookbacks.run(s, lb.id, HUGO)
        lb_id = lb.id
        s.commit()
    with db.tenant("two") as s:
        customer(s, "D", 3, amount=6000)
        assert lookbacks.rows(s) == [] and s.scalars(select(Alert)).first() is None
        with pytest.raises(LookbackError, match="not found"):
            lookbacks.run(s, lb_id, HUGO)
        assert plan(s).preview["to_raise"] == 1  # its own data, counted on its own


# --- over HTTP, on the demo -----------------------------------------------------------------------------------------------------
def test_the_demo_has_a_change_waiting_and_a_lookback_planned(client):
    desk = client.get("/api/detection", headers=h("meridian", "maya")).json()
    s = next(x for x in desk["scenarios"] if x["scenario"] == "structuring")
    assert s["pending"]["params"]["count"] == 4 and s["pending"]["proposed_by"] == "omar" and s["params"]["count"] == 3 and s["revision"] == 0
    bt = s["pending"]["backtest"]
    assert bt["removed"]["count"] > 0 and bt["productive_lost"] == 0 and bt["removed"]["items"]
    assert s["record"]["alerts"] > 0 and 0 < s["record"]["productive_rate"] < 1
    (lb,) = desk["lookbacks"]
    assert lb["status"] == "planned" and lb["params"]["min"] == 5000 and lb["preview"]["to_raise"] > 0 and lb["preview"]["already_alerted"] > 0
    assert desk["can_decide"] and desk["can_propose"] and desk["can_sign_off"]
    for other in ("kestrel", "alder"):
        if other in CLIENTS:
            d = client.get("/api/detection", headers=h(other, None)).json()
            assert d["lookbacks"] == [] and all(x["pending"] is None and x["revision"] == 0 for x in d["scenarios"])


@pytest.mark.parametrize("tenant,who", EVERYONE)
def test_everyone_can_read_the_settings_and_only_the_permitted_can_change_them(client, tenant, who):
    perms = _permissions(tenant, who)
    desk = client.get("/api/detection", headers=h(tenant, who))
    assert desk.status_code == 200
    assert desk.json()["can_propose"] == ("tune_detection" in perms) and desk.json()["can_decide"] == ("approve_tuning" in perms)
    assert client.post("/api/detection/scenarios/structuring/backtest", headers=h(tenant, who), json={"params": {"count": 4}}).status_code == 200
    bad = client.post("/api/detection/scenarios/structuring/proposals", headers=h(tenant, who), json={"params": {"count": 40}, "reason": WHY})
    assert bad.status_code == (409 if "tune_detection" in perms else 403), (who, tenant, bad.status_code)
    plan_ = client.post("/api/lookbacks", headers=h(tenant, who), json={"name": "x", "reason": "x", "scenario": "structuring", "period_from": "2026-01-01", "period_to": "2026-02-01"})
    assert plan_.status_code == (409 if "tune_detection" in perms else 403)
    decide = client.post("/api/detection/proposals/scv-none/decision", headers=h(tenant, who), json={"approve": True})
    assert decide.status_code in ((404,) if "approve_tuning" in perms else (403, 404))


def test_a_backtest_over_http_checks_its_inputs(client):
    hd = h("meridian", "omar")
    assert client.post("/api/detection/scenarios/structuring/backtest", headers=hd, json={"params": {"count": 99}}).status_code == 409
    assert client.post("/api/detection/scenarios/nothing/backtest", headers=hd, json={}).status_code == 404
    assert client.post("/api/detection/scenarios/structuring/backtest", headers=hd, json={"params": {}, "days": 5}).status_code == 400
    out = client.post("/api/detection/scenarios/structuring/backtest", headers=hd, json={"params": {"min": 5000}, "days": 365}).json()
    assert out["added"]["count"] > 0 and out["to"]["min"] == 5000


def test_a_lookback_over_http_from_run_to_report(client):
    desk = client.get("/api/detection", headers=h("meridian", "hugo")).json()
    lb = desk["lookbacks"][0]
    assert client.post(f"/api/lookbacks/{lb['id']}/run", headers=h("meridian", "ivan"), json={}).status_code == 403
    assert client.post(f"/api/lookbacks/{lb['id']}/run", headers=h("kestrel", None), json={}).status_code in (403, 404)
    assert client.post(f"/api/lookbacks/{lb['id']}/launch", headers=h("meridian", "hugo"), json={}).status_code == 404
    ran = client.post(f"/api/lookbacks/{lb['id']}/run", headers=h("meridian", "hugo"), json={})
    assert ran.status_code == 200, ran.text
    out = ran.json()
    assert out["status"] == "working" and out["progress"]["alerts"] == lb["preview"]["to_raise"] and out["progress"]["open"] == out["progress"]["cases"] > 0
    case = client.get(f"/api/cases/{out['progress']['case_ids'][0]}", headers=h("meridian", "hugo")).json()
    assert any(a["explanation"].startswith("Lookback 'Cash deposits from $5,000'") for a in case["alerts"])
    # Someone who sees only fraud cases gets the totals and none of the case links.
    seen = next(x for x in client.get("/api/detection", headers=h("meridian", "fran")).json()["lookbacks"] if x["id"] == lb["id"])
    assert seen["progress"]["cases"] == out["progress"]["cases"] and seen["progress"]["case_ids"] == []
    early = client.post(f"/api/lookbacks/{lb['id']}/close", headers=h("meridian", "maya"), json={"note": "All reviewed and nothing further to report on these customers."})
    assert early.status_code == 409 and "still open" in early.json()["detail"]
    report = client.get(f"/api/lookbacks/{lb['id']}/report", headers=h("meridian", "audra"))
    assert report.status_code == 200 and report.headers["content-type"].startswith("text/markdown") and "attachment" in report.headers["content-disposition"]
    assert "# Lookback: Cash deposits from $5,000" in report.text and "Status: working" in report.text
    assert client.get(f"/api/lookbacks/{lb['id']}/report", headers=h("kestrel", None)).status_code == 404


def test_approving_the_waiting_change_over_http_changes_the_settings_in_force(client):
    desk = client.get("/api/detection", headers=h("meridian", "maya")).json()
    pending = next(x for x in desk["scenarios"] if x["scenario"] == "structuring")["pending"]
    assert client.post(f"/api/detection/proposals/{pending['id']}/decision", headers=h("meridian", "omar"), json={"approve": True}).status_code == 403
    assert client.post(f"/api/detection/proposals/{pending['id']}/decision", headers=h("kestrel", None), json={"approve": True}).status_code in (403, 404)
    ok = client.post(f"/api/detection/proposals/{pending['id']}/decision", headers=h("meridian", "maya"), json={"approve": True, "note": "Agreed."})
    assert ok.status_code == 200 and ok.json()["status"] == "live"
    after = next(x for x in client.get("/api/detection", headers=h("meridian", "audra")).json()["scenarios"] if x["scenario"] == "structuring")
    assert after["params"]["count"] == 4 and after["revision"] == 1 and after["pending"] is None and after["history"][0]["decided_by"] == "maya"
    assert next(x for x in client.get("/api/detection", headers=h("kestrel", None)).json()["scenarios"] if x["scenario"] == "structuring")["params"]["count"] == 3
