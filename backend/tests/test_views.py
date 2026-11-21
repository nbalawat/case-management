"""Grid settings and saved views live on the server: per person, shareable with the team, changed only by their owner."""
from test_client_api import h

GRID = "/api/grids/cases"


def test_settings_follow_the_person_not_the_browser(client):
    assert client.put(f"{GRID}/settings/density", json={"value": "compact"}, headers=h("meridian", "ana")).status_code == 200
    assert client.put(f"{GRID}/settings/columns", json={"value": {"owner": False}}, headers=h("meridian", "ana")).status_code == 200
    assert client.get(GRID, headers=h("meridian", "ana")).json()["settings"] == {"density": "compact", "columns": {"owner": False}}
    assert client.get(GRID, headers=h("meridian", "ivan")).json()["settings"] == {}  # someone else's are their own
    assert client.put(f"{GRID}/settings/anything", json={"value": 1}, headers=h("meridian", "ana")).status_code == 422
    assert client.put(f"{GRID}/settings/columns", json={"value": "x" * 9000}, headers=h("meridian", "ana")).status_code == 422
    assert client.get("/api/grids/Not A Grid", headers=h("meridian", "ana")).status_code == 422


def test_a_view_is_private_until_shared_and_only_its_owner_changes_it(client):
    v = client.post(f"{GRID}/views", json={"name": "  My  urgent ", "query": "?priority=high&page=3&size=50"}, headers=h("meridian", "ana")).json()
    assert (v["name"], v["query"], v["shared"], v["mine"]) == ("My urgent", "priority=high", False, True)  # filters kept, not the page
    assert [x["name"] for x in client.get(GRID, headers=h("meridian", "ivan")).json()["views"]] == []
    shared = client.post(f"{GRID}/views/{v['id']}/share", json={"shared": True}, headers=h("meridian", "ana")).json()
    assert shared["shared"]
    seen = client.get(GRID, headers=h("meridian", "ivan")).json()["views"]
    assert [(x["name"], x["owner"], x["owner_name"], x["mine"]) for x in seen] == [("My urgent", "ana", "Ana Ruiz", False)]
    assert client.delete(f"{GRID}/views/{v['id']}", headers=h("meridian", "ivan")).status_code == 403
    assert client.post(f"{GRID}/views/{v['id']}/share", json={"shared": False}, headers=h("meridian", "ivan")).status_code == 403
    again = client.post(f"{GRID}/views", json={"name": "My urgent", "query": "priority=critical", "shared": True}, headers=h("meridian", "ana")).json()
    assert again["id"] == v["id"] and again["query"] == "priority=critical"  # same name: replaced, not duplicated
    assert client.get(GRID, headers=h("kestrel")).json()["views"] == []  # another client never sees it
    assert client.delete(f"{GRID}/views/{v['id']}", headers=h("meridian", "ana")).status_code == 200
    assert client.delete(f"{GRID}/views/{v['id']}", headers=h("meridian", "ana")).status_code == 404


def test_views_kept_in_a_browser_can_be_brought_over_once(client):
    old = [{"name": "Open SARs", "query": "type=sar"}, {"name": "Open SARs", "query": "dupe"}, {"name": "", "query": "x"}]
    out = client.post(f"{GRID}/views/import", json={"views": old}, headers=h("meridian", "maya")).json()
    assert out["imported"] == 1 and [v["name"] for v in out["views"] if v["mine"]] == ["Open SARs"]
    assert client.post(f"{GRID}/views/import", json={"views": old}, headers=h("meridian", "maya")).json()["imported"] == 0


def test_a_read_only_reviewer_keeps_views_but_cannot_share_them(client):
    from test_client_api import _auditor

    who = _auditor("meridian")
    assert client.post(f"{GRID}/views", json={"name": "Sample"}, headers=h("meridian", who)).status_code == 200
    assert client.post(f"{GRID}/views", json={"name": "Team", "shared": True}, headers=h("meridian", who)).status_code == 422
