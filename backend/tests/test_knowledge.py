"""The knowledge library: parsing, search, each client's own documents, the policy_search tool, citations of
passages, and the library on the API, in Ask and over MCP.

A client's own documents are the access question here: they must be searched, cited and served only for that client.
"""
import json
import shutil

import pytest
from sqlalchemy import select

import app.db as appdb
from app import knowledge
from app.ask import citations, service, tools
from app.domain import models as m
from app.knowledge import corpus
from app.tenants.registry import runtime
from app.visibility import bind_viewer
from test_client_api import CLIENTS, EVERYONE, h

KINDS = {"typology", "policy", "procedure", "regulation"}
OWN = """---
title: Meridian cash policy
kind: policy
scenarios: [structuring]
source: Meridian compliance manual 4.2
---
## Cash thresholds
Meridian branches escalate any customer who makes three or more cash deposits of 7,500 dollars or more in a week.

## Sign-off
Branch managers sign off every quokka exception in writing.
"""
OVERRIDE = """---
title: Structuring at Meridian
kind: typology
scenarios: [structuring]
---
## Indicators
Meridian's own list: deposits just under the branch limit of 5,000 dollars, made by the same wombat courier.
"""


def person(tenant, who):
    return runtime(tenant).policy.people[who]


@pytest.fixture
def own_docs(tmp_path, monkeypatch):
    """A library where the first demo client has one document of its own and its own version of another."""
    shutil.copytree(corpus.BASE / "library", tmp_path / "library")
    mine = tmp_path / "clients" / CLIENTS[0]
    mine.mkdir(parents=True)
    (mine / "meridian-cash.md").write_text(OWN)
    (mine / "structuring.md").write_text(OVERRIDE)
    monkeypatch.setattr(corpus, "BASE", tmp_path)
    corpus.reload()
    yield CLIENTS[0], next(c for c in CLIENTS if c != CLIENTS[0])
    corpus.reload()


# --- parsing --------------------------------------------------------------------------------------------------------
def test_every_library_document_parses_into_cited_passages():
    ps = knowledge.documents(None)
    assert len({p.document for p in ps}) >= 8
    assert len({p.id for p in ps}) == len(ps), "passage ids are unique"
    for p in ps:
        assert p.id == f"kb:{p.document}#{corpus._slug(p.section)}" and p.kind in KINDS and p.text and p.title and p.client is None
        assert knowledge.passage(None, p.id) == p


@pytest.mark.parametrize("name,raw,error", [
    ("Bad Name", "---\ntitle: x\nkind: policy\n---\n## A\ntext", "lower-case"),
    ("no-header", "## A\ntext", "--- header"),
    ("no-title", "---\nkind: policy\n---\n## A\ntext", "'title'"),
    ("no-kind", "---\ntitle: x\n---\n## A\ntext", "'kind'"),
    ("no-sections", "---\ntitle: x\nkind: policy\n---\nJust prose, no headings.", "no '## ' sections"),
])
def test_a_malformed_document_is_refused_with_the_reason(name, raw, error):
    with pytest.raises(ValueError, match=error):
        corpus.parse(name, raw)


def test_empty_sections_are_dropped_and_headings_become_ids():
    ps = corpus.parse("doc", "---\ntitle: T\nkind: procedure\n---\nIntro is ignored.\n## First step!\nDo it.\n## Empty\n\n## Last\nDone.")
    assert [p.id for p in ps] == ["kb:doc#first-step", "kb:doc#last"] and ps[0].text == "Do it."
    assert knowledge.link("kb:doc#first-step") == "/knowledge/doc#first-step"


# --- search ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("query,best", [
    ("What are the signs of structuring?", "kb:structuring#indicators"),
    ("cash deposits just under 10,000", "kb:structuring"),
    ("When must we file a SAR?", "kb:sar-decisioning#deadlines"),
    ("tipping off", "kb:sar-decisioning#confidentiality-no-tipping-off"),
    ("red flags for account takeover", "kb:account-takeover#indicators"),
    ("What does policy say about PEPs?", "kb:kyc-review"),
    ("high risk countries", "kb:high-risk-geography"),
    ("sanctions name match true match", "kb:sanctions-name-match"),
    ("funds moved in and out quickly", "kb:rapid-movement"),
])
def test_search_puts_the_passage_that_answers_first(query, best):
    hits = knowledge.search(None, query)
    assert hits and hits[0]["id"].startswith(best), [x["id"] for x in hits]
    assert all(a["score"] >= b["score"] for a, b in zip(hits, hits[1:]))


def test_search_is_the_same_every_time_and_filters_by_kind_and_scenario():
    a, b = knowledge.search(None, "review the account"), knowledge.search(None, "review the account")
    assert a == b
    assert {x["kind"] for x in knowledge.search(None, "review", kind="policy", limit=10)} <= {"policy"}
    assert all("structuring" in knowledge.passage(None, x["id"]).scenarios for x in knowledge.search(None, "cash", scenario="structuring"))
    assert knowledge.search(None, "zzzz qqqq") == [] and knowledge.search(None, "the of and") == []
    assert len(knowledge.search(None, "customer", limit=50)) == 10


def test_a_clients_own_documents_are_searched_only_for_that_client(own_docs):
    mine, other = own_docs
    hit = knowledge.search(mine, "quokka exception")
    assert hit[0]["id"] == "kb:meridian-cash#sign-off" and hit[0]["client_document"]
    assert knowledge.search(other, "quokka exception") == [] and knowledge.search(None, "quokka") == []
    assert knowledge.passage(other, "kb:meridian-cash#sign-off") is None
    # The client's own version replaces the library's, for that client only.
    assert knowledge.search(mine, "wombat courier")[0]["id"] == "kb:structuring#indicators"
    assert [p.section for p in knowledge.documents(mine) if p.document == "structuring"] == ["Indicators"]
    assert knowledge.search(other, "wombat") == []
    assert len([p for p in knowledge.documents(other) if p.document == "structuring"]) > 1


# --- the tool and citations -----------------------------------------------------------------------------------------
def _run(tenant, who, name, args):
    p = person(tenant, who)
    with appdb.session_scope(tenant) as s:
        return tools.run(tools.ToolContext(bind_viewer(s, p), p), name, args)


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_everyone_may_search_the_library(client, tenant, who):
    assert "policy_search" in {t.name for t in tools.available(person(tenant, who))}
    r = _run(tenant, who, "policy_search", {"query": "signs of structuring", "limit": 3})
    assert not r.error and r.data["passages"][0]["id"] == "kb:structuring#indicators"
    assert [x["id"] for x in r.records] == [x["id"] for x in r.data["passages"]]
    assert all(x["kind"] == "policy" and x["link"].startswith("/knowledge/") for x in r.records)


def test_bad_searches_come_back_as_errors(client):
    tenant, who = EVERYONE[0]
    assert _run(tenant, who, "policy_search", {"query": "ab"}).error
    assert "kind must be" in _run(tenant, who, "policy_search", {"query": "cash", "kind": "gossip"}).summary
    assert _run(tenant, who, "policy_search", {"query": "cash", "secret": 1}).error


def test_a_cited_passage_becomes_a_source_and_a_made_up_one_is_flagged(client, own_docs):
    mine, other = own_docs
    who = next(p for t, p in EVERYONE if t == mine)
    elsewhere = next(p for t, p in EVERYONE if t == other)
    answer = "Escalate it (kb:meridian-cash#cash-thresholds), see also kb:structuring#indicators and kb:made-up#nothing."
    with appdb.session_scope(mine) as s:
        sources, unverified = citations.resolve(s, person(mine, who), answer, {})
    assert {x["id"] for x in sources} == {"kb:meridian-cash#cash-thresholds", "kb:structuring#indicators"}
    assert unverified == ["kb:made-up#nothing"]
    own = next(x for x in sources if x["id"] == "kb:meridian-cash#cash-thresholds")
    assert own["client_document"] and own["label"] == "Meridian cash policy: Cash thresholds" and own["link"] == "/knowledge/meridian-cash#cash-thresholds"
    with appdb.session_scope(other) as s:  # the same answer at another client: that client has no such document
        sources, unverified = citations.resolve(s, person(other, elsewhere), answer, {})
    assert "kb:meridian-cash#cash-thresholds" in unverified and "kb:structuring#indicators" in {x["id"] for x in sources}


@pytest.mark.parametrize("tenant,who", EVERYONE[:3], ids=[f"{t}:{p}" for t, p in EVERYONE[:3]])
def test_ask_answers_a_policy_question_from_the_library_with_its_source(client, tenant, who):
    p = person(tenant, who)
    with appdb.session_scope(tenant) as s:
        bind_viewer(s, p)
        out = service.ask(s, p, "What are the signs of structuring?")
    assert out["status"] == "answered" and out["steps"][0]["tool"] == "policy_search"
    assert "kb:structuring#indicators" in out["answer"]
    src = next(x for x in out["sources"] if x["id"] == "kb:structuring#indicators")
    assert src["kind"] == "policy" and src["shown_by_tool"] and src["excerpt"]
    assert out["figures"]["unverified"] == [] and out["unverified"] == []
    with appdb.session_scope(tenant) as s:
        row = s.scalars(select(m.AskLog).order_by(m.AskLog.created.desc())).first()
    assert "kb:structuring#indicators" in row.cited


def test_questions_about_the_records_still_go_to_the_records(client):
    tenant, who = EVERYONE[0]
    p = person(tenant, who)
    with appdb.session_scope(tenant) as s:
        bind_viewer(s, p)
        out = service.ask(s, p, "How many structuring alerts are there?")
    assert out["steps"][0]["tool"] != "policy_search"


# --- the API and MCP ------------------------------------------------------------------------------------------------
def test_the_library_on_the_api_shows_each_client_its_own_documents(client, own_docs):
    mine, other = own_docs
    me = next(p for t, p in EVERYONE if t == mine)
    them = next(p for t, p in EVERYONE if t == other)
    docs = {d["document"]: d for d in client.get("/api/knowledge", headers=h(mine, me)).json()}
    assert docs["meridian-cash"]["client_document"] and docs["structuring"]["client_document"]
    theirs = {d["document"]: d for d in client.get("/api/knowledge", headers=h(other, them)).json()}
    assert "meridian-cash" not in theirs and not theirs["structuring"]["client_document"]
    doc = client.get("/api/knowledge/meridian-cash", headers=h(mine, me)).json()
    assert [x["section"] for x in doc["sections"]] == ["Cash thresholds", "Sign-off"] and "7,500" in doc["sections"][0]["text"]
    assert client.get("/api/knowledge/meridian-cash", headers=h(other, them)).status_code == 404
    found = client.get("/api/knowledge/search", params={"q": "quokka"}, headers=h(mine, me)).json()["passages"]
    assert found[0]["id"] == "kb:meridian-cash#sign-off" and found[0]["link"] == "/knowledge/meridian-cash#sign-off"
    assert client.get("/api/knowledge/search", params={"q": "quokka"}, headers=h(other, them)).json()["passages"] == []
    assert client.get("/api/knowledge/search", params={"q": "x"}, headers=h(mine, me)).status_code == 422
    assert client.get("/api/knowledge", headers=h(mine, "nobody-here")).status_code in (401, 403)


def test_policy_search_over_mcp_runs_at_the_callers_client(client, own_docs):
    from test_mcp import call

    mine, other = own_docs
    me = next(p for t, p in EVERYONE if t == mine)
    them = next(p for t, p in EVERYONE if t == other)
    err, out = call(client, mine, me, "policy_search", {"query": "quokka exception"})
    assert not err and out["passages"][0]["id"] == "kb:meridian-cash#sign-off"
    err, out = call(client, other, them, "policy_search", {"query": "quokka exception"})
    assert not err and out["passages"] == []
    err, text = call(client, mine, me, "policy_search", {"query": "cash", "kind": "gossip"})
    assert err and "kind" in text
    assert json.dumps(out)  # plain data
