"""After a report is sent: the regulator's answer chased when it is overdue, and the continuing-activity review.

The same functions run from the periodic sweep (built-in engine) and from each report's FilingWorkflow (Temporal);
tests/test_temporal.py runs the workflow.
"""
from datetime import timedelta

from sqlalchemy import select

from app.cases.timers import run_timers
from app.domain import models as m
from app.filing import followups
from app.filing import service as filing
from support import PERSONAS, T
from test_filing import _filed, _profile

MAYA = PERSONAS["maya"]


def _kinds(s, f):
    return [e.kind for e in s.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == f.case_id).order_by(m.CaseEvent.id)) if e.kind.startswith("filing.")]


def test_a_sent_report_waits_for_the_regulators_answer_and_is_chased_once_when_it_is_late(s):
    _profile()
    _, f = _filed(s)
    due = f.submitted_at + timedelta(days=followups.ANSWER_DAYS)
    assert followups.deadlines(f) == {followups.OVERDUE: due}
    assert followups.view(f) == {"status": "submitted", "next": followups.OVERDUE, "due": due.isoformat()}
    assert followups.follow_up(s, f, due - timedelta(minutes=1)) == []
    assert followups.follow_up(s, f, due) == [followups.OVERDUE] and followups.follow_up(s, f, due + timedelta(days=9)) == []
    assert [x["what"] for x in f.history][-1] == followups.OVERDUE and _kinds(s, f)[-1] == "filing.answer_overdue"
    assert followups.view(f)["next"] is None  # nothing more to wait for until the regulator answers


def test_the_clients_profile_can_give_the_regulator_longer(s):
    from app.tenants import registry

    _profile()
    registry.runtime(T).settings["filing"]["answer_days"] = 45
    _, f = _filed(s)
    assert followups.deadlines(f)[followups.OVERDUE] == f.submitted_at + timedelta(days=45)


def test_an_accepted_suspicion_report_comes_round_for_its_continuing_activity_review(s):
    _profile()
    _, f = _filed(s)
    filing.acknowledge(s, f.id, "BSA-123", MAYA)
    due = f.answered_at + timedelta(days=filing.CONTINUING_DAYS)
    assert followups.deadlines(f) == {followups.CONTINUING: due}
    assert followups.follow_up(s, f, due) == [followups.CONTINUING] and followups.follow_up(s, f, due) == []
    assert _kinds(s, f)[-1] == "filing.continuing_review_due" and followups.view(f)["next"] is None


def test_a_rejected_or_replaced_report_owes_nothing_more(s):
    _profile()
    _, f = _filed(s)
    filing.reject(s, f.id, "Missing subject address", MAYA)
    assert followups.deadlines(f) == {} and followups.view(f)["next"] is None
    new = filing.amend(s, f.id, MAYA)
    assert f.status == "superseded" and followups.deadlines(f) == {}
    assert set(followups.deadlines(new)) == ({followups.OVERDUE} if new.status == "submitted" else set())  # the correction has clocks of its own
    assert followups.view(None) == {"status": "gone", "next": None, "due": None}


def test_the_periodic_sweep_applies_due_follow_ups(s):
    _profile()
    _, f = _filed(s)
    f.submitted_at = f.submitted_at - timedelta(days=followups.ANSWER_DAYS + 1)
    s.flush()
    assert run_timers(s)["filing_follow_ups"] == 1 and run_timers(s)["filing_follow_ups"] == 0
