"""In-app notifications: written as things happen to a person's cases, never for their own actions, and only theirs to read."""
from datetime import timedelta

from app.cases import assignments, merge
from app.notifications import rules, store
from support import PERSONAS as P
from test_client_api import h
from test_operations import T0, _pair


def _inbox(s, who):
    return store.inbox(s, who)


def test_a_reassignment_tells_the_new_owner_and_nobody_else(s):
    a, _ = _pair(s)
    a.queue = "aml-l2"
    assignments.reassign(s, a, "lena", P["tara"], "Holiday cover")
    s.flush()
    got = _inbox(s, "lena")
    assert got["unread"] == 1 and got["items"][0]["title"] == f"Case {a.id} was assigned to you" and got["items"][0]["body"] == "Holiday cover"
    assert _inbox(s, "tara")["unread"] == 0  # never told about your own action


def test_a_merge_tells_the_owner_of_the_folded_case_and_an_undo_tells_them_again(s):
    a, b = _pair(s)
    b.owner = "ivan"
    merge.merge(s, b, a, P["ana"], "One matter", ts=T0 + timedelta(days=1))
    s.flush()
    assert [n["title"] for n in _inbox(s, "ivan")["items"]] == [f"Your case {b.id} was merged into {a.id}"]
    merge.unmerge(s, b, P["ana"], "Two matters after all", ts=T0 + timedelta(days=2))
    s.flush()
    assert _inbox(s, "ivan")["items"][0]["title"].startswith(f"Case {b.id} is open again")
    assert store.mark_read(s, "ivan") == 2 and _inbox(s, "ivan")["unread"] == 0 and store.mark_read(s, "ivan") == 0


def test_rules_leave_out_the_actor_and_unknown_events():
    ctx = rules.Context(owner="ana", holders=lambda role: ["tara", "ana"])
    note = rules.notes_for("sla.stage_breached", "case-1", {"stage": "triage", "notify": "team_lead", "priority_from": "low", "priority_to": "medium"}, "timers", ctx)
    assert note.people == ("ana", "tara") and "past its triage deadline" in note.title and "medium" in note.body
    assert rules.notes_for("workflow.notified", "case-1", {"people": ["ana"], "message": "Hi"}, "ana", ctx) is None
    assert rules.notes_for("case.note_added", "case-1", {}, "x", ctx) is None


def test_a_person_reads_and_clears_only_their_own_over_http(client):
    me = client.get("/api/notifications", headers=h("meridian", "maya")).json()
    assert set(me) == {"unread", "items"}
    mine = [n["id"] for n in me["items"]]
    others = [n["id"] for n in client.get("/api/notifications", headers=h("meridian", "ivan")).json()["items"]]
    if others:
        assert client.post("/api/notifications/read", json={"ids": others[:1]}, headers=h("meridian", "maya")).json()["marked"] == 0
    out = client.post("/api/notifications/read", json={}, headers=h("meridian", "maya")).json()
    assert out["marked"] == me["unread"] and out["unread"] == 0 and [n["id"] for n in out["items"]] == mine
