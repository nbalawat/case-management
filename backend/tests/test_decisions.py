"""Decision tables: the cell language, hit policies, analysis, DMN exchange, versions per client, and the bindings that
put a table's answer into live work (case priority and queue on opening, customer risk band)."""
from datetime import datetime

import pytest
import yaml

from app.alerts.hub import route_alerts
from app.decisions import DecisionError, HitPolicyViolation, decide, dmn, evaluate as evaluate_by_id, list_tables, service, store
from app.decisions.analysis import analyse
from app.decisions.engine import evaluate
from app.decisions.feel import FeelError, parse_test
from app.decisions.model import from_dict, parse, to_yaml
from app.domain.models import Account, Alert, Case, CaseEvent, Party
from app.kyc import risk
from support import PERSONAS, T

P = PERSONAS
T0 = datetime(2026, 5, 1, 9)


# --- the cell language ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("cell,kind,yes,no", [
    ("-", "number", [1, None, -5], []),
    (">= 100", "number", [100, 250.5], [99.99, None, "x"]),
    ("< 0.6", "number", [0.59, -1], [0.6]),
    ("[1..10]", "number", [1, 10, 5], [0.999, 10.01]),
    ("(1..10]", "number", [1.01, 10], [1]),
    ("]1..10[", "number", [5], [1, 10]),
    ("[1..2], [5..6]", "number", [1.5, 5], [3]),
    ('"high", "critical"', "text", ["high", "Critical"], ["low", None]),
    ("high", "text", ["HIGH "], ["medium"]),
    ('not("low", "medium")', "text", ["high"], ["low", "medium"]),
    ("!= low", "text", ["high"], ["low", None]),
    ("true", "bool", [True], [False, None]),
    ("null", "text", [None], ["x"]),
    (["us", "gb"], "text", ["GB"], ["fr"]),
    (5, "number", [5.0], [6]),
])
def test_unary_tests(cell, kind, yes, no):
    t = parse_test(cell, kind)
    assert all(t.holds(v) for v in yes)
    assert not any(t.holds(v) for v in no)


@pytest.mark.parametrize("cell,kind", [("[10..1]", "number"), ("[1..2]", "text"), (">= high", "number"), ('"open', "text"),
                                       ("not(-)", "text"), ("1, -", "number"), ("__import__('os')", "text"), ("yes", "bool")])
def test_bad_cells_are_refused(cell, kind):
    with pytest.raises(FeelError):
        parse_test(cell, kind)


# --- hit policies ----------------------------------------------------------------------------------------------
def table(hit="UNIQUE", rules=None, **kw):
    doc = {"id": "t", "title": "T", "version": "1.0", "context": "inputs", "hit_policy": hit,
           "inputs": [{"name": "amount", "kind": "number"}, {"name": "risk", "kind": "text"}],
           "outputs": [{"name": "level", "kind": "text", "values": ["high", "medium", "low"], "default": "low"}],
           "rules": rules or [{"id": "a", "when": {"amount": ">= 100"}, "then": {"level": "high"}},
                              {"id": "b", "when": {"amount": "< 100", "risk": "high"}, "then": {"level": "medium"}}]} | kw
    return from_dict(doc)


def test_unique_picks_the_one_rule_and_defaults_when_none_match():
    t = table()
    assert evaluate(t, {"amount": 500}).outputs == {"level": "high"}
    r = evaluate(t, {"amount": 5, "risk": "low"})
    assert r.defaulted and r.outputs == {"level": "low"} and r.matched == ()


def test_unique_refuses_two_matches_and_the_analysis_finds_them_first():
    t = table(rules=[{"id": "a", "when": {"amount": ">= 100"}, "then": {"level": "high"}},
                     {"id": "b", "when": {"risk": "high"}, "then": {"level": "medium"}}])
    with pytest.raises(HitPolicyViolation):
        evaluate(t, {"amount": 100, "risk": "high"})
    report = analyse(t)
    assert report.overlaps[0]["rules"] == ["a", "b"]
    assert report.overlaps[0]["example"]["amount"] >= 100 and report.overlaps[0]["example"]["risk"] == "high"


def test_first_priority_any_collect():
    rules = [{"id": "a", "when": {"risk": "high"}, "then": {"level": "medium"}}, {"id": "b", "when": {"amount": ">= 100"}, "then": {"level": "high"}}]
    hit = {"amount": 200, "risk": "high"}
    assert evaluate(table("FIRST", rules), hit).matched == ("a",)
    assert evaluate(table("PRIORITY", rules), hit).outputs == {"level": "high"}  # high comes first in the values
    with pytest.raises(HitPolicyViolation):
        evaluate(table("ANY", rules), hit)
    assert evaluate(table("ANY", [rules[0], rules[0] | {"id": "c"}]), hit).matched == ("a", "c")
    assert evaluate(table("RULE ORDER", rules), hit).outputs == {"level": ["medium", "high"]}
    pts = from_dict({"id": "p", "title": "P", "version": "1", "context": "inputs", "hit_policy": "COLLECT", "aggregation": "SUM",
                     "inputs": [{"name": "x", "kind": "number"}], "outputs": [{"name": "points", "kind": "number", "default": 0}],
                     "rules": [{"when": {"x": "> 1"}, "then": {"points": 10}}, {"when": {"x": "> 5"}, "then": {"points": 5}}]})
    assert evaluate(pts, {"x": 9}).outputs == {"points": 15}
    assert evaluate(pts, {"x": 0}).outputs == {"points": 0}


def test_analysis_finds_dead_rules_and_gaps():
    t = table("FIRST", [{"id": "a", "when": {"amount": ">= 10"}, "then": {"level": "high"}},
                        {"id": "b", "when": {"amount": ">= 50"}, "then": {"level": "medium"}},  # always beaten by a
                        {"id": "c", "when": {"amount": "[1..2]", "risk": "x"}, "then": {"level": "low"}}],
              outputs=[{"name": "level", "kind": "text", "values": ["high", "medium", "low"]}])  # no default
    r = analyse(t)
    assert r.never_decide == ["b"]
    assert r.gap_count and any("match no rule" in w for w in r.warnings)
    impossible = table("FIRST", [{"id": "a", "when": {"amount": "[1..2]"}, "then": {"level": "high"}},
                                 {"id": "b", "when": {"amount": "(1..1]"}, "then": {"level": "low"}}])
    assert analyse(impossible).never_match == ["b"]


@pytest.mark.parametrize("change,error", [
    ({"hit_policy": "SOMETIMES"}, "hit_policy"),
    ({"aggregation": "SUM"}, "aggregation"),
    ({"id": "Bad Id"}, "id"),
    ({"version": "one"}, "version"),
    ({"rules": [{"id": "a", "when": {"nope": 1}, "then": {"level": "high"}}]}, "unknown input"),
    ({"rules": [{"id": "a", "when": {}, "then": {}}]}, "gives no value"),
    ({"rules": [{"id": "a", "when": {}, "then": {"level": "extreme"}}]}, "not one of"),
    ({"rules": [{"id": "a", "then": {"level": "high"}}, {"id": "a", "then": {"level": "high"}}]}, "used twice"),
])
def test_structural_rules(change, error):
    with pytest.raises(DecisionError, match=error):
        table(**change)


# --- DMN exchange ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("table_id", sorted(store.library()))
def test_every_library_table_survives_a_dmn_round_trip(table_id):
    t = store.library()[table_id]
    back = dmn.parse(dmn.export(t))
    assert [(r.id, r.tests, r.outputs) for r in back.rules] == [(r.id, r.tests, r.outputs) for r in t.rules]
    assert (back.hit_policy, back.aggregation, back.binding, back.context, back.version) == (t.hit_policy, t.aggregation, t.binding, t.context, t.version)
    assert not analyse(t).errors


CAMUNDA = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d" name="d" namespace="http://camunda.org/schema/1.0/dmn">
  <decision id="Decision_Risk1" name="Dish risk">
    <decisionTable id="DT_1" hitPolicy="FIRST">
      <input id="I1" label="Amount"><inputExpression id="E1" typeRef="number"><text>txn.amount</text></inputExpression></input>
      <input id="I2" label="Country"><inputExpression id="E2" typeRef="string"><text>country</text></inputExpression></input>
      <output id="O1" name="Level" typeRef="string" />
      <rule id="Rule_1"><inputEntry id="a"><text>&gt;= 10000</text></inputEntry><inputEntry id="b"><text>"IR","KP"</text></inputEntry><outputEntry id="c"><text>"high"</text></outputEntry></rule>
      <rule id="Rule_2"><inputEntry id="d"><text>-</text></inputEntry><inputEntry id="e"><text></text></inputEntry><outputEntry id="f"><text>"low"</text></outputEntry></rule>
    </decisionTable>
  </decision>
</definitions>"""


def test_a_table_from_another_dmn_tool_is_read():
    t = dmn.parse(CAMUNDA)
    assert t.id == "decision_risk1" and [c.name for c in t.inputs] == ["txn_amount", "country"] and t.outputs[0].name == "level"
    assert evaluate(t, {"txn_amount": 20000, "country": "KP"}).outputs == {"level": "high"}
    assert evaluate(t, {"txn_amount": 20000, "country": "FR"}).matched == ("Rule_2",)
    assert parse(t.source).rules == t.rules  # kept as YAML


@pytest.mark.parametrize("xml", ['<!DOCTYPE x [<!ENTITY a "b">]><definitions/>', "<definitions", "<definitions xmlns='x'/>"])
def test_unsafe_or_broken_dmn_is_refused(xml):
    with pytest.raises(DecisionError):
        dmn.parse(xml)


def test_yaml_round_trip():
    t = store.library()["case_priority"]
    assert parse(to_yaml(t)).rules == t.rules


# --- versions per client and live bindings ----------------------------------------------------------------------
def enabled(table_id: str, version: str = "1.1", **change) -> str:
    doc = yaml.safe_load(store.library()[table_id].source) | {"version": version, "enabled": True} | change
    return yaml.safe_dump(doc, sort_keys=False)


def customer(s, pid="P1", pep=False, risk_rating="medium"):
    s.add(Party(id=pid, name=f"Person {pid}", pep=pep, risk_rating=risk_rating))
    s.add(Account(id=f"A-{pid}", party_id=pid))
    s.flush()


def alert(s, pid="P1", score=0.8, n=1):
    a = Alert(id=f"alr-{pid}-{n}", dedupe_key=f"k-{pid}-{n}", scenario="rapid_movement", scenario_version="1", party_id=pid, score=score,
              explanation="x", txn_ids=[], created=T0)
    s.add(a)
    s.flush()
    return a


def test_library_tables_change_nothing_until_a_client_enables_one(s):
    customer(s, pep=True)
    (case,) = route_alerts(s, [alert(s, score=0.65)], screened=True)
    assert case.priority == "medium" and case.queue == "aml-l1"
    assert not s.query(CaseEvent).filter_by(case_id=case.id, kind="decision.applied").count()


def test_an_enabled_priority_table_sets_priority_and_says_why(s):
    service.publish(s, parse(enabled("case_priority")), P["omar"], "PEPs first")
    customer(s, pep=True)
    (case,) = route_alerts(s, [alert(s, score=0.65)], screened=True)
    assert case.priority == "critical"
    ev = s.query(CaseEvent).filter_by(case_id=case.id, kind="decision.applied").one()
    assert ev.payload["rules"] == ["r2"] and ev.payload["before"] == "medium" and ev.payload["version"] == "1.1"
    assert decide(s, "case_priority", case)["_rule"] == ["r2"]


def test_a_routing_table_picks_the_first_queue(s):
    service.publish(s, parse(enabled("case_routing")), P["omar"])
    customer(s, "P1", pep=True)
    customer(s, "P2")
    c1, c2 = route_alerts(s, [alert(s, "P1", 0.5), alert(s, "P2", 0.5)], screened=True)
    assert (c1.queue, c2.queue) == ("aml-l2", "aml-l1")


def test_a_queue_the_client_does_not_have_is_refused_at_publish(s):
    src = enabled("case_routing", rules=[{"id": "r1", "when": {}, "then": {"queue": "nowhere"}}])
    with pytest.raises(DecisionError, match="queue"):
        service.publish(s, parse(src), P["omar"])


def test_only_a_higher_version_with_the_permission_publishes(s):
    with pytest.raises(DecisionError, match="higher"):
        service.publish(s, parse(enabled("case_priority", version="1.0")), P["omar"])
    with pytest.raises(DecisionError, match="permission"):
        service.publish(s, parse(enabled("case_priority")), P["ivan"])


def test_an_inconsistent_table_cannot_be_published(s):
    src = enabled("case_routing", hit_policy="UNIQUE", rules=[{"id": "a", "when": {"pep": True}, "then": {"queue": "aml-l2"}},
                                                               {"id": "b", "when": {"max_score": ">= 0.5"}, "then": {"queue": "aml-l1"}}])
    with pytest.raises(DecisionError, match="both match"):
        service.publish(s, parse(src), P["omar"])


def test_a_clients_version_stays_with_that_client(db):
    db.onboard("acme")
    db.onboard("zenith")
    with db.tenant("acme") as s:
        service.publish(s, parse(enabled("case_priority")), P["omar"])
        s.commit()
        assert store.get(s, "case_priority").version == "1.1"
    with db.tenant("zenith") as s:
        assert store.get(s, "case_priority").version == "1.0"
        assert {t["id"] for t in list_tables(s)} == set(store.library())


def test_the_risk_band_table_overrides_the_score_band(s):
    from app.domain.models import Party as Pt

    customer(s, pep=True, risk_rating="low")
    party = s.get(Pt, {"tenant_id": T, "id": "P1"})
    before = risk.score(s, party)
    service.publish(s, parse(enabled("customer_risk_band")), P["omar"])
    after = risk.score(s, party)
    assert after["band"] == "high" and after["decision"]["rules"] and after["score_band"] == before["band"]
    assert after["model"] != before["model"]  # the assessment records that a table decided


def test_simulation_replays_recent_cases(s):
    customer(s, "P1", pep=True)
    customer(s, "P2")
    route_alerts(s, [alert(s, "P1", 0.65), alert(s, "P2", 0.95)], screened=True)
    sim = service.check(s, parse(enabled("case_priority")))["simulation"]
    assert sim["cases"] == 2 and sim["changed"] == 1 and sim["examples"][0]["would_be"] == "critical"


def test_evaluate_by_id_for_other_components(s):
    out = evaluate_by_id(s, "red_flag_points", {"pep": True, "risk_rating": "high", "alerts": 1, "alerted_amount": 0, "customer_kind": "person"})
    assert out["points"] == 50 and out["_rule"] == ["pep", "high_risk"]


# --- over HTTP -------------------------------------------------------------------------------------------------
def H(who=None):
    return {"X-Tenant": "meridian"} | ({"X-Persona": who} if who else {})


def test_api_round_trip(client):
    tables = client.get("/api/decisions", headers=H()).json()
    assert {"case_priority", "case_routing", "customer_risk_band", "red_flag_points"} <= {t["id"] for t in tables}
    ref = client.get("/api/decisions/reference", headers=H()).json()
    assert "case" in ref["contexts"] and any(b["id"] == "case_priority" for b in ref["bindings"])
    one = client.get("/api/decisions/case_priority", headers=H()).json()
    assert one["versions"][0]["active"] and one["analysis"]["checked"]
    draft = dict(one) | {"version": "1.1", "enabled": True}
    for k in ("yaml", "versions", "analysis"):
        draft.pop(k)
    checked = client.post("/api/decisions/check", json={"table": draft}, headers=H()).json()
    assert checked["ok"] and checked["publishable"] and "Puts the table in use" in checked["changes"]
    assert checked["simulation"]["cases"] > 0
    tried = client.post("/api/decisions/case_priority/evaluate", json={"inputs": {"max_score": 0.95}}, headers=H()).json()
    assert tried["outputs"] == {"priority": "critical"}
    assert client.post("/api/decisions/publish", json={"table": draft}, headers=H("ivan")).status_code == 403
    done = client.post("/api/decisions/publish", json={"table": draft, "note": "go live"}, headers=H("omar")).json()
    assert done["version"] == "1.1"
    xml = client.get("/api/decisions/case_priority/dmn", headers=H())
    assert xml.status_code == 200 and "decisionTable" in xml.text
    imported = client.post("/api/decisions/check", json={"dmn": xml.text.replace('"version": "1.1"', '"version": "1.2"')}, headers=H()).json()
    assert imported["ok"] and imported["changes"] == ["No change to the rules"]
    reg = client.get("/api/governance?area=Decisions", headers=H("maya")).json()["register"]
    assert reg and reg[0]["what"] == "Version 1.1 published"
