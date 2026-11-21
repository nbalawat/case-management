"""Ask across the whole landscape, against the seeded demo: every person at every client, every object.

Each figure the landscape layer returns is checked against an oracle written here in plain Python over the raw
rows, following the rule in app/ask/landscape/scope.py. The cases a person may see come from the case list over
HTTP, so the oracle does not share code with what it checks. Every check runs twice: with a session bound to the
person (as the API opens it) and with an unbound one, because the scope rule must hold either way.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

import app.db as appdb
from app.ask import tools
from app.ask.landscape import catalog, query
from app.domain import models as m
from app.tenants.registry import runtime
from app.visibility import bind_viewer, viewer_of
from test_client_api import CLIENTS, EVERYONE, h

OBJECTS = list(catalog.objects())
PERMS = {"filings": ("submit_filings", "view_team"), "agent_actions": ("view_team",), "pipeline_runs": ("load_data", "view_team")}


def person(tenant, who):
    return runtime(tenant).policy.people[who]


@pytest.fixture(scope="module")
def raw(client):
    """Every row of every object at every client, read without any person's view."""
    out = {}
    for t in CLIENTS:
        with appdb.session_scope(t) as s:
            cases = {c.id: c for c in s.scalars(select(m.Case))}
            out[t] = {"cases": cases, "parties": {p.id for p in s.scalars(select(m.Party))},
                      "rows": {oid: list(s.scalars(select(o.entity))) for oid, o in catalog.objects().items()}}
            s.expunge_all()
    return out


@pytest.fixture(scope="module")
def visible(client):
    """(tenant, person) -> the case ids the case list shows them."""
    return {(t, p): {c["id"] for c in client.get("/api/cases?status=all", headers=h(t, p)).json()} for t, p in EVERYONE}


def _rid(oid, row):
    return str(row.id)


def expected(raw_t, oid, cases_seen: set[str], asker) -> set[str]:
    """The ids of `oid` records this person may see, by the rule in scope.py, computed from raw rows."""
    viewer = viewer_of(asker)
    if viewer.views is None:
        parties = raw_t["parties"]
    else:
        parties = {raw_t["cases"][c].party_id for c in cases_seen} | {
            a.party_id for a in raw_t["rows"]["alerts"] if a.case_id is None or a.case_id in cases_seen}
    out = set()
    for r in raw_t["rows"][oid]:
        case_id = getattr(r, "case_id", None)
        if oid in ("detection_changes", "lookbacks", "pipeline_runs"):
            ok = True
        elif oid == "customers":
            ok = r.id in parties
        elif oid in ("transactions", "risk_assessments"):
            ok = r.party_id in parties
        elif oid in ("holds", "disputes", "agent_runs"):
            ok = case_id in cases_seen
        elif oid == "agent_actions":
            ok = case_id in cases_seen if case_id else viewer.unlimited
        else:  # alerts, filings, kyc_triggers: the case decides, or with no case the customer does
            ok = case_id in cases_seen if case_id else r.party_id in parties
        if ok:
            out.add(_rid(oid, r))
    return out


def sessions(tenant, asker):
    """The two ways a session reaches the landscape layer."""
    for bound in (True, False):
        with appdb.session_scope(tenant) as s:
            yield (bind_viewer(s, asker) if bound else s), bound


# --- the oracle itself has something to check -------------------------------------------------------------
def test_the_demo_has_every_object_and_people_with_different_views(raw, visible):
    empty = [oid for oid in OBJECTS if not any(raw[t]["rows"][oid] for t in CLIENTS) and oid not in ("lookbacks", "detection_changes", "pipeline_runs")]
    assert not empty, f"no client has any {empty}: the checks below would prove nothing for them"
    sizes = {len(v) for v in visible.values()}
    assert len(sizes) > 2, "every person sees the same cases: the visibility checks below would prove nothing"
    assert any(viewer_of(person(t, p)).views is not None for t, p in EVERYONE), "nobody has limited views"
    assert any(not viewer_of(person(t, p)).sees_restricted for t, p in EVERYONE)


# --- every person, every object: the count, the examples and single reads -------------------------------------
@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_each_person_sees_exactly_the_records_the_rule_allows(raw, visible, tenant, who):
    asker = person(tenant, who)
    seen = visible[(tenant, who)]
    others = [t for t in CLIENTS if t != tenant]
    for s, bound in sessions(tenant, asker):
        for oid, obj in catalog.objects().items():
            if obj.permission(asker) is not None:
                continue
            want = expected(raw[tenant], oid, seen, asker)
            out = query.breakdown(s, asker, obj)
            assert out["matching"] == len(want), f"{who}@{tenant} {oid} (bound={bound})"
            assert out["rows"][0]["records"] == len(want)
            if obj.dims:  # every example id is one they may see
                assert {i for r in out["rows"] for i in r["ids"]} <= want, f"{who}@{tenant} {oid}"
            listed = query.list_records(s, asker, obj, limit=query.MAX_LIST)
            assert listed["matching"] == len(want) and {str(r[obj.id_dim]) for r in listed["records"]} <= want
            everyone = {_rid(oid, r) for r in raw[tenant]["rows"][oid]}
            for rid in sorted(want)[:3]:
                assert query.get_one(s, asker, obj, rid) is not None, f"{who}@{tenant} cannot open {oid} {rid}"
            for rid in sorted(everyone - want)[:3]:
                assert query.get_one(s, asker, obj, rid) is None, f"{who}@{tenant} opened {oid} {rid}"
            for o in others:  # another client's ids are nothing here (unless the client's own data uses the same id)
                for r in raw[o]["rows"][oid][:3]:
                    rid = _rid(oid, r)
                    if rid not in everyone:
                        assert query.get_one(s, asker, obj, rid) is None, f"{who}@{tenant} opened {o}'s {oid} {rid}"


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_figures_from_cases_count_only_cases_the_person_can_see(raw, visible, tenant, who):
    """A customer's open cases and 'has a SAR' come from cases; a restricted case must never show through them."""
    asker = person(tenant, who)
    seen = visible[(tenant, who)]
    obj = catalog.get("customers")
    cases = [c for c in raw[tenant]["cases"].values() if c.id in seen]
    want = expected(raw[tenant], "customers", seen, asker)
    with appdb.session_scope(tenant) as s:
        bind_viewer(s, asker)
        for pid in sorted(want)[:25]:
            row = query.get_one(s, asker, obj, pid)
            assert row["customer_open_cases"] == sum(1 for c in cases if c.party_id == pid and c.closed is None), (who, pid)
            assert (row["customer_has_sar"] in (True, "yes")) == any(c.party_id == pid and c.disposition == "sar_filed" for c in cases), (who, pid)
        # the same, summed across all of them in one breakdown
        flagged = query.breakdown(s, asker, obj, filters=[{"field": "customer_has_sar", "op": "=", "value": True}])["matching"]
        assert flagged == len({c.party_id for c in cases if c.disposition == "sar_filed"} & want)


def test_somebody_misses_a_case_figure_that_the_auditor_sees(raw, visible):
    """The check above only means something if some customer's figures differ by who asks."""
    differs = False
    for t in CLIENTS:
        every = {c.id for c in raw[t]["cases"].values()}
        for (tt, p), seen in visible.items():
            if tt == t and seen != every:
                hidden = every - seen
                if any(raw[t]["cases"][c].party_id in expected(raw[t], "customers", seen, person(t, p)) for c in hidden):
                    differs = True
    assert differs


# --- group totals add up, and amounts match the rows ------------------------------------------------------------
@pytest.mark.parametrize("tenant", CLIENTS)
def test_groups_add_up_and_amounts_match_the_raw_rows(raw, tenant):
    asker = person(tenant, next(p for t, p in EVERYONE if t == tenant and viewer_of(person(t, p)).unlimited))
    tx = raw[tenant]["rows"]["transactions"]
    obj = catalog.get("transactions")
    with appdb.session_scope(tenant) as s:
        bind_viewer(s, asker)
        out = query.breakdown(s, asker, obj, group_by=["channel"], measures=["transactions", "total_amount", "max_amount"])
        by = {r["key"][0]: r["values"] for r in out["rows"]}
        for ch in {t.channel for t in tx}:
            rows = [t for t in tx if t.channel == ch]
            n, total, top = by[ch]
            assert n == len(rows) and abs(total - sum(float(t.amount) for t in rows)) < 0.01 and abs(top - max(float(t.amount) for t in rows)) < 0.01
        assert sum(r["records"] for r in out["rows"]) == out["matching"] == len(tx)
        big = query.breakdown(s, asker, obj, filters=[{"field": "amount", "op": ">", "value": 10000}])["matching"]
        assert big == sum(1 for t in tx if float(t.amount) > 10000)
        # a filter on a joined table (the customer) must join it, not multiply every row by every customer
        high = {p.id for p in s.scalars(select(m.Party).where(m.Party.risk_rating == "high"))}
        want = [t for t in tx if t.party_id in high]
        f = [{"field": "customer_risk_rating", "op": "=", "value": "high"}]
        out = query.breakdown(s, asker, obj, measures=["transactions", "total_amount"], filters=f)
        assert out["matching"] == out["rows"][0]["values"][0] == len(want) > 0
        assert abs(out["rows"][0]["values"][1] - sum(float(t.amount) for t in want)) < 0.01
        assert query.list_records(s, asker, obj, filters=f)["matching"] == len(want)
        assert all(r["customer_risk_rating"] == "high" for r in query.list_records(s, asker, obj, filters=f, columns=["customer_risk_rating"])["records"])


# --- who may read what --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_objects_needing_a_permission_are_refused_and_never_described(client, tenant, who):
    asker = person(tenant, who)
    text = tools.catalog_text(asker)
    with appdb.session_scope(tenant) as s:
        ctx = tools.ToolContext(bind_viewer(s, asker), asker)
        for oid, perms in PERMS.items():
            may = any(asker.can(x) for x in perms)
            for tool in ("breakdown", "list_records"):
                res = tools.run(ctx, tool, {"object": oid})
                assert res.error != may, f"{who}@{tenant} {tool} {oid}: {res.summary}"
                if not may:
                    assert "permission" in res.summary and not res.records
            assert (f"\n{oid} (" in text) == may
        for oid in set(OBJECTS) - set(PERMS):
            assert not tools.run(ctx, "list_records", {"object": oid}).error


def test_an_unknown_object_or_field_is_an_error_the_model_can_read(client):
    tenant, who = EVERYONE[0]
    asker = person(tenant, who)
    with appdb.session_scope(tenant) as s:
        ctx = tools.ToolContext(bind_viewer(s, asker), asker)
        assert "There is no object" in tools.run(ctx, "breakdown", {"object": "payroll"}).summary
        assert tools.run(ctx, "breakdown", {"object": "transactions", "group_by": ["password"]}).error
        assert tools.run(ctx, "list_records", {"object": "transactions", "filters": [{"field": "nope", "op": "=", "value": 1}]}).error
        assert tools.run(ctx, "record", {"object": "alerts", "id": "alr-0000000000"}).error


# --- over HTTP: questions about other objects, with sources ---------------------------------------------------------
LANDSCAPE_QUESTIONS = ["How many transactions over $10,000 by channel?", "How many alerts by status?", "Show the active holds",
                       "How many disputes by rail?", "How many customers by risk rating?"]


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_landscape_questions_answer_with_sources_the_person_may_open(client, raw, visible, tenant, who):
    asker = person(tenant, who)
    seen = visible[(tenant, who)]
    for q in LANDSCAPE_QUESTIONS:
        body = client.post("/api/ask", headers=h(tenant, who), json={"question": q}).json()
        assert body["status"] == "answered", (q, body)
        step = body["steps"][0]
        oid = step["input"].get("object")
        assert oid and oid != "cases", (q, step["input"])
        want = expected(raw[tenant], oid, seen, asker)
        assert step["result"]["matching"] <= len(want)
        for src in body["sources"]:
            assert src["id"] in want or src["kind"] != catalog.get(oid).kind, (q, src)
            assert src["link"].startswith("/")
        assert body["figures"]["unverified"] == [], (q, body["answer"], body["figures"])


def test_the_stream_sends_each_step_then_the_whole_answer(client):
    tenant, who = EVERYONE[0]
    with client.stream("POST", "/api/ask/stream", headers=h(tenant, who), json={"question": "How many alerts by status?"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    kinds = [line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("event: ")]
    assert kinds[0] == "started" and kinds[-1] == "done" and "step" in kinds
    import json
    done = json.loads([line for line in text.splitlines() if line.startswith("data: ")][-1][6:])
    plain = client.post("/api/ask", headers=h(tenant, who), json={"question": "How many alerts by status?"}).json()
    assert done["answer"] == plain["answer"] and done["status"] == "answered"


def test_a_bad_question_is_refused_before_the_stream_opens(client):
    tenant, who = EVERYONE[0]
    assert client.post("/api/ask/stream", headers=h(tenant, who), json={"question": "?"}).status_code == 422
    assert client.post("/api/ask/stream", headers=h(tenant, "nobody-here"), json={"question": "How many alerts?"}).status_code == 403


def test_a_join_to_the_case_table_under_another_name_keeps_the_persons_filter(visible):
    """The viewer's case filter must follow aliases of the case table, or a join escapes it (or fails outright)."""
    from sqlalchemy.orm import aliased
    tenant, who = next((t, p) for t, p in EVERYONE if viewer_of(person(t, p)).views is not None)
    c = aliased(m.Case)
    with appdb.session_scope(tenant) as s:
        bind_viewer(s, person(tenant, who))
        joined = set(s.scalars(select(c.id).select_from(m.Alert).join(c, c.id == m.Alert.case_id)))
    assert joined and joined <= visible[(tenant, who)]
