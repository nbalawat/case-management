"""Ask anything over HTTP, against the seeded demo: every person at every client, asking as themselves."""
import pytest

from test_client_api import CLIENTS, EVERYONE, _auditor, _permissions, h, ids  # noqa: F401  (ids is a fixture)

QUESTIONS = ["How many open cases are there by case type?", "Show the largest open cases by amount", "Which scenario produces the most SARs?"]


def ask(client, tenant, who, question, **extra):
    """`who` None: the client's auditor, who sees every case."""
    who = who or _auditor(tenant)
    return client.post("/api/ask", headers=h(tenant, who), json={"question": question} | extra)


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_everyone_can_ask_and_only_ever_gets_their_own_clients_cases(client, ids, tenant, who):
    others = set().union(*(ids[t]["cases"] for t in CLIENTS if t != tenant))
    mine = {c["id"] for c in client.get("/api/cases?status=all", headers=h(tenant, who)).json()}  # what this person may see
    assert mine <= ids[tenant]["cases"]
    for q in QUESTIONS:
        out = ask(client, tenant, who, q)
        assert out.status_code == 200, out.text
        body = out.json()
        assert body["status"] == "answered" and body["unverified"] == [] and body["scope"]["client"]
        cited = {c["id"] for c in body["cited"]}
        shown = {i for st in body["steps"] for r in st["result"].get("rows", []) for i in r["case_ids"]} \
            | {c["id"] for st in body["steps"] for c in st["result"].get("cases", [])}
        assert (cited | shown) <= mine and not (cited | shown) & others
        assert cited or mine != ids[tenant]["cases"]  # someone who sees everything always gets cases back
        totals = [st["result"]["matching_cases"] for st in body["steps"] if "matching_cases" in st["result"]]
        assert totals and all(n <= len(mine) for n in totals)


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_team_workload_questions_follow_each_clients_own_policy(client, tenant, who):
    sees = "view_team" in _permissions(tenant, who)
    start = client.get("/api/ask/start", headers=h(tenant, who)).json()
    assert start["scope"]["team_workload"] == sees and ("team_workload" in start["scope"]["tools"]) == sees
    assert ("Who has the most open work?" in start["suggestions"]) == sees
    for q in ("Who has the most open work?", "open cases by owner"):
        body = ask(client, tenant, who, q).json()
        assert body["steps"][0]["ok"] == sees
        if not sees:
            assert "team view" in body["answer"] and body["cited"] == [] and "result" in body["steps"][0] and "rows" not in body["steps"][0]["result"]


@pytest.mark.parametrize("tenant", CLIENTS)
def test_another_clients_case_id_is_unknown_here(client, ids, tenant):
    other = next(t for t in CLIENTS if t != tenant)
    theirs = sorted(ids[other]["cases"])[0]
    body = ask(client, tenant, None, f"tell me about {theirs}").json()
    assert body["steps"][0]["ok"] is False and body["cited"] == [] and "No case" in body["answer"]
    mine = sorted(ids[tenant]["cases"])[0]
    assert [c["id"] for c in ask(client, tenant, None, f"tell me about {mine}").json()["cited"]][0] == mine


def test_the_same_question_gets_each_clients_own_answer(client, ids):
    totals = {}
    for t in CLIENTS:
        body = ask(client, t, None, "how many cases are there in total").json()
        totals[t] = body["steps"][0]["result"]["matching_cases"]
        assert totals[t] == len(ids[t]["cases"])
    assert len(set(totals.values())) > 1


def test_identity_is_checked_before_anything_is_answered(client):
    assert ask(client, "meridian", "nobody", "how many open cases").status_code == 403
    assert ask(client, "nowhere", None, "how many open cases").status_code == 404
    assert ask(client, "kestrel", "ivan", "how many open cases").status_code == 403  # a real person, at another client
    assert client.get("/api/ask/log", headers=h("nowhere")).status_code == 404


def test_bad_questions_are_rejected(client):
    assert ask(client, "meridian", "tara", "  ").status_code == 422
    assert ask(client, "meridian", "tara", "x" * 1001).status_code == 422
    assert client.post("/api/ask", headers=h("meridian", "tara"), json={}).status_code == 422


def test_question_record_is_per_client_and_per_person(client):
    marker = "how many open cases are critical priority"
    asked = ask(client, "meridian", "ana", marker).json()
    own = client.get("/api/ask/log", headers=h("meridian", "ana")).json()
    assert own[0]["question"] == marker and {r["asked_by"] for r in own} == {"ana"}
    assert all(r["question"] != marker for r in client.get("/api/ask/log", headers=h("meridian", "ivan")).json())
    lead = client.get("/api/ask/log", headers=h("meridian", "tara")).json()
    assert any(r["id"] == asked["id"] and r["asked_by_name"] == "Ana Ruiz" for r in lead)
    for other in ("kestrel", "alder"):
        for who in [p for t, p in EVERYONE if t == other]:
            assert all(r["id"] != asked["id"] for r in client.get("/api/ask/log", headers=h(other, who)).json())


def test_read_only_people_can_ask_and_asking_changes_no_case(client):
    before = client.get("/api/cases?status=all", headers=h("meridian")).json()
    assert ask(client, "meridian", "audra", "how many cases are closed").json()["status"] == "answered"
    assert client.get("/api/cases?status=all", headers=h("meridian")).json() == before


def test_a_suspended_client_cannot_ask(client):
    op = {"X-Operator": "olivia"}
    assert client.post("/api/platform/tenants/alder/status", headers=op, json={"status": "suspended"}).status_code == 200
    try:
        assert ask(client, "alder", None, "how many open cases").status_code == 403
    finally:
        assert client.post("/api/platform/tenants/alder/status", headers=op, json={"status": "live"}).status_code == 200
    assert ask(client, "alder", None, "how many open cases").status_code == 200
