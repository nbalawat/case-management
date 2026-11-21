"""Access control, tested exhaustively: every person against every workflow step and every changing endpoint."""
from datetime import datetime

import pytest
import yaml
from fastapi.testclient import TestClient

from app.cases import assignments, requests
from app.cases.case_types import library, load_case_types
from app.cases.service import TransitionError, allowed_transitions, create_case, set_task, transition
from app.config import settings
from app.domain.access import AccessPolicyError, parse_policy
from app.domain.models import Account, Party
from support import PERSONAS, POLICY, T

T0 = datetime(2026, 5, 1, 9)
STEPS = [(ct["case_type"], t["from"], t["to"], i) for ct in library().values() for i, t in enumerate(ct["transitions"])]


def _case_in(s, case_type: str, state: str):
    s.add(Party(id="P1", name="Test Person"))
    s.add(Account(id="A1", party_id="P1"))
    s.flush()
    ct = load_case_types(T)[case_type]
    c = create_case(s, ct, "P1", "medium", created=T0)
    c.state, c.queue = state, ct["states"][state].get("queue")
    return c, ct


@pytest.mark.parametrize("case_type,src,dst,index", STEPS, ids=[f"{a}:{b}->{c}#{i}" for a, b, c, i in STEPS])
def test_only_the_roles_a_step_names_can_take_it(s, case_type, src, dst, index):
    """For each of the workflow steps, each of the people is either let through the role check or stopped by it."""
    c, ct = _case_in(s, case_type, src)
    step = ct["transitions"][index]
    for person in PERSONAS.values():
        offered = {(t["to"], t["label"]): t for t in allowed_transitions(s, c, person)}
        entry = offered.get((dst, step["label"]))
        if entry is None:
            continue  # the step's condition does not hold for this case; conditions are tested separately
        role_ok = person.role in step["roles"] and not person.read_only
        if role_ok:
            assert "Needs role" not in (entry["blocked"] or ""), f"{person.id} wrongly stopped at {step['label']}"
        else:
            assert entry["blocked"], f"{person.id} wrongly offered {step['label']}"
            with pytest.raises(TransitionError):
                transition(s, c, dst, person, "note")
            assert c.state == src


def test_every_role_named_in_a_workflow_exists_and_someone_holds_it():
    for ct in library().values():
        for t in ct["transitions"]:
            for role in t["roles"]:
                assert role in POLICY.roles and POLICY.holders(role), f"{ct['case_type']}: nobody can '{t['label']}'"


def test_every_queue_a_workflow_uses_has_members_who_can_act_there():
    for ct in library().values():
        for sid, st in ct["states"].items():
            if st.get("kind") == "closed":
                continue
            members = POLICY.queue_members(st["queue"])
            roles = {r for t in ct["transitions"] if t["from"] == sid for r in t["roles"]}
            assert any(m.role in roles for m in members), f"{ct['case_type']}.{sid}: nobody in queue {st['queue']} can move the case"


def test_only_queue_members_can_claim(s):
    c, _ = _case_in(s, "tm_alert_investigation", "triage")
    for person in PERSONAS.values():
        if "aml-l1" in person.queues:
            assignments.claim(s, c, person)
            assert c.owner == person.id
        else:
            with pytest.raises(TransitionError):
                assignments.claim(s, c, person)


def test_read_only_and_non_working_roles_cannot_change_a_case(s):
    c, _ = _case_in(s, "tm_alert_investigation", "triage")
    for who in ("audra", "omar"):
        with pytest.raises(TransitionError):
            requests.raise_request(s, c, PERSONAS[who], "customer", "q")
    assert not PERSONAS["audra"].can("work") and PERSONAS["audra"].can("view_team")
    set_task(s, c, "review_briefing", PERSONAS["ana"])  # a working role still can


# --- the same rules at the HTTP boundary -------------------------------------------
def _h(who):
    return {"X-Persona": who}


def test_auditor_can_read_everything_and_change_nothing(client):
    cases = client.get("/api/cases?status=all", headers=_h("audra")).json()
    cid = next(c["id"] for c in cases if c["open"] and c["case_type"] == "tm_alert_investigation")
    detail = client.get(f"/api/cases/{cid}", headers=_h("audra")).json()
    assert detail["access"]["read_only"] and all(t["blocked"] for t in detail["transitions"])
    assert client.get("/api/team", headers=_h("audra")).status_code == 200
    changing = [
        ("post", f"/api/cases/{cid}/transition", {"to": detail["transitions"][0]["to"], "note": "x"}),
        ("post", f"/api/cases/{cid}/narrative", {"text": "x"}),
        ("post", f"/api/cases/{cid}/claim", None),
        ("post", f"/api/cases/{cid}/release", None),
        ("post", f"/api/cases/{cid}/reassign", {"owner": "ivan"}),
        ("post", f"/api/cases/{cid}/tasks/review_briefing", {"done": True}),
        ("post", f"/api/cases/{cid}/requests", {"recipient": "customer", "question": "q"}),
        ("post", f"/api/cases/{cid}/agents/run", None),
        ("post", "/api/timers/run", None),
        ("post", "/api/feeds/run", {"mapping": {"txn_id": "TXN_REF"}}),
        ("post", "/api/case-types/publish", {"source": "x"}),
    ]
    for method, url, body in changing:
        r = getattr(client, method)(url, json=body, headers=_h("audra"))
        assert r.status_code == 403, f"auditor reached {url}: {r.status_code}"
    assert client.get(f"/api/cases/{cid}", headers=_h("audra")).json()["events"] == detail["events"]


@pytest.mark.parametrize("url,body,allowed", [
    ("/api/timers/run", None, {"tara", "omar"}),
    ("/api/case-types/publish", {"source": "not: a definition"}, {"omar"}),
    ("/api/feeds/run", {"mapping": {}}, {"omar"}),
])
def test_permission_gated_endpoints_admit_exactly_the_holders(client, url, body, allowed):
    for who in PERSONAS:
        status = client.post(url, json=body, headers=_h(who)).status_code
        assert (status != 403) == (who in allowed), f"{who} on {url}: {status}"


def test_team_view_is_limited_to_roles_with_the_permission(client):
    for who, person in PERSONAS.items():
        assert (client.get("/api/team", headers=_h(who)).status_code == 200) == person.can("view_team")


def test_reassign_over_http_needs_a_lead_and_a_queue_member(client):
    cases = client.get("/api/cases", headers=_h("tara")).json()
    cid = next(c["id"] for c in cases if c["queue"] == "aml-l2")
    assert client.post(f"/api/cases/{cid}/reassign", json={"owner": "lena"}, headers=_h("ivan")).status_code == 409
    assert client.post(f"/api/cases/{cid}/reassign", json={"owner": "kai"}, headers=_h("tara")).status_code == 409
    assert client.post(f"/api/cases/{cid}/reassign", json={"owner": "lena"}, headers=_h("tara")).status_code == 200
    assert client.get(f"/api/cases/{cid}", headers=_h("tara")).json()["case"]["owner"] == "lena"


# --- the policy file itself --------------------------------------------------------
def _raw():
    return yaml.safe_load((settings.access_dir / "full_service_bank.yaml").read_text())


def test_shipped_policy_loads_and_the_auditor_is_read_only():
    policy = parse_policy(_raw())
    assert policy.roles["auditor"].read_only and len(policy.people) >= 10


@pytest.mark.parametrize("mutate,message", [
    (lambda r: r["roles"]["analyst"]["permissions"].append("fly"), "unknown permission"),
    (lambda r: r["people"][0].update(role="wizard"), "unknown role"),
    (lambda r: r["people"][0].update(queues=["nowhere"]), "unknown queue"),
    (lambda r: r["people"].append(dict(r["people"][0])), "repeated"),
    (lambda r: r["roles"]["auditor"]["permissions"].append("work"), "read-only"),
    (lambda r: r["people"][-1].update(queues=["qa"]), "can work cases"),
    (lambda r: r.update(default_person="nobody"), "default_person"),
    (lambda r: r.pop("roles"), "missing 'roles'"),
])
def test_a_broken_policy_is_refused_with_a_clear_reason(mutate, message):
    raw = _raw()
    mutate(raw)
    with pytest.raises(AccessPolicyError, match=message):
        parse_policy(raw)
