"""Durable stage clocks: everything on the application's side of Temporal.

No server is needed here. tests/test_temporal.py runs the workflows on a real one.
"""
from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

import app.db as appdb
from app.backbone import status
from app.cases.service import stage_due
from app.cases.timers import breach_case, run_timers
from app.config import settings
from app.domain import models as m
from app.durable import clocks, temporal
from app.durable.temporal import DurableError
from support import T
from test_backbone import OTHER, world  # noqa: F401  fixture
from test_detection_governance import customer, detect


def case_due_in(s, pid, seconds):
    """A customer with a case whose current stage is due `seconds` from now (negative: already overdue)."""
    customer(s, pid, 3)
    c = detect(s, pid)[0]
    hours = (stage_due(c) - c.state_entered).total_seconds() / 3600
    c.state_entered = m.now() - timedelta(hours=hours) + timedelta(seconds=seconds)
    s.flush()
    return c


def breaches(s, case_id):
    return [e for e in s.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == case_id)) if e.kind == "sla.stage_breached"]


# ---- the breach itself

def test_a_breach_is_applied_once_however_often_it_is_asked_for(s):
    c = case_due_in(s, "A", -5)
    before = c.priority
    assert breach_case(s, c, m.now()) is True and breach_case(s, c, m.now()) is False
    assert len(breaches(s, c.id)) == 1 and c.priority != before and c.data["stage_breach"] == c.state_entered.isoformat()
    assert run_timers(s)["stage_breaches"] == 0


def test_a_case_that_is_not_due_or_is_closed_is_left_alone(s):
    c = case_due_in(s, "A", 600)
    assert breach_case(s, c, m.now()) is False
    d = case_due_in(s, "B", -5)
    d.closed = m.now()
    assert breach_case(s, d, m.now()) is False and breaches(s, d.id) == []


def test_the_sweep_still_finds_what_it_always_found(s):
    c = case_due_in(s, "A", -5)
    case_due_in(s, "B", 600)
    assert run_timers(s)["stage_breaches"] == 1 and len(breaches(s, c.id)) == 1


# ---- which clocks should be running

def test_every_open_case_with_a_deadline_has_a_clock(s):
    a, b = case_due_in(s, "A", 600), case_due_in(s, "B", -5)
    got = {c.case_id: c for c in clocks.due_clocks(s)}
    assert set(got) == {a.id, b.id}
    assert got[a.id].entered == a.state_entered.isoformat() and got[a.id].due == stage_due(a).isoformat()


def test_a_closed_case_and_a_stage_already_acted_on_have_none(s):
    a, b, c = case_due_in(s, "A", 600), case_due_in(s, "B", -5), case_due_in(s, "C", 600)
    a.closed = m.now()
    breach_case(s, b, m.now())
    s.flush()
    assert [x.case_id for x in clocks.due_clocks(s)] == [c.id]


def test_a_case_that_moves_stage_gets_a_new_clock(s):
    b = case_due_in(s, "B", -5)
    breach_case(s, b, m.now())
    assert clocks.due_clocks(s) == []
    b.state_entered = m.now()  # a new visit to a stage
    s.flush()
    (clock,) = clocks.due_clocks(s)
    assert clock.entered == b.state_entered.isoformat()


# ---- a clock running out

@pytest.fixture
def cases(world):  # noqa: F811
    """Cases at two clients, reachable the way an activity reaches them: through the application's own sessions."""
    with world.tenant(T) as s:
        ids = {"due": case_due_in(s, "A", -5).id, "later": case_due_in(s, "B", 600).id}
        s.commit()
    with world.tenant(OTHER) as s:
        ids["theirs"] = case_due_in(s, "A", -5).id
        s.commit()
    world.ids = ids
    return world


def entered(world, case_id, tenant=T):  # noqa: F811
    with world.tenant(tenant) as s:
        return s.get(m.Case, case_id).state_entered.isoformat()


def test_a_clock_running_out_applies_the_escalation(cases):
    cid = cases.ids["due"]
    assert clocks.fire(T, cid, entered(cases, cid)) == "breached"
    with cases.tenant(T) as s:
        assert len(breaches(s, cid)) == 1


def test_firing_twice_is_the_same_as_firing_once(cases):
    cid = cases.ids["due"]
    e = entered(cases, cid)
    assert [clocks.fire(T, cid, e), clocks.fire(T, cid, e), clocks.fire(T, cid, e)] == ["breached", "already", "already"]
    with cases.tenant(T) as s:
        assert len(breaches(s, cid)) == 1


def test_a_clock_for_an_earlier_visit_to_a_stage_does_nothing(cases):
    cid = cases.ids["due"]
    old = entered(cases, cid)
    with cases.tenant(T) as s:
        s.get(m.Case, cid).state_entered = m.now()
        s.commit()
    assert clocks.fire(T, cid, old) == "moved_on"
    with cases.tenant(T) as s:
        assert breaches(s, cid) == []


def test_a_clock_for_a_closed_or_missing_case_does_nothing(cases):
    cid = cases.ids["due"]
    e = entered(cases, cid)
    with cases.tenant(T) as s:
        s.get(m.Case, cid).closed = m.now()
        s.commit()
    assert clocks.fire(T, cid, e) == "closed" and clocks.fire(T, "case-nope", e) == "gone"


def test_a_clock_that_fires_early_does_nothing(cases):
    cid = cases.ids["later"]
    assert clocks.fire(T, cid, entered(cases, cid)) == "not_due"


def test_one_clients_clock_cannot_touch_anothers_case(cases):
    theirs = cases.ids["theirs"]
    assert clocks.fire(T, theirs, entered(cases, theirs, OTHER)) == "gone"
    with cases.tenant(OTHER) as s:
        assert breaches(s, theirs) == []


def test_clocks_are_listed_per_client(cases):
    got = clocks.clocks_by_client()
    assert {t: sorted(c.case_id for c in v) for t, v in got.items()} == {T: sorted([cases.ids["due"], cases.ids["later"]]), OTHER: [cases.ids["theirs"]]}


def test_the_whole_sweep_runs_for_every_client(cases):
    out = clocks.sweep_all()
    assert set(out) == {T, OTHER} and out[T]["stage_breaches"] == 1 and out[OTHER]["stage_breaches"] == 1
    assert clocks.sweep_all()[T]["stage_breaches"] == 0


# ---- names and addresses

def test_a_clock_is_named_for_its_client_and_case():
    assert temporal.clock_id("meridian", "case-1") == "caseforge.meridian.clock.case-1"
    assert temporal.clock_id("kestrel", "case-1") != temporal.clock_id("meridian", "case-1")


@pytest.mark.parametrize("tenant", ["", "ab", "Acme", "a.b", "acme\n", None])
def test_a_clock_is_never_named_for_something_that_is_not_a_client(tenant):
    with pytest.raises(DurableError):
        temporal.clock_id(tenant, "case-1")


@pytest.mark.parametrize("url, want", [("temporal://localhost:7233", "localhost:7233"), ("temporal://temporal.internal:7233/", "temporal.internal:7233")])
def test_the_address_is_read_from_the_url(url, want):
    assert temporal.target(url) == want


@pytest.mark.parametrize("url", ["", "localhost:7233", "http://localhost:7233", "temporal://"])
def test_anything_else_is_refused(url):
    with pytest.raises(DurableError, match="TEMPORAL_URL"):
        temporal.target(url)


# ---- the workflow code

def test_the_workflow_definitions_are_valid_and_named():
    from temporalio import workflow

    from app.durable.workflows import StageClock, Sweep

    for cls, name, signals in ((StageClock, "StageClock", {"stage"}), (Sweep, "Sweep", set())):
        d = workflow._Definition.must_from_class(cls)
        assert d.name == name and set(d.signals) == signals
    assert set(workflow._Definition.must_from_class(StageClock).queries) >= {"state"}


def test_the_activities_carry_the_names_the_workflows_call():
    from temporalio import activity

    from app.durable.workflows import STAGE_DUE, SWEEP

    assert {activity._Definition.must_from_callable(a).name for a in temporal.activities()} == {STAGE_DUE, SWEEP}


def test_workflow_code_imports_nothing_from_the_application():
    """Workflow code is replayed and must be deterministic. It may know activity names, never the code behind them."""
    tree = ast.parse((Path(temporal.__file__).parent / "workflows.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and not (node.module or "").startswith("app"), ast.dump(node)
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("app") for a in node.names)


# ---- what the platform reports

def test_with_no_temporal_the_page_says_the_sweep_checks_clocks(s):
    assert settings.temporal_url == ""
    v = status.view(s)["clocks"]
    assert v == {"engine": "sweep", "connected": False, "clocks": None, "note": "Stage clocks are checked by the sweep, when it is asked for and when work lists load."}


def test_a_temporal_that_is_down_is_reported_not_raised():
    object.__setattr__(settings, "temporal_url", "temporal://127.0.0.1:9")
    try:
        v = temporal.status(T)
    finally:
        object.__setattr__(settings, "temporal_url", "")
    assert v == {"engine": "temporal", "connected": False, "clocks": None, "note": "Temporal did not answer. The sweep still checks stage clocks."}


def test_the_worker_will_not_start_without_an_address(capsys):
    from app.durable import worker

    assert worker.main() == 2 and "TEMPORAL_URL" in capsys.readouterr().out
    assert appdb.engine is not None
