"""The workflow designer over HTTP: from a template to a published workflow that real cases run on."""
from __future__ import annotations

from support import T


def h(who: str | None = None, tenant: str = T) -> dict:
    return {"X-Tenant": tenant} | ({"X-Persona": who} if who else {})


ADMIN = h("omar")


def test_the_palette_is_drawn_from_the_server(client):
    cat = client.get("/api/composer/catalog", headers=ADMIN).json()
    kinds = {b["id"]: b["kind"] for b in cat["blocks"]}
    assert kinds == {"start": "start", "stage": "stage", "wait": "stage", "approval": "stage", "decision": "decision", "outcome": "outcome"}
    assert {a["id"] for a in cat["actions"]} >= {"notify", "raise_priority", "emit_event", "request_information", "screen_party", "run_agents"}
    ref = cat["reference"]
    assert {q["id"] for q in ref["queues"]} >= {"aml-l1", "mlro"} and next(q for q in ref["queues"] if q["id"] == "mlro")["roles"] == ["mlro"]
    assert {f["id"]: f["kind"] for f in ref["facts"]}["screening_hit"] == "bool"
    assert {f["id"] for f in cat["formats"]} == {"caseforge", "case_type", "bpmn"}
    sar = next(d for d in ref["dispositions"] if d["id"] == "sar_filed")
    assert "suspicious activity report" in sar["does"]


def test_compose_check_try_publish_and_run_a_new_workflow(client):
    templates = client.get("/api/composer/templates", headers=ADMIN).json()
    assert {"investigation_to_filing", "case_type:tm_alert_investigation"} <= {t["id"] for t in templates}
    r = client.post("/api/composer/drafts", json={"template": "two_level_review"}, headers=ADMIN)
    assert r.status_code == 201
    draft = r.json()
    design = draft["design"]
    design["key"], design["title"] = "complaint_review", "Complaint review"
    saved = client.put(f"/api/composer/drafts/{draft['id']}", json={"design": design, "revision": draft["revision"]}, headers=ADMIN)
    assert saved.status_code == 200 and saved.json()["revision"] == 2
    stale = client.put(f"/api/composer/drafts/{draft['id']}", json={"design": design, "revision": 1}, headers=ADMIN)
    assert stale.status_code == 409 and "Reload" in stale.json()["detail"]

    check = client.post("/api/composer/validate", json={"design": design}, headers=ADMIN).json()
    assert check["ok"] and check["is_new"] and check["changes"] == ["New case type 'complaint_review'"]
    assert check["summary"]["stages"] == 3 and "case_type: complaint_review" in check["yaml"]

    path = ["second_line_review", "approval", "actioned"]
    tried = client.post("/api/composer/trial", json={"design": design, "steps": path}, headers=ADMIN).json()
    assert tried["ok"] and tried["case"]["disposition"] == "actioned", tried
    assert {r["outcome"] for r in tried["routes"]["routes"]} == {"Closed, no action", "Actioned"}

    assert client.post(f"/api/composer/drafts/{draft['id']}/publish", json={}, headers=h("ana")).status_code == 403
    pub = client.post(f"/api/composer/drafts/{draft['id']}/publish", json={"note": "First version"}, headers=ADMIN)
    assert pub.status_code == 200, pub.text
    assert pub.json()["version"] == "1.0.0" and pub.json()["draft"]["status"] == "published"
    types = {t["case_type"]: t for t in client.get("/api/case-types", headers=ADMIN).json()}
    assert types["complaint_review"]["versions"][0]["note"] == "First version"

    party = client.get("/api/cases?status=all", headers=h("maya")).json()[0]["party_id"]
    opened = client.post("/api/composer/cases", json={"case_type": "complaint_review", "party_id": party}, headers=h("ana"))
    assert opened.status_code == 201, opened.text
    cid = opened.json()["case_id"]
    case = client.get(f"/api/cases/{cid}", headers=h("ana")).json()
    assert case["case"]["state"] == "first_line_review"
    for t in case["tasks"]:
        client.post(f"/api/cases/{cid}/tasks/{t['id']}", json={"done": True}, headers=h("ana"))
    moved = client.post(f"/api/cases/{cid}/transition", json={"to": "second_line_review"}, headers=h("ana"))
    assert moved.status_code == 200, moved.text
    assert client.get(f"/api/cases/{cid}", headers=h("ana")).json()["case"]["state"] == "second_line_review"

    again = client.post(f"/api/composer/drafts/{draft['id']}/publish", json={}, headers=ADMIN)
    assert again.status_code == 422 and "not newer" in again.json()["detail"]


def test_import_export_round_trip_over_http(client):
    d = client.post("/api/composer/drafts", json={"template": "screening_triage"}, headers=ADMIN).json()["design"]
    for fmt in ("caseforge", "case_type", "bpmn"):
        out = client.post("/api/composer/export", json={"design": d, "format": fmt}, headers=ADMIN).json()
        assert out["filename"].startswith(d["key"])
        back = client.post("/api/composer/import", json={"content": out["content"], "filename": out["filename"]}, headers=ADMIN).json()
        assert back["format"] == fmt and back["errors"] == 0, (fmt, back)
    bad = client.post("/api/composer/import", json={"content": "<nope"}, headers=ADMIN)
    assert bad.status_code == 422 and "XML" in bad.json()["detail"]


def test_a_broken_design_explains_itself_and_cannot_be_tried(client):
    d = client.post("/api/composer/drafts", json={"template": "blank"}, headers=ADMIN).json()["design"]
    d["edges"] = [e for e in d["edges"] if e["source"] != "work"]
    check = client.post("/api/composer/validate", json={"design": d}, headers=ADMIN).json()
    assert not check["ok"] and any("dead end" in i["message"] and i["id"] == "work" for i in check["issues"])
    tried = client.post("/api/composer/trial", json={"design": d}, headers=ADMIN).json()
    assert tried["ready"] is False and "dead end" in tried["why"]
    bad_key = client.post("/api/composer/validate", json={"design": d | {"key": "Bad Key"}}, headers=ADMIN)
    assert bad_key.status_code == 422 and "lower case" in bad_key.json()["detail"]


def test_drafts_belong_to_one_client(client):
    mine = client.post("/api/composer/drafts", json={"template": "blank"}, headers=ADMIN).json()
    other = client.get("/api/composer/drafts", headers=h(None, "kestrel")).json()
    assert mine["id"] not in {d["id"] for d in other}
    assert client.get(f"/api/composer/drafts/{mine['id']}", headers=h(None, "kestrel")).status_code == 404
    archived = client.post(f"/api/composer/drafts/{mine['id']}/archive", json={}, headers=ADMIN).json()
    assert archived["status"] == "archived" and mine["id"] not in {d["id"] for d in client.get("/api/composer/drafts", headers=ADMIN).json()}


def test_only_people_who_work_cases_open_them_by_hand(client):
    r = client.post("/api/composer/cases", json={"case_type": "tm_alert_investigation", "party_id": "x"}, headers=h("audra"))
    assert r.status_code == 403
    r = client.post("/api/composer/cases", json={"case_type": "nope", "party_id": "x"}, headers=h("ana"))
    assert r.status_code == 404
