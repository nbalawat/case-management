"""What decision tables and forms add to the workflow designer: decision answers as condition facts, and a step
that needs the stage's forms completed."""
import pytest

from app.cases import conditions
from app.cases.case_types import REQUIREMENTS, CaseTypeError, validate
from app.cases.service import _blocked, facts
from app.decisions import decide
from app.decisions.case_facts import fact_name
from app.forms import requirement
from app.forms import service as forms
from support import PERSONAS as P
from test_forms import GOOD, SAR, tm_case


def test_case_tables_offer_their_outputs_as_typed_facts():
    by = {c["id"]: c for c in conditions.catalog()}
    assert by["dt_case_priority_priority"]["kind"] == "choice" and by["dt_case_priority_priority"]["values"] == ["critical", "high", "medium", "low"]
    assert by["dt_red_flag_points_points"]["kind"] == "number" and by["dt_case_routing_queue"]["kind"] == "text"
    assert not any(c["id"].startswith("dt_customer_risk_band") for c in conditions.catalog())  # about customers, not cases
    conditions.check(["dt_case_priority_priority == critical", "dt_red_flag_points_points >= 40"])


def test_a_case_reads_the_tables_answer_without_looping(s):
    c = tm_case(s)
    f = facts(s, c)
    assert f[fact_name("case_priority", "priority")] == decide(s, "case_priority", c)["priority"]
    assert f["dt_red_flag_points_points"] == decide(s, "red_flag_points", c)["points"]
    assert conditions.evaluate([f"dt_case_priority_priority == {f['dt_case_priority_priority']}"], f)
    assert not conditions.evaluate(["dt_case_priority_priority == nonsense"], f)


def _step(s, c, **extra):
    return {"from": c.state, "to": "awaiting_information", "label": "Decide", "roles": ["investigator"], "requires": ["form"]} | extra


def test_a_step_can_require_the_stages_forms(s):
    assert "form" in REQUIREMENTS
    c = tm_case(s)
    t = _step(s, c)
    assert _blocked(s, c, t, P["ivan"], None) == "Fill in the form 'SAR decision record'"
    forms.save(s, c, SAR, {"typology": "structuring"}, P["ivan"])
    assert _blocked(s, c, t, P["ivan"], None) == "Complete the form 'SAR decision record'"
    forms.save(s, c, SAR, GOOD, P["ivan"], complete=True)
    assert _blocked(s, c, t, P["ivan"], None) is None


def test_a_stage_without_forms_needs_none_and_a_step_may_name_its_own(s):
    c = tm_case(s, state="triage")  # the SAR record is filled in investigation, not triage
    assert requirement.check(s, c, _step(s, c)) is None
    assert requirement.check(s, c, _step(s, c, forms=[SAR])) == "Fill in the form 'SAR decision record'"
    assert "does not have" in requirement.check(s, c, _step(s, c, forms=["no_such_form"]))


@pytest.mark.parametrize("forms_value", ["sar_decision", [""], [3]])
def test_a_step_naming_forms_must_name_them_as_a_list(forms_value):
    from app.cases.case_types import library

    ct = library()["tm_alert_investigation"]
    ct = ct | {"transitions": [dict(ct["transitions"][0], forms=forms_value), *ct["transitions"][1:]]}
    with pytest.raises(CaseTypeError, match="forms must be a list"):
        validate(ct)
