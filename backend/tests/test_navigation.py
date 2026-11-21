"""The app shell shapes itself around each person: the desks they work and the counts on their menu."""
import pytest

from app.cases.case_types import load_case_types
from app.domain.access import Persona, Sees
from app.navigation import desks


def h(who, tenant="meridian"):
    return {"X-Tenant": tenant, "X-Persona": who}


@pytest.mark.parametrize("who, expected", [
    ("ana", ["AML", "Sanctions"]),         # her queues route alert triage and sanctions adjudication
    ("ivan", ["AML"]),
    ("sofia", ["Sanctions"]),
    ("kai", ["KYC"]),
    ("fran", ["Fraud"]),
    ("quinn", ["Quality"]),
    ("tara", ["Fraud", "KYC"]),            # approvals and due diligence
    ("maya", ["AML", "KYC", "Sanctions"]),  # MLRO review, due diligence, and sanctions decisions by permission
    ("omar", ["Sanctions"]),               # owns the screening lists, works no queue
    ("audra", []),                         # the auditor works no desk
])
def test_each_person_gets_the_desks_of_their_job(client, who, expected):
    r = client.get("/api/navigation", headers=h(who))
    assert r.status_code == 200
    assert r.json()["desks"] == expected


def test_menu_counts_only_what_the_person_can_see(client):
    ivan = client.get("/api/navigation", headers=h("ivan")).json()["badges"]
    mine = client.get("/api/work", headers=h("ivan")).json()
    assert ivan["my_open"] == len(mine["mine"])
    assert set(ivan) == {"my_open", "queue_unassigned", "alerts_waiting"}
    assert client.get("/api/navigation", headers=h("audra")).json()["badges"]["queue_unassigned"] == 0  # no queues


def test_a_role_limited_to_a_case_type_puts_that_desk_on_the_menu(client):
    types = load_case_types("meridian")
    viewer = Persona(id="x", name="X", role="r", title="t", views=(Sees("all", frozenset({"fraud_claim"})),))
    assert desks(viewer, types) == ["Fraud"]
    assert desks(Persona(id="y", name="Y", role="r", title="t"), types) == []


def test_desks_follow_each_clients_own_people(client):
    meta = client.get("/api/meta", headers={"X-Tenant": "kestrel"}).json()
    for p in meta["personas"]:
        out = client.get("/api/navigation", headers=h(p["id"], "kestrel")).json()
        assert set(out["desks"]) <= {"AML", "Fraud", "KYC", "Sanctions", "Quality"}
