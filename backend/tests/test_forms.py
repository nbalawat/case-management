"""Forms: definitions and their rules, the JSON Schema they compile to, filling one in on a case, versions per client."""
from datetime import datetime

import pytest
import yaml
from jsonschema import Draft202012Validator

from app.cases.case_types import load_case_types
from app.cases.service import create_case
from app.domain.models import Account, Alert, CaseEvent, FormSubmission, Party, Transaction
from app.forms import FormError, is_complete, list_forms, service, store
from app.forms.model import from_dict, parse, to_yaml
from app.forms.schema import errors, to_json_schema, visible_only
from support import PERSONAS

P = PERSONAS
T0 = datetime(2026, 5, 1, 9)
SAR = "sar_decision"


def form(**change):
    doc = {"id": "f", "title": "F", "version": "1.0", "sections": [{"title": "S", "fields": [
        {"key": "decision", "type": "choice", "options": ["yes", "no"], "required": True},
        {"key": "why", "type": "long_text", "required": True, "min_length": 10, "visible_when": "decision == no"},
        {"key": "amount", "type": "money", "minimum": 0},
        {"key": "on", "type": "date"},
        {"key": "mail", "type": "email"},
        {"key": "flags", "type": "multi_choice", "options": ["a", "b"]},
    ]}]} | change
    return from_dict(doc)


def test_conditional_fields_are_required_only_when_shown():
    f = form()
    assert errors(f, visible_only(f, {"decision": "yes", "why": "dropped"}), complete=True) == {}
    assert visible_only(f, {"decision": "yes", "why": "dropped"}) == {"decision": "yes"}
    assert errors(f, visible_only(f, {"decision": "no"}), complete=True) == {"why": "Required"}
    assert errors(f, {"decision": "no", "why": "short"}, complete=True) == {"why": "At least 10 characters"}


@pytest.mark.parametrize("data,field,message", [
    ({"amount": -1}, "amount", "At least 0"),
    ({"amount": "ten"}, "amount", "A number is needed"),
    ({"on": "31/12/2026"}, "on", "Not a date (YYYY-MM-DD)"),
    ({"mail": "nobody"}, "mail", "Not an email address"),
    ({"flags": ["a", "z"]}, "flags", "Not one of the options"),
    ({"decision": "maybe"}, "decision", "Not one of the options"),
])
def test_values_are_checked_even_in_a_draft(data, field, message):
    assert errors(form(), data, complete=False) == {field: message}


def test_a_draft_may_be_incomplete():
    assert errors(form(), {}, complete=False) == {}
    assert "decision" in errors(form(), {}, complete=True)


def test_the_json_schema_is_valid_and_enforces_the_same_rules():
    schema = to_json_schema(form())
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    assert v.is_valid({"decision": "yes"})
    assert not v.is_valid({"decision": "no"})  # why is required when shown
    assert v.is_valid({"decision": "no", "why": "long enough reason"})
    assert not v.is_valid({"decision": "yes", "why": "hidden fields are not kept"})
    assert not v.is_valid({"decision": "yes", "other": 1})


@pytest.mark.parametrize("form_id", sorted(store.library()))
def test_library_forms_compile_and_survive_yaml(form_id):
    f = store.library()[form_id]
    Draft202012Validator.check_schema(to_json_schema(f))
    assert parse(to_yaml(f)) == f


@pytest.mark.parametrize("change,error", [
    ({"id": "Bad"}, "id"),
    ({"sections": []}, "at least one section"),
    ({"sections": [{"fields": [{"key": "a"}, {"key": "a"}]}]}, "used twice"),
    ({"sections": [{"fields": [{"key": "a", "type": "colour"}]}]}, "type must be"),
    ({"sections": [{"fields": [{"key": "a", "type": "choice", "options": ["x"]}]}]}, "two different options"),
    ({"sections": [{"fields": [{"key": "a", "visible_when": "b == x"}, {"key": "b"}]}]}, "not a field above"),
    ({"sections": [{"fields": [{"key": "b", "type": "choice", "options": ["x", "y"]}, {"key": "a", "visible_when": "b == z"}]}]}, "not an option"),
    ({"sections": [{"fields": [{"key": "a", "type": "text", "minimum": 1}]}]}, "for numbers"),
    ({"sections": [{"fields": [{"key": "a", "pattern": "("}]}]}, "regular expression"),
    ({"sections": [{"fields": [{"key": "a", "prefill": "salary"}]}]}, "not a case fact"),
])
def test_structural_rules(change, error):
    with pytest.raises(FormError, match=error):
        form(**change)


# --- on a case ---------------------------------------------------------------------------------------------------
def tm_case(s, state="investigation"):
    s.add(Party(id="P1", name="Test Person"))
    s.add(Account(id="A1", party_id="P1"))
    s.add(Transaction(id="t1", account_id="A1", party_id="P1", ts=T0, amount=42_000.0, direction="out", channel="wire"))
    s.flush()
    c = create_case(s, load_case_types(s.info["tenant_id"])["tm_alert_investigation"], "P1", "medium", created=T0)
    s.add(Alert(id="alr-1", dedupe_key="k1", scenario="rapid_movement", scenario_version="1", party_id="P1", score=0.8, explanation="x",
                txn_ids=["t1"], case_id=c.id, created=T0))
    c.state = state
    s.flush()
    return c


GOOD = {"typology": "structuring", "decision": "no_file", "no_file_reason": "Payroll for a seasonal business, confirmed with invoices and the employer's tax filings."}


def test_a_case_offers_its_forms_prefilled(s):
    c = tm_case(s)
    (entry,) = service.for_case(s, c)
    assert entry["form"]["id"] == SAR and entry["status"] == "not_started" and entry["locked"] is None
    assert entry["data"] == {"amount_involved": 42000.0}
    assert set(entry["missing"]) == {"typology", "decision"}


def test_fill_complete_and_reopen(s):
    c = tm_case(s)
    assert is_complete(s, c, SAR) == (False, "Fill in the form 'SAR decision record'")
    service.save(s, c, SAR, {"typology": "structuring"}, P["ivan"])
    assert is_complete(s, c, SAR)[1] == "Complete the form 'SAR decision record'"
    with pytest.raises(service.FormInvalid) as e:
        service.save(s, c, SAR, {"typology": "structuring", "decision": "no_file"}, P["ivan"], complete=True)
    assert e.value.fields == {"no_file_reason": "Required"}
    service.save(s, c, SAR, GOOD, P["ivan"], complete=True)
    assert is_complete(s, c, SAR) == (True, None)
    with pytest.raises(FormError, match="Reopen"):
        service.save(s, c, SAR, GOOD, P["ivan"])
    with pytest.raises(FormError, match="why"):
        service.reopen(s, c, SAR, P["ivan"], " ")
    service.reopen(s, c, SAR, P["ivan"], "The customer sent new documents")
    assert is_complete(s, c, SAR)[0] is False
    kinds = [e.kind for e in s.query(CaseEvent).filter_by(case_id=c.id).order_by(CaseEvent.id) if e.kind.startswith("form.")]
    assert kinds == ["form.saved", "form.completed", "form.reopened"]


def test_forms_are_locked_outside_their_stages_and_for_people_who_do_not_work_cases(s):
    c = tm_case(s, state="triage")
    with pytest.raises(FormError, match="another stage"):
        service.save(s, c, SAR, GOOD, P["ivan"])
    c.state = "investigation"
    with pytest.raises(FormError, match="work"):
        service.save(s, c, SAR, GOOD, P["audra"])


def test_answers_stay_on_the_version_they_were_given_against(s):
    c = tm_case(s)
    service.save(s, c, SAR, GOOD, P["ivan"], complete=True)
    doc = yaml.safe_load(store.library()[SAR].source)
    doc["version"] = "1.1"
    doc["sections"][0]["fields"].append({"key": "extra", "type": "text", "required": True})
    service.publish(s, from_dict(doc), P["omar"], "add a field")
    (entry,) = service.for_case(s, c)
    assert entry["form"]["version"] == "1.0" and entry["missing"] == {}
    assert s.query(FormSubmission).one().form_version == "1.0"


def test_publish_rules(s):
    doc = yaml.safe_load(store.library()[SAR].source)
    with pytest.raises(FormError, match="higher"):
        service.publish(s, from_dict(doc), P["omar"])
    with pytest.raises(FormError, match="Unknown stage"):
        service.publish(s, from_dict(doc | {"version": "2.0", "stages": ["nowhere"]}), P["omar"])
    with pytest.raises(FormError, match="permission"):
        service.publish(s, from_dict(doc | {"version": "2.0"}), P["ivan"])
    assert {f["id"] for f in list_forms(s)} == set(store.library())


# --- over HTTP -----------------------------------------------------------------------------------------------------
def H(who=None):
    return {"X-Tenant": "meridian"} | ({"X-Persona": who} if who else {})


def test_api(client):
    forms = client.get("/api/forms", headers=H()).json()
    assert {f["id"] for f in forms} >= {SAR, "edd_questionnaire", "fraud_claim_intake"}
    ref = client.get("/api/forms/reference", headers=H()).json()
    assert any(t["id"] == "money" for t in ref["types"]) and any(ct["id"] == "tm_alert_investigation" for ct in ref["case_types"])
    one = client.get(f"/api/forms/{SAR}", headers=H()).json()
    assert one["json_schema"]["$schema"].endswith("2020-12/schema") and one["versions"][0]["active"]
    assert client.get(f"/api/forms/{SAR}/schema", headers=H()).headers["content-type"].startswith("application/schema+json")
    draft = {k: one[k] for k in ("id", "title", "description", "case_types", "stages", "sections")} | {"version": "1.1"}
    checked = client.post("/api/forms/check", json={"form": draft}, headers=H()).json()
    assert checked["ok"] and checked["changes"] == ["No change to the fields"]
    assert client.post("/api/forms/publish", json={"form": draft}, headers=H("ivan")).status_code == 403
    assert client.post("/api/forms/publish", json={"form": draft}, headers=H("omar")).json()["version"] == "1.1"
    case = next(c for c in client.get("/api/cases", headers=H("ivan")).json() if c["case_type"] == "tm_alert_investigation" and c["state"] == "investigation")
    entries = client.get(f"/api/cases/{case['id']}/forms", headers=H("ivan")).json()
    assert entries[0]["form"]["id"] == SAR
    bad = client.put(f"/api/cases/{case['id']}/forms/{SAR}", json={"data": {"decision": "no_file"}, "complete": True}, headers=H("ivan"))
    assert bad.status_code == 422 and "typology" in bad.json()["fields"]
    ok = client.put(f"/api/cases/{case['id']}/forms/{SAR}", json={"data": GOOD, "complete": True}, headers=H("ivan")).json()
    assert ok["status"] == "complete"
    assert client.post(f"/api/cases/{case['id']}/forms/{SAR}/reopen", json={"reason": "more evidence"}, headers=H("ivan")).json()["status"] == "draft"
