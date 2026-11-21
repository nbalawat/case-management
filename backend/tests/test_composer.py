"""The workflow designer: blocks compile to ordinary case types, checks speak plain words, trials are real and leave
nothing behind, and workflows travel in and out as CaseForge JSON, case-type YAML and BPMN 2.0."""
from __future__ import annotations

import itertools
import json
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.cases import case_types as registry
from app.cases import conditions
from app.cases.case_types import library, load_case_types
from app.cases.service import allowed_transitions, complete_stage_tasks, create_case, transition
from app.composer import interchange, templates, trial
from app.composer.compiler import Context, compile_design, to_yaml
from app.composer.model import Design, Edge, Node
from app.composer.validation import next_version, validate
from app.domain.models import Account, Alert, Case, CaseEvent, InfoRequest, Party, Transaction
from app.tenants.registry import runtime
from support import PERSONAS as P, T

T0 = datetime(2026, 5, 1, 9)


@pytest.fixture
def ctx(s):
    rt = runtime(T)
    return Context.for_tenant(T, rt.policy)


def errors(issues):
    return [i for i in issues if i["level"] == "error"]


def party(s, pid="P1", amount=20_000.0, pep=False, risk="medium", name="Test Person"):
    s.add(Party(id=pid, name=name, occupation="teacher", pep=pep, risk_rating=risk))
    s.add(Account(id=f"A-{pid}", party_id=pid))
    s.add(Transaction(id=f"t-{pid}", account_id=f"A-{pid}", party_id=pid, ts=T0, amount=amount, direction="out", channel="wire"))
    s.flush()
    return pid


def open_on(s, ct, pid):
    c = create_case(s, ct, pid, "medium", created=T0)
    s.add(Alert(id=f"alr-{pid}", dedupe_key=f"k-{pid}", scenario="rapid_movement", scenario_version="1", party_id=pid, score=0.8,
                explanation="x", txn_ids=[f"t-{pid}"], case_id=c.id, created=T0))
    s.flush()
    return c


def register(ct):
    """Make a compiled definition resolvable the way a published one is, without going through publishing."""
    registry.register_published(T, ct)
    return ct


# --- templates ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tid", list(templates.TEMPLATES))
def test_every_template_is_ready_to_publish_at_a_real_client(ctx, tid):
    design = templates.build(tid, ctx)
    issues, definition = validate(design, ctx)
    assert errors(issues) == [], issues
    registry.validate(definition)
    registry.check_policy(definition, ctx.policy)
    assert definition["case_type"] not in ctx.active


def test_a_live_workflow_opens_as_a_template_on_its_next_version(ctx):
    d = templates.build("case_type:tm_alert_investigation", ctx)
    assert d.version == next_version(ctx.active["tm_alert_investigation"]["version"]) == "4.1.0"
    assert errors(validate(d, ctx)[0]) == []


# --- round trip through the designer -----------------------------------------------------------------------------------
def _semantics(ct: dict) -> tuple:
    states = {k: {**v, "kind": v.get("kind", "open"), "tasks": [{**t, "required": t.get("required", True)} for t in v.get("tasks", [])],
                  "on_enter": v.get("on_enter") or []} for k, v in ct["states"].items()}
    trans = sorted((t["from"], t["to"], t["label"], tuple(t["roles"]), tuple(t.get("requires", [])), bool(t.get("four_eyes")),
                    json.dumps(t.get("when") or [])) for t in ct["transitions"])
    keep = ("case_type", "title", "domain", "version", "initial_state", "sla", "qa")
    return {k: ct.get(k) for k in keep}, sorted(ct.get("scenarios", [])), states, trans, (ct.get("agents") or {}).get("on_create", [])


@pytest.mark.parametrize("name", sorted(library()))
def test_every_shipped_case_type_survives_a_round_trip_through_the_designer(ctx, name):
    ct = library()[name]
    design = interchange.from_definition(ct)
    back = compile_design(design, ctx)
    assert _semantics(back) == _semantics(ct)


def test_yaml_and_json_exports_reimport_to_the_same_workflow(ctx):
    design = templates.build("investigation_to_filing", ctx)
    for fmt in ("caseforge", "case_type", "bpmn"):
        text = interchange.write(design, ctx, fmt)
        again, notes = interchange.read(text, ctx, fmt)
        assert _semantics(compile_design(again, ctx)) == _semantics(compile_design(design, ctx)), fmt
        assert errors(validate(again, ctx)[0]) == [], fmt
        assert interchange.detect(text) == fmt


# --- decisions ---------------------------------------------------------------------------------------------------------
def test_a_decision_routes_by_facts_and_otherwise_is_the_exact_complement(ctx):
    design = templates.build("investigation_to_filing", ctx)
    definition = compile_design(design, ctx)
    out_of_mlro = [t for t in definition["transitions"] if t["from"] == "mlro_review" and t["label"] != "Return for rework"]
    assert {t["to"] for t in out_of_mlro} == {"senior_approval", "sar_filed"}
    for amount, pep in itertools.product([0, 99_999.99, 100_000, 5e6], [True, False]):
        f = {"alerted_amount": amount, "pep": pep}
        holding = [t["to"] for t in out_of_mlro if conditions.evaluate(t.get("when"), f)]
        assert holding == (["senior_approval"] if amount >= 100_000 or pep else ["sar_filed"]), (amount, pep)
    senior = next(t for t in out_of_mlro if t["to"] == "senior_approval")
    assert senior["four_eyes"] and senior["roles"] and senior["label"] == "Approve ($100,000 or more, or a PEP)"


def test_chained_decisions_combine_their_conditions(ctx):
    d = templates.build("blank", ctx)
    work = next(n for n in d.nodes if n.type == "stage")
    d.nodes += [Node(id="d1", type="decision", config={"label": "Amount", "otherwise": True, "branches": [
                    {"id": "big", "label": "Big", "conditions": [{"fact": "alerted_amount", "op": ">=", "value": 1000}]}]}),
                Node(id="d2", type="decision", config={"label": "PEP", "otherwise": True, "branches": [
                    {"id": "pep", "label": "PEP", "conditions": [{"fact": "pep", "op": "==", "value": True}]}]}),
                Node(id="o2", type="outcome", config={"label": "Big PEP", "disposition": "big_pep"})]
    d.edges = [e for e in d.edges if e.source != work.id]
    d.edges += [Edge(id="x1", source=work.id, target="d1", config={"label": "Decide", "roles": ["analyst"]}),
                Edge(id="x2", source="d1", target="d2", handle="big"), Edge(id="x3", source="d1", target="closed", handle="otherwise"),
                Edge(id="x4", source="d2", target="o2", handle="pep"), Edge(id="x5", source="d2", target="closed", handle="otherwise")]
    issues, definition = validate(d, ctx)
    assert errors(issues) == []
    big_pep = next(t for t in definition["transitions"] if t["to"] == "big_pep")
    assert big_pep["when"] == ["alerted_amount >= 1000", "pep == true"]
    closes = [t for t in definition["transitions"] if t["to"] == "closed"]
    for amount, pep in itertools.product([10, 5000], [True, False]):
        f = {"alerted_amount": amount, "pep": pep}
        assert sum(conditions.evaluate(t.get("when"), f) for t in closes) + conditions.evaluate(big_pep["when"], f) == 1


# --- plain-word checks -------------------------------------------------------------------------------------------------
def _messages(design, ctx):
    return " | ".join(i["message"] for i in validate(design, ctx)[0])


def test_checks_name_each_problem_and_where_it_is(ctx):
    d = templates.build("two_level_review", ctx)
    second = next(n for n in d.nodes if n.config.get("label") == "Second-line review")
    approve_edge = next(e for e in d.edges if e.config.get("label") == "Escalate")
    d.edges = [e for e in d.edges if e.source != second.id]  # a dead end, and the approval becomes unreachable
    d.nodes.append(Node(id="lost", type="stage", config={"label": "Lost", "queue": "nope"}))
    approve_edge.config["roles"] = []
    issues, definition = validate(d, ctx)
    assert definition is None
    on = lambda nid: " | ".join(i["message"] for i in errors(issues) if i["id"] == nid)  # noqa: E731
    assert "dead end" in on(second.id)
    assert "does not exist" in on("lost") and "No path" in on("lost")
    assert any("Nobody can take" in i["message"] and i["id"] == approve_edge.id for i in issues)


@pytest.mark.parametrize("break_it,expect", [
    (lambda d: d.nodes.__setitem__(0, Node(id="start", type="nope")), "not a block"),
    (lambda d: setattr(d, "nodes", [n for n in d.nodes if n.type != "outcome"]), "no outcome"),
    (lambda d: setattr(d, "nodes", [n for n in d.nodes if n.type != "start"]), "no start"),
    (lambda d: d.edges.append(Edge(id="bad", source="start", target="closed")), "more than one block"),
    (lambda d: next(n for n in d.nodes if n.type == "outcome").config.update(disposition="Not OK"), "lower case"),
    (lambda d: next(n for n in d.nodes if n.type == "stage").config.update(sla_hours=-1), "positive number"),
    (lambda d: next(n for n in d.nodes if n.type == "stage").config.update(actions=[{"do": "notify"}]), "needs role to notify"),
    (lambda d: next(n for n in d.nodes if n.type == "stage").config.update(actions=[{"do": "teleport"}]), "unknown automatic action"),
    (lambda d: next(e for e in d.edges if e.config.get("roles")).config.update(roles=["wizard"]), "'wizard' is not a role"),
])
def test_each_rule_explains_itself(ctx, break_it, expect):
    d = templates.build("blank", ctx)
    break_it(d)
    assert expect.lower() in _messages(d, ctx).lower()


def test_decision_problems_are_pinned_to_the_decision(ctx):
    d = templates.build("investigation_to_filing", ctx)
    route = next(n for n in d.nodes if n.type == "decision")
    route.config["branches"].append({"id": "b_new", "label": "Odd", "conditions": [{"fact": "risk_rating", "op": ">=", "value": "purple"}]})
    msgs = [i["message"] for i in validate(d, ctx)[0] if i["id"] == route.id]
    assert any("goes nowhere" in m for m in msgs) and any("not one of" in m for m in msgs)


def test_publishing_over_a_live_version_needs_a_newer_number(ctx):
    d = templates.build("case_type:tm_alert_investigation", ctx)
    d.version = "4.0.0"
    issues, _ = validate(d, ctx)
    assert any("not newer" in i["message"] and "4.1.0" in i["hint"] for i in issues)


def test_four_eyes_with_a_single_holder_is_flagged(ctx):
    d = templates.build("investigation_to_filing", ctx)
    assert any("Only 1 person" in i["message"] for i in validate(d, ctx)[0])  # Meridian has one head of FIU


def test_a_claimed_scenario_and_a_screening_decision_without_screening_are_warned_about(ctx):
    d = templates.build("screening_triage", ctx)
    next(n for n in d.nodes if n.type == "start").config["scenarios"] = ["structuring"]
    for n in d.nodes:
        n.config["actions"] = [a for a in n.config.get("actions") or [] if a["do"] != "screen_party"]
    msgs = _messages(d, ctx)
    assert "already open 'Transaction monitoring investigation' cases" in msgs and "no stage before it screens" in msgs


# --- automatic actions run on the case core, on arrival ----------------------------------------------------------------
def test_automatic_actions_run_when_a_case_arrives_and_feed_decisions(s, ctx):
    from app.sanctions import lists

    lists.ensure(s)
    listed = lists.names(s)[0]
    d = templates.build("screening_triage", ctx)
    ct = register(compile_design(d, ctx))
    hit = open_on(s, ct, party(s, "P1", name=listed))
    clean = open_on(s, ct, party(s, "P2", name="Zzyzx Qwerty"))
    assert hit.data["screening"]["hit"] is True and clean.data["screening"]["hit"] is False
    for c, expect in ((hit, "officer_review"), (clean, "no_match")):
        complete_stage_tasks(s, c, P["ana"])
        options = {t["to"] for t in allowed_transitions(s, c, P["ana"])}
        assert options == {expect}, (c.party_id, options)
    transition(s, hit, "officer_review", P["ana"])
    assert hit.priority == "high"
    kinds = [e.kind for e in s.scalars(select(CaseEvent).where(CaseEvent.case_id == hit.id))]
    assert kinds.count("workflow.action") == 3 and "workflow.notified" in kinds


def test_asking_for_information_on_arrival_opens_a_real_request(s, ctx):
    ct = register(compile_design(templates.build("customer_outreach", ctx), ctx))
    c = open_on(s, ct, party(s))
    complete_stage_tasks(s, c, P["kai"])
    transition(s, c, "awaiting_the_customer", P["kai"])
    r = s.scalars(select(InfoRequest).where(InfoRequest.case_id == c.id)).one()
    assert r.recipient == "customer" and r.raised_by == "workflow" and (r.due - r.created).days == 10
    assert {t["to"]: t["blocked"] for t in allowed_transitions(s, c, P["kai"])}["assess_the_response"] == "An information request is still open"


def test_registered_facts_and_requirements_extend_the_language(s, ctx):
    from app.cases import service

    conditions.register_fact("dt_demo_band", "Demo decision table band", lambda s_, c: "red", "choice", ["red", "green"])
    service.register_requirement("demo_form", "Demo form complete", lambda s_, c, t: "Fill in the demo form")
    try:
        d = templates.build("blank", ctx)
        edge = next(e for e in d.edges if e.config.get("roles"))
        edge.config["requires"] = ["demo_form"]
        edge.config["when"] = {"match": "all", "conditions": [{"fact": "dt_demo_band", "op": "==", "value": "red"}]}
        issues, definition = validate(d, ctx)
        assert errors(issues) == []
        c = open_on(s, register(definition), party(s))
        assert allowed_transitions(s, c, P["ana"])[0]["blocked"] == "Fill in the demo form"
    finally:
        for name in ("dt_demo_band",):
            conditions.PROVIDERS.pop(name, None)
            conditions.FACTS.pop(name, None)
        service.REQUIREMENT_CHECKS.pop("demo_form", None)
        registry.REQUIREMENTS.pop("demo_form", None)


# --- trials --------------------------------------------------------------------------------------------------------------
def _count(s, model):
    return s.scalar(select(func.count()).select_from(model))


def test_a_trial_runs_for_real_and_leaves_nothing_behind(s, ctx):
    d = templates.build("investigation_to_filing", ctx)
    _, definition = validate(d, ctx)
    before = {m: _count(s, m) for m in (Case, CaseEvent, Party, Alert, InfoRequest)}
    path = ["investigation", "sar_drafting", "mlro_review", "senior_approval", "sar_filed"]
    out = trial.run(s, T, ctx.policy, definition, trial.Sample(amount=250_000), path)
    assert out["ok"] and out["case"]["closed"] and out["case"]["disposition"] == "sar_filed"
    assert [st["by"] for st in out["steps"]][-2:] == ["Maya Chen", "Hugo Laurent"]
    assert any("wrote the narrative" in p for st in out["steps"] for p in st["prepared"])
    assert any(e["kind"] == "workflow.action" for e in out["timeline"])  # the screening on arrival ran
    assert {m: _count(s, m) for m in before} == before
    assert ("investigation_to_filing", definition["version"]) not in runtime(T).published


def test_a_trial_stops_where_the_facts_or_the_people_do(s, ctx):
    d = templates.build("investigation_to_filing", ctx)
    _, definition = validate(d, ctx)
    path = ["investigation", "sar_drafting", "mlro_review", "senior_approval"]
    low = trial.run(s, T, ctx.policy, definition, trial.Sample(amount=5_000), path)
    assert not low["ok"] and "does not apply" in low["stopped"]["why"]
    one = trial.run(s, T, ctx.policy, definition, trial.Sample(amount=5_000), path[:3])
    assert {o["to"] for o in one["options"]} == {"sar_filed", "sar_drafting"}


def test_routes_list_every_way_through_for_the_sample(ctx):
    _, definition = validate(templates.build("investigation_to_filing", ctx), ctx)
    small = trial.routes(definition, trial.Sample(amount=5_000).facts())
    big = trial.routes(definition, trial.Sample(amount=500_000).facts())
    assert not any(st["to"] == "senior_approval" for r in small["routes"] for st in r["steps"])
    assert any(st["to"] == "senior_approval" for r in big["routes"] for st in r["steps"])
    assert small["outcomes_not_reached"] == [] and set(small["outcomes_reached"]) == {"SAR filed", "Closed, no SAR"}


# --- BPMN from other tools -----------------------------------------------------------------------------------------------
FOREIGN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" id="d1" targetNamespace="x">
  <bpmn:process id="Process_EDD" name="Enhanced due diligence" isExecutable="true">
    <bpmn:laneSet id="ls">
      <bpmn:lane id="l1" name="KYC and onboarding"><bpmn:flowNodeRef>start</bpmn:flowNodeRef><bpmn:flowNodeRef>collect</bpmn:flowNodeRef></bpmn:lane>
      <bpmn:lane id="l2" name="Enhanced due diligence"><bpmn:flowNodeRef>review</bpmn:flowNodeRef><bpmn:flowNodeRef>gw</bpmn:flowNodeRef></bpmn:lane>
      <bpmn:lane id="l3" name="Board"><bpmn:flowNodeRef>board</bpmn:flowNodeRef></bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="start" name="Onboarding request"/>
    <bpmn:userTask id="collect" name="Collect&#10;documents"/>
    <bpmn:boundaryEvent id="timer" attachedToRef="collect"><bpmn:timerEventDefinition><bpmn:timeDuration>P3D</bpmn:timeDuration></bpmn:timerEventDefinition></bpmn:boundaryEvent>
    <bpmn:userTask id="review" name="EDD review"/>
    <bpmn:exclusiveGateway id="gw" name="High risk?" default="f_std"/>
    <bpmn:userTask id="board" name="Board approval"/>
    <bpmn:serviceTask id="notify" name="Send welcome pack"/>
    <bpmn:endEvent id="ok" name="Onboarded"/>
    <bpmn:endEvent id="no" name="Declined"/>
    <bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="collect"/>
    <bpmn:sequenceFlow id="f2" sourceRef="collect" targetRef="review" name="Documents complete"/>
    <bpmn:sequenceFlow id="f3" sourceRef="review" targetRef="gw" name="Recommend onboarding"/>
    <bpmn:sequenceFlow id="f_high" sourceRef="gw" targetRef="board" name="High risk">
      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">${risk_rating == 'high' || pep == true}</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    <bpmn:sequenceFlow id="f_std" sourceRef="gw" targetRef="notify"/>
    <bpmn:sequenceFlow id="f5" sourceRef="board" targetRef="notify" name="Approve"/>
    <bpmn:sequenceFlow id="f6" sourceRef="board" targetRef="no" name="Decline"/>
    <bpmn:sequenceFlow id="f7" sourceRef="notify" targetRef="ok"/>
    <bpmn:sequenceFlow id="f8" sourceRef="review" targetRef="no" name="Decline"/>
    <bpmn:sequenceFlow id="ft" sourceRef="timer" targetRef="review"/>
  </bpmn:process>
</bpmn:definitions>"""


def test_a_bpmn_file_from_another_tool_imports_with_sensible_defaults_and_notes(ctx):
    design, notes = interchange.read(FOREIGN, ctx)
    by = {n.id: n for n in design.nodes}
    assert design.key == "enhanced_due_diligence" and design.title == "Enhanced due diligence"
    assert by["collect"].config["label"] == "Collect documents" and by["collect"].config["sla_hours"] == 72
    assert by["collect"].config["queue"] == "kyc" and by["review"].config["queue"] == "kyc-edd" and by["board"].config["queue"] is None
    gw = by["gw"].config
    assert gw["otherwise"] and gw["branches"][0]["match"] == "any" and len(gw["branches"][0]["conditions"]) == 2
    collect_out = next(e for e in design.edges if e.source == "collect")
    assert collect_out.config["roles"] == ["kyc_analyst"]  # who the KYC queue's people are
    joined = " ".join(notes)
    assert "Lane 'Board' matches no queue" in joined and "Send welcome pack" in joined and "72 hours" in joined
    issues, _ = validate(design, ctx)
    open_items = {i["message"] for i in errors(issues)}
    assert any("Nobody can take" in m for m in open_items)  # the board lane needs people; the designer says so
    board = by["board"]
    board.config["queue"] = "kyc-edd"
    for e in design.edges:
        if e.source in ("board", "notify"):
            e.config["roles"] = ["mlro"]
    issues, definition = validate(design, ctx)
    assert errors(issues) == [], issues
    assert definition["states"]["collect_documents"]["sla_hours"] == 72


@pytest.mark.parametrize("bad,expect", [
    ("<x", "Not valid XML"),
    ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>', "DOCTYPE"),
    ("<definitions/>", "No BPMN process"),
    ("{", "Not valid JSON"),
    ('{"format": "other"}', "not a CaseForge workflow"),
    ("states: [", "not valid YAML"),
])
def test_unreadable_files_are_refused_with_a_reason(ctx, bad, expect):
    with pytest.raises(interchange.InterchangeError, match=expect):
        interchange.read(bad, ctx)


def test_yaml_export_is_what_publishing_accepts(ctx):
    d = templates.build("two_level_review", ctx)
    text = to_yaml(compile_design(d, ctx))
    assert text.startswith("# Composed in the CaseForge workflow designer")
    ct = registry.parse(text)
    registry.check_policy(ct, ctx.policy)


def test_design_documents_reject_bad_keys_and_versions():
    with pytest.raises(ValueError, match="lower case"):
        Design(key="Bad Key", title="x")
    with pytest.raises(ValueError, match="1.0.0"):
        Design(key="ok_key", title="x", version="one")


def test_library_definitions_untouched(ctx):
    assert set(load_case_types(T)) >= {"tm_alert_investigation"}
