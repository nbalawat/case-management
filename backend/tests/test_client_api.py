"""Tenancy at the HTTP boundary, against the seeded demo: three clients, one platform operator."""
import json

import pytest
import yaml

from app.seed import DEMO_CLIENTS
from app.tenants.templates import templates

CLIENTS = [c["tenant_id"] for c in DEMO_CLIENTS]
PEOPLE = {c["tenant_id"]: yaml.safe_load(templates()[c["template"]].access_source) for c in DEMO_CLIENTS}
EVERYONE = [(t, p["id"]) for t in CLIENTS for p in PEOPLE[t]["people"]]
OP = {"X-Operator": "olivia"}


def h(tenant, who=None):
    return {"X-Tenant": tenant} | ({"X-Persona": who} if who else {})


def _permissions(tenant, who):
    raw = PEOPLE[tenant]
    person = next(p for p in raw["people"] if p["id"] == who)
    roles = person.get("roles") or [person["role"]]
    read_only = any(raw["roles"][r].get("read_only") for r in roles)
    return {p for r in roles for p in raw["roles"][r]["permissions"] if not read_only or p.startswith("view_")}


def _admin(tenant):
    return next(p for t, p in EVERYONE if t == tenant and "manage_access" in _permissions(t, p))


def _auditor(tenant):
    """Someone who sees every case at the client, restricted or not."""
    return next((p["id"] for p in PEOPLE.get(tenant, {"people": []})["people"] if p.get("role") == "auditor"), None)


def everything(tenant):
    return h(tenant, _auditor(tenant))


@pytest.fixture(scope="module")
def ids(client):
    """For each client: every case id and one of each addressable thing."""
    out = {}
    for t in CLIENTS:
        cases = client.get("/api/cases?status=all", headers=everything(t)).json()
        rfi = next((r["id"] for c in cases for r in client.get(f"/api/cases/{c['id']}", headers=everything(t)).json()["requests"]), None)
        out[t] = {"cases": {c["id"] for c in cases}, "open": next(c["id"] for c in cases if c["open"]), "rfi": rfi,
                  "parties": {c["party_name"] for c in cases}}
    return out


def test_each_client_has_its_own_people_work_and_configuration(client, ids):
    seen = []
    for t in CLIENTS:
        meta = client.get("/api/meta", headers=h(t)).json()
        own = {p["id"] for p in meta["personas"] if not p["id"].startswith("ms.")}  # ms.: the provider's staff, under an engagement
        assert own == {p["id"] for p in PEOPLE[t]["people"]}
        types = {ct["case_type"]: ct["version"] for ct in client.get("/api/case-types", headers=h(t)).json()}
        assert set(types) == set(templates()[next(c["template"] for c in DEMO_CLIENTS if c["tenant_id"] == t)].case_types)
        seen.append((ids[t]["cases"], types))
    assert not (seen[0][0] & seen[1][0]) and not (seen[0][0] & seen[2][0]) and not (seen[1][0] & seen[2][0])
    assert seen[2][1]["kyc_refresh"] == "3.1.0" and seen[0][1]["kyc_refresh"] == "3.0.0"  # Alder published its own version
    alder = next(ct for ct in client.get("/api/case-types", headers=h("alder")).json() if ct["case_type"] == "kyc_refresh")
    on = {v["version"]: v["open_cases"] for v in alder["versions"]}
    assert on["3.0.0"] > 0 and on["3.1.0"] > 0  # open cases stay on the version they started under


@pytest.mark.parametrize("asker,owner", [(a, b) for a in CLIENTS for b in CLIENTS if a != b])
def test_another_clients_ids_resolve_to_nothing(client, ids, asker, owner):
    cid, rfi = ids[owner]["open"], ids[owner]["rfi"]
    who = _admin(asker)
    worker = next(p for t, p in EVERYONE if t == asker and "work" in _permissions(t, p))
    probes = [("get", f"/api/cases/{cid}", None, who), ("post", f"/api/cases/{cid}/transition", {"to": "investigation", "note": "x"}, worker),
              ("post", f"/api/cases/{cid}/narrative", {"text": "x"}, worker), ("post", f"/api/cases/{cid}/claim", None, worker),
              ("post", f"/api/cases/{cid}/release", None, worker), ("post", f"/api/cases/{cid}/reassign", {"owner": worker}, worker),
              ("post", f"/api/cases/{cid}/tasks/review_briefing", {"done": True}, worker),
              ("post", f"/api/cases/{cid}/requests", {"recipient": "customer", "question": "q"}, worker),
              ("post", f"/api/cases/{cid}/agents/run", None, worker)]
    if rfi:
        probes.append(("post", f"/api/requests/{rfi}/answer", {"response": "x"}, worker))
    before = client.get(f"/api/cases/{cid}", headers=h(owner)).json()
    for method, url, body, person in probes:
        r = getattr(client, method)(url, headers=h(asker, person), **({"json": body} if method == "post" else {}))
        assert r.status_code == 404, f"{asker} reached {owner}'s {url}: {r.status_code}"
    assert client.get(f"/api/cases/{cid}", headers=h(owner)).json() == before  # and nothing changed
    for url in ("/api/cases?status=all", "/api/work", "/api/events?limit=500", "/api/dashboard", "/api/feeds"):
        text = client.get(url, headers=h(asker, who)).text
        assert not any(i in text for i in ids[owner]["cases"]), f"{owner}'s case ids appear in {asker}'s {url}"


def test_a_person_exists_only_at_their_own_client(client):
    for tenant, who in EVERYONE:
        for other in CLIENTS:
            status = client.get("/api/me", headers=h(other, who)).status_code
            assert status == (200 if other == tenant or any(p == who and t == other for t, p in EVERYONE) else 403)
    assert client.get("/api/me", headers=h("kestrel", "omar")).json()["detail"] == "'omar' is not a user of Kestrel Payments"
    assert client.get("/api/cases", headers=h("ghost-bank")).status_code == 404
    me = client.get("/api/me", headers=h("kestrel", "joy")).json()
    assert me["roles"] == ["mlro", "team_lead"] and me["tenant"] == {"id": "kestrel", "name": "Kestrel Payments"}


@pytest.mark.parametrize("url,body,permission", [
    ("/api/timers/run", None, "run_timers"),
    ("/api/case-types/publish", {"source": "not: a definition"}, "publish_workflow"),
    ("/api/feeds/run", {"mapping": {}}, "load_data"),
    ("/api/access/publish", {"source": "not: a policy"}, "manage_access"),
])
def test_permission_gates_follow_each_clients_own_policy(client, url, body, permission):
    for tenant, who in EVERYONE:
        status = client.post(url, json=body, headers=h(tenant, who)).status_code
        assert (status != 403) == (permission in _permissions(tenant, who)), f"{who}@{tenant} on {url}: {status}"


def test_team_view_follows_each_clients_own_policy(client):
    for tenant, who in EVERYONE:
        status = client.get("/api/team", headers=h(tenant, who)).status_code
        assert (status == 200) == ("view_team" in _permissions(tenant, who)), f"{who}@{tenant}: {status}"
    team = client.get("/api/team", headers=h("kestrel", "joy")).json()
    assert {q["id"] for q in team["queues"]} == set(PEOPLE["kestrel"]["queues"]) and "qa" not in {q["id"] for q in team["queues"]}


def test_the_same_alert_id_from_two_clients_makes_two_cases(client):
    alert = {"source": "legacy-tm", "external_id": "DUP-1", "scenario": "rapid_movement", "party_id": "P10005", "score": 0.8, "explanation": "rule 12"}
    a = client.post("/api/alerts", json=alert, headers=h("meridian")).json()
    b = client.post("/api/alerts", json=alert, headers=h("kestrel")).json()
    again = client.post("/api/alerts", json=alert, headers=h("meridian")).json()
    assert not a["duplicate"] and not b["duplicate"] and again["duplicate"] and a["case_id"] != b["case_id"]
    names = {t: client.get(f"/api/cases/{c['case_id']}", headers=h(t)).json()["party"]["name"] for t, c in (("meridian", a), ("kestrel", b))}
    assert names["meridian"] != names["kestrel"]  # customer P10005 is a different person at each client
    assert client.get(f"/api/cases/{a['case_id']}", headers=h("kestrel")).status_code == 404


def test_loading_a_feed_changes_only_the_loading_client(client):
    def kpis(t):
        return client.get("/api/dashboard", headers=h(t)).json()["kpis"]

    before = {t: kpis(t) for t in CLIENTS}
    profile = client.post("/api/feeds/profile", json={}, headers=h("kestrel", "pat")).json()
    mapping = {p["target"]: p["source"] for p in profile["proposals"]}
    run = client.post("/api/feeds/run", json={"mapping": mapping}, headers=h("kestrel", "pat")).json()
    assert run["feed"]["stats"]["loaded"] > 1000 and run["cases"]
    after = {t: kpis(t) for t in CLIENTS}
    assert after["kestrel"]["transactions"] == before["kestrel"]["transactions"] + run["feed"]["stats"]["loaded"]
    assert after["meridian"] == before["meridian"] and after["alder"] == before["alder"]
    assert client.get("/api/feeds", headers=h("meridian")).json()["feeds"] == []
    again = client.post("/api/feeds/run", json={"mapping": mapping}, headers=h("alder", "otto")).json()  # same file, another client
    assert again["feed"]["stats"]["loaded"] == run["feed"]["stats"]["loaded"] and again["feed"]["stats"]["duplicates"] == 0


def test_a_client_changes_its_own_access_policy_over_http(client):
    page = client.get("/api/access", headers=h("kestrel", "pat")).json()
    assert page["can_edit"] and page["policy"]["version"] == "1.0.0" and not client.get("/api/access", headers=h("kestrel", "nia")).json()["can_edit"]
    raw = yaml.safe_load(page["yaml"])
    raw["version"] = "1.1.0"
    raw["people"].append({"id": "uma", "name": "Uma Reyes", "role": "fraud_analyst", "queues": ["fraud"]})
    draft = yaml.safe_dump(raw, sort_keys=False)
    assert client.get("/api/me", headers=h("kestrel", "uma")).status_code == 403
    check = client.post("/api/access/check", json={"source": draft}, headers=h("kestrel", "pat")).json()
    assert check["ok"] and check["changes"] == ["Adds Uma Reyes (Fraud analyst)"]
    broken = client.post("/api/access/check", json={"source": draft.replace("role: head_fiu", "role: mlro")}, headers=h("kestrel", "pat")).json()
    assert not broken["ok"] and "senior-approval" in broken["error"]
    assert client.post("/api/access/publish", json={"source": draft}, headers=h("kestrel", "nia")).status_code == 403
    assert client.post("/api/access/publish", json={"source": draft, "note": "new joiner"}, headers=h("kestrel", "pat")).status_code == 200
    assert client.get("/api/me", headers=h("kestrel", "uma")).json()["queues"] == [{"id": "fraud", "label": "Fraud claims"}]
    assert client.get("/api/me", headers=h("meridian", "uma")).status_code == 403 and client.get("/api/me", headers=h("alder", "uma")).status_code == 403
    history = client.get("/api/access", headers=h("kestrel", "pat")).json()["history"]
    assert [(v["version"], v["published_by"], v["note"]) for v in history][0] == ("1.1.0", "pat", "new joiner")
    assert client.get("/api/access", headers=h("meridian")).json()["policy"]["version"] == "1.0.0"


# --- the operator console ---------------------------------------------------------
def test_the_operator_console_is_for_platform_staff_only(client):
    for url in ("/api/platform/overview", "/api/platform/templates"):
        assert client.get(url).status_code == 403
        assert client.get(url, headers={"X-Operator": "mallory"}).status_code == 403
        for tenant, who in EVERYONE:
            assert client.get(url, headers=h(tenant, who)).status_code == 403
        assert client.get(url, headers=OP).status_code == 200
    body = {"id": "sneaky", "name": "Sneaky", "template": "private_bank"}
    assert client.post("/api/platform/tenants", json=body, headers=h("meridian", "omar")).status_code == 403
    assert client.post("/api/platform/tenants/meridian/status", json={"status": "suspended"}, headers=h("meridian", "omar")).status_code == 403


def test_the_operator_sees_counts_and_never_client_content(client, ids):
    overview = client.get("/api/platform/overview", headers=OP).json()
    rows = {r["id"]: r for r in overview["tenants"]}
    assert set(CLIENTS) <= set(rows)
    for t in CLIENTS:
        cases = client.get("/api/cases?status=all", headers=everything(t)).json()
        assert rows[t]["cases"] == len(cases) and rows[t]["open_cases"] == sum(c["open"] for c in cases)
        assert rows[t]["transactions"] == client.get("/api/dashboard", headers=h(t)).json()["kpis"]["transactions"]
    assert overview["totals"]["open_cases"] == sum(r["open_cases"] for r in rows.values())
    text = json.dumps(overview)
    for t in CLIENTS:
        assert not any(i in text for i in ids[t]["cases"]) and not any(n in text for n in ids[t]["parties"])
    assert client.get("/api/cases", headers=OP | {"X-Tenant": "platform"}).status_code == 404  # being an operator opens no client


def test_onboarding_a_client_from_the_console_and_suspending_it(client):
    body = {"id": "northwind", "name": "Northwind Credit Union", "template": "fintech_payments", "region": "us-west", "sample_data": True}
    assert client.post("/api/platform/tenants/check", json=body, headers=OP).json() == {
        "ok": True, "case_types": ["fraud_claim", "tm_alert_investigation", "sanctions_hit"], "people": 9, "roles": 11, "queues": 8}
    for bad, message in ((body | {"id": "meridian"}, "already exists"), (body | {"id": "No"}, "Client id"),
                         (body | {"case_types": ["kyc_refresh"]}, "unknown queue"), (body | {"template": "x"}, "Unknown template")):
        assert message in client.post("/api/platform/tenants/check", json=bad, headers=OP).json()["error"]
        assert client.post("/api/platform/tenants", json=bad, headers=OP).status_code == 422
    assert client.get("/api/cases", headers=h("northwind")).status_code == 404
    created = client.post("/api/platform/tenants", json=body, headers=OP)
    assert created.status_code == 201 and created.json()["sample_data"]["cases"] > 5
    cases = client.get("/api/cases?status=all", headers=h("northwind", "nia")).json()
    assert len(cases) == created.json()["sample_data"]["cases"]
    assert client.get("/api/work", headers=h("northwind", "nia")).json()["persona"]["tenant"]["name"] == "Northwind Credit Union"
    assert "northwind" in {t["id"] for t in client.get("/api/tenants").json()}
    # its policy is its own copy: Kestrel's later change (if any) is not here, and a change here stays here
    assert client.get("/api/access", headers=h("northwind")).json()["policy"]["version"] == "1.0.0"
    assert client.post("/api/platform/tenants/northwind/status", json={"status": "archived"}, headers=OP).status_code == 422
    assert client.post("/api/platform/tenants/northwind/status", json={"status": "suspended"}, headers=OP).json()["status"] == "suspended"
    assert client.get("/api/cases", headers=h("northwind", "nia")).status_code == 403
    assert client.post("/api/alerts", json={"source": "x", "external_id": "1", "scenario": "card_fraud", "party_id": "P1", "score": 0.5,
                                            "explanation": "x"}, headers=h("northwind")).status_code == 403
    assert client.get("/api/cases", headers=h("kestrel")).status_code == 200  # nobody else is affected
    assert client.post("/api/platform/tenants/northwind/status", json={"status": "live"}, headers=OP).json()["status"] == "live"
    assert len(client.get("/api/cases?status=all", headers=h("northwind")).json()) == len(cases)


def test_the_operator_can_switch_case_types_but_not_from_under_open_cases(client):
    r = client.put("/api/platform/tenants/alder/case-types", json={"case_types": ["kyc_onboarding"]}, headers=OP)
    assert r.status_code == 422 and "Open cases still use" in r.json()["detail"]
    r = client.put("/api/platform/tenants/alder/case-types", json={"case_types": ["fraud_claim"]}, headers=OP)
    assert r.status_code == 422 and "unknown queue" in r.json()["detail"]


# --- the oracle for customer rules, written from the policy's meaning and nothing else --------------------------------
def reaches(tenant, who, party: dict) -> bool:
    """Whether this person's roles reach a customer {booking, country, segment, rm}, by the YAML alone."""
    raw = PEOPLE[tenant]
    person = next(p for p in raw["people"] if p["id"] == who)
    roles = person.get("roles") or [person["role"]]
    where = party.get("booking") or party["country"]

    def meet(a, b):
        return b if a is None else a if b is None else set(a) & set(b)

    for r in roles:
        c = raw["roles"][r].get("customers") or {}
        j, sg = meet(c.get("jurisdictions"), person.get("jurisdictions")), meet(c.get("segments"), person.get("segments"))
        if (j is None or where in j) and (sg is None or party["segment"] in sg) and (c.get("book", "any") != "own" or party["rm"] == who):
            return True
    return False


def hidden_types(tenant, who) -> set:
    """Case types tipping-off rules hide from this person: all the confidential ones if any of their roles faces customers."""
    raw = PEOPLE[tenant]
    person = next(p for p in raw["people"] if p["id"] == who)
    roles = person.get("roles") or [person["role"]]
    facing = any(raw["roles"][r].get("customer_facing") for r in roles)
    return set((raw.get("tipping_off") or {}).get("case_types", ["tm_alert_investigation"])) if facing else set()
