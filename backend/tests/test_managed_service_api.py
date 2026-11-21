"""The managed service at the HTTP boundary, against the seeded demo.

The demo provider has three engageable staff. Kestrel and Alder allow the managed service; Meridian, deployed for a
large bank that runs its own team, does not. Tests run in file order because they walk one story: what is seeded,
what each side sees, an approval, an end, and the same rules when everyone signs in with tokens.
"""
import pytest

from app.auth import dev_idp
from app.config import settings
from test_client_api import OP, h

PRIYA = h("kestrel", "ms.priya")


def test_the_operator_sees_every_clients_terms_and_every_engagement(client):
    r = client.get("/api/platform/managed-service", headers=OP).json()
    terms = {c["id"]: c["allows"] for c in r["clients"]}
    assert terms["meridian"] is None
    assert {x["id"] for x in terms["kestrel"]["roles"]} == {"analyst", "fraud_analyst", "investigator", "sanctions_officer"}
    assert terms["alder"]["approval"] == "client" and "submit_filings" in terms["alder"]["client_signoff"]
    states = {(e["tenant"], e["staff_id"]): e["status"] for e in r["engagements"]}
    assert states == {("kestrel", "priya"): "active", ("kestrel", "marcus"): "active", ("alder", "tomas"): "active",
                      ("alder", "marcus"): "proposed"}
    assert {m["id"] for m in r["staff"]} >= {"olivia", "priya", "marcus", "tomas"}
    assert next(m for m in r["staff"] if m["id"] == "olivia")["console"] is True


def test_only_console_staff_open_the_console(client):
    for who in ("priya", "marcus", "nobody"):
        assert client.get("/api/platform/managed-service", headers={"X-Operator": who}).status_code == 403
        assert client.get("/api/platform/overview", headers={"X-Operator": who}).status_code == 403
    assert client.get("/api/platform/managed-service", headers=h("kestrel", "pat")).status_code == 403  # a client admin is not staff


def test_a_member_of_staff_sees_where_they_may_work(client):
    me = client.get("/api/provider/me", headers={"X-Operator": "marcus"}).json()
    assert {(e["tenant"], e["status"]) for e in me["engagements"]} == {("kestrel", "active"), ("alder", "proposed")}
    assert client.get("/api/provider/me", headers={"X-Operator": "nobody"}).status_code == 403


def test_engaged_staff_work_the_clients_queue_like_anyone_there(client):
    me = client.get("/api/me", headers=PRIYA).json()
    assert me["tenant"]["id"] == "kestrel" and {q["id"] for q in me["queues"]} == {"aml-l1", "fraud"}
    work = client.get("/api/work", headers=PRIYA).json()
    assert len(work["mine"]) == 2 and all(c["owner"] == "ms.priya" for c in work["mine"])  # claimed when the demo was seeded
    case = work["mine"][0]["id"]
    assert client.get(f"/api/cases/{case}", headers=PRIYA).status_code == 200


def test_staff_cannot_use_their_place_anywhere_else(client):
    for t in ("meridian", "alder"):
        assert client.get("/api/me", headers=h(t, "ms.priya")).status_code == 403
    assert client.get("/api/me", headers=h("alder", "ms.marcus")).status_code == 403  # proposed, not yet approved
    for path in ("/api/access/managed-service", "/api/team"):
        assert client.get(path, headers=PRIYA).status_code == 403
    assert client.post("/api/access/publish", headers=PRIYA, json={"source": "x"}).status_code == 403


def test_the_client_sees_what_provider_staff_did(client):
    client.get("/api/cases?status=all", headers=PRIYA)
    view = client.get("/api/access/managed-service", headers=h("kestrel", "pat")).json()
    assert view["can_decide"] is True
    mine = [x for x in view["log"] if x["actor"] == "ms.priya" and x["kind"] == "provider.request"]
    assert any(x["subject"].startswith("GET /api/cases") for x in mine)
    assert all(x["actor_name"] == "Priya Raman" and x["org"] == "provider" for x in mine)
    assert not any(x["subject"].startswith("GET /api/navigation") for x in mine)  # the shell's own polling is not noise on the record
    auditor = client.get("/api/access/managed-service", headers=h("kestrel", "eli")).json()
    assert auditor["can_decide"] is False and auditor["log"]
    assert client.get("/api/access/managed-service", headers=h("kestrel", "dev")).status_code == 403  # a fraud analyst


def test_the_client_approves_a_waiting_engagement(client):
    view = client.get("/api/access/managed-service", headers=h("alder", "otto")).json()
    waiting = next(e for e in view["engagements"] if e["status"] == "proposed")
    assert waiting["name"] == "Marcus Webb"
    assert client.post(f"/api/access/engagements/{waiting['id']}/approve", headers=h("alder", "carl"), json={}).status_code == 403
    r = client.post(f"/api/access/engagements/{waiting['id']}/approve", headers=h("alder", "otto"), json={"reason": "Backlog cover"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert client.get("/api/me", headers=h("alder", "ms.marcus")).json()["name"] == "Marcus Webb"
    people = {p["id"] for p in client.get("/api/meta", headers=h("alder")).json()["personas"]}
    assert {"ms.marcus", "ms.tomas"} <= people


def test_the_operator_cannot_place_staff_where_the_client_does_not_allow(client):
    body = {"tenant": "meridian", "staff_id": "priya", "roles": ["analyst"], "queues": ["aml-l1"], "days": 30}
    r = client.post("/api/platform/engagements", headers=OP, json=body)
    assert r.status_code == 422 and "does not allow" in r.json()["detail"]
    body |= {"tenant": "kestrel", "staff_id": "tomas", "roles": ["admin"]}
    assert "only these roles" in client.post("/api/platform/engagements", headers=OP, json=body).json()["detail"]
    body |= {"roles": ["analyst"], "days": 400}
    assert client.post("/api/platform/engagements", headers=OP, json=body).status_code == 422
    assert client.post("/api/platform/engagements", headers={"X-Operator": "priya"}, json=body).status_code == 403


def test_the_operator_adds_staff_and_proposes_an_engagement(client):
    assert client.post("/api/platform/staff", headers=OP, json={"id": "zoe", "name": "Zoe Park", "title": "Analyst"}).status_code == 201
    assert client.post("/api/platform/staff", headers=OP, json={"id": "zoe", "name": "Again"}).status_code == 422
    body = {"tenant": "kestrel", "staff_id": "zoe", "roles": ["fraud_analyst"], "queues": ["fraud"], "days": 14, "note": "Peak cover"}
    r = client.post("/api/platform/engagements", headers=OP, json=body)
    assert r.status_code == 201 and r.json()["status"] == "proposed"
    assert client.get("/api/me", headers=h("kestrel", "ms.zoe")).status_code == 403
    rej = client.post(f"/api/access/engagements/{r.json()['id']}/reject", headers=h("kestrel", "pat"), json={"reason": "Not needed"})
    assert rej.json()["status"] == "rejected" and rej.json()["end_reason"] == "Not needed"


def test_ending_an_engagement_locks_the_person_out_at_once(client):
    eng = next(e for e in client.get("/api/platform/managed-service", headers=OP).json()["engagements"]
               if e["staff_id"] == "priya" and e["tenant"] == "kestrel")
    owned = [c["id"] for c in client.get("/api/work", headers=PRIYA).json()["mine"]]
    r = client.post(f"/api/platform/engagements/kestrel/{eng['id']}/end", headers=OP, json={"reason": "Rotation"})
    assert r.status_code == 200 and r.json()["status"] == "ended"
    assert client.get("/api/me", headers=PRIYA).status_code == 403
    for cid in owned:
        detail = client.get(f"/api/cases/{cid}", headers=h("kestrel", "eli")).json()
        assert detail["case"]["owner"] is None
    assert client.post(f"/api/platform/engagements/alder/{eng['id']}/end", headers=OP, json={"reason": "x"}).status_code == 404


@pytest.fixture
def sso(client):
    saved = settings.auth_mode, settings.dev_idp
    object.__setattr__(settings, "auth_mode", "oidc")
    object.__setattr__(settings, "dev_idp", True)
    yield client
    object.__setattr__(settings, "auth_mode", saved[0])
    object.__setattr__(settings, "dev_idp", saved[1])


def bearer(realm, user):
    return {"Authorization": f"Bearer {dev_idp.mint(realm, user)}"}


def test_with_sign_in_staff_use_their_providers_token_and_name_the_client(sso):
    marcus = bearer("platform", "marcus")
    assert sso.get("/api/me", headers=marcus | {"X-Tenant": "kestrel"}).json()["id"] == "ms.marcus"
    assert sso.get("/api/me", headers=marcus | {"X-Tenant": "kestrel", "X-Persona": "joy"}).json()["id"] == "ms.marcus"  # no switching
    assert sso.get("/api/me", headers=marcus | {"X-Tenant": "meridian"}).status_code == 403
    assert sso.get("/api/me", headers=marcus).status_code == 403  # no client named: no client data
    assert sso.get("/api/platform/overview", headers=marcus).status_code == 403  # not console staff
    who = sso.get("/api/auth/whoami", headers=marcus).json()
    assert who["operator"] is None and who["staff"]["name"] == "Marcus Webb"


def test_a_clients_identity_provider_cannot_speak_for_provider_staff(sso):
    for name in ("ms.marcus", "marcus"):
        assert sso.get("/api/me", headers=bearer("kestrel", name)).status_code == 403
    assert sso.get("/api/me", headers=bearer("kestrel", "ms.marcus") | {"X-Tenant": "kestrel"}).status_code == 403


def test_the_dev_provider_keeps_client_realms_to_client_people(sso):
    page = sso.get("/dev-idp/kestrel/authorize", params={"client_id": dev_idp.CLIENT_ID, "redirect_uri": "http://localhost:5173/",
                                                       "response_type": "code", "state": "s", "code_challenge": "x" * 43,
                                                       "code_challenge_method": "S256"})
    assert page.status_code == 200 and "ms.marcus" not in page.text and "Marcus Webb" not in page.text
    staff = sso.get("/dev-idp/platform/authorize", params={"client_id": dev_idp.CLIENT_ID, "redirect_uri": "http://localhost:5173/",
                                                         "response_type": "code", "state": "s", "code_challenge": "x" * 43,
                                                         "code_challenge_method": "S256"})
    assert "Marcus Webb" in staff.text and "Olivia Grant" in staff.text
