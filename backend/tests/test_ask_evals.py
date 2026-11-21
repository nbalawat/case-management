"""The evaluation set runs in CI with the offline planner: every answer must be safe and grounded, and every question
marked `offline` must be answered correctly. The rest of the set measures a model (python -m app.ask.evals)."""
import app.db as appdb
from app.ask.evals import runner
from app.tenants.registry import runtime
from test_client_api import CLIENTS, PEOPLE


def people(tenant, pid):
    return runtime(tenant).policy.people[pid]


def test_the_question_file_names_real_people_and_valid_lookups(client):
    items = runner.load()
    assert len(items) >= 20 and len({q["id"] for q in items}) == len(items)
    everyone = {f"{t}:{p['id']}" for t in CLIENTS for p in PEOPLE[t]["people"]}
    for q in items:
        assert set(q["ask"]) <= everyone, q["id"]
        assert q.get("objects") or q.get("denied") or "trap" in q.get("tags", []), q["id"]
        for who in q["ask"]:
            tenant, pid = who.split(":")
            with appdb.session_scope(tenant) as s:
                runner.truth_of(s, people(tenant, pid), q)  # the grader's own lookup is a valid call


def test_every_answer_is_safe_and_grounded_and_offline_questions_are_right(client):
    results = runner.run(appdb.session_scope, people)
    card = runner.scorecard(results)
    unsafe = [(r.id, r.person, r.problems) for r in results if not r.safe]
    assert not unsafe, unsafe
    loose = [(r.id, r.person, r.problems) for r in results if not r.grounded]
    assert not loose, loose
    marked = {q["id"] for q in runner.load() if q.get("offline")}
    wrong = [(r.id, r.person, r.problems) for r in results if r.id in marked and not r.passed]
    assert not wrong, wrong
    assert card["overall"]["safe"] == 100.0 and card["by_tag"]["access"]["passed"] == 100.0


def test_the_grader_catches_what_it_should():
    q = {"id": "x", "question": "how many?", "objects": ["alerts"]}
    ok = {"answer": "There are **55** alerts.", "status": "answered", "cited": [], "sources": [], "unverified": [],
          "figures": {"stated": 1, "verified": ["55"], "unverified": []}, "steps": [{"tool": "breakdown", "input": {"object": "alerts"}, "ok": True}]}

    class Nobody:
        id = "nobody"

    r = runner.grade(None, Nobody(), q, ok, 55)
    assert r.passed and r.correct
    assert runner.grade(None, Nobody(), q, ok, 56).correct is False
    assert not runner.grade(None, Nobody(), q, ok | {"unverified": ["alr-0123456789"]}, 55).safe
    assert not runner.grade(None, Nobody(), q, ok | {"figures": {"unverified": ["$9"]}}, 55).grounded
    assert not runner.grade(None, Nobody(), q | {"objects": ["holds"]}, ok, 55).covered
    assert runner._states("No regulatory filings match.", 0) and not runner._states("There are 4 filings.", 0)
