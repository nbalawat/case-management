"""The MCP server: Ask's governed tools for MCP clients, over HTTP at /mcp and over stdio.

Every call runs as the person the request signs in as, at their own client, with their visibility and permissions.
Checked for every person at every demo client, against the landscape layer read directly as that same person.
"""
import asyncio
import json

import pytest
from sqlalchemy import func, select

import app.db as appdb
from app.ask.landscape import catalog, query
from app.domain import models as m
from app.mcp.server import build, fixed
from app.tenants.registry import runtime
from app.visibility import bind_viewer
from test_client_api import CLIENTS, EVERYONE, h

URL = "http://localhost/mcp"
TOOLS = {"catalog", "breakdown", "list_records", "record", "calculate", "policy_search", "ask"}


def person(tenant, who):
    return runtime(tenant).policy.people[who]


def rpc(client, tenant, who, method, params=None, *, headers=None, url=URL):
    hdrs = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"} | h(tenant, who) | (headers or {})
    return client.post(url, headers=hdrs, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def call(client, tenant, who, tool, args=None):
    """(is_error, data) for one tool call over HTTP."""
    r = rpc(client, tenant, who, "tools/call", {"name": tool, "arguments": args or {}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    text = res["content"][0]["text"]
    return bool(res.get("isError")), (text if res.get("isError") else json.loads(text))


def test_the_server_answers_mcp_over_http_with_or_without_a_trailing_slash(client):
    tenant, who = EVERYONE[0]
    for url in (URL, URL + "/"):
        r = rpc(client, tenant, who, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                    "clientInfo": {"name": "test", "version": "1"}}, url=url)
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "caseforge"
    tools = {t["name"]: t for t in rpc(client, tenant, who, "tools/list").json()["result"]["tools"]}
    assert set(tools) == TOOLS
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools.values())


def test_nobody_gets_past_the_door(client):
    tenant, who = EVERYONE[0]
    assert rpc(client, tenant, "nobody-here", "tools/list").status_code == 403
    assert rpc(client, "no-such-client", who, "tools/list").status_code == 404
    other = next(t for t in CLIENTS if t != tenant)
    assert rpc(client, other, who, "tools/list").status_code == 403  # a person exists only at their own client


def test_only_this_hosts_names_are_accepted(client):
    tenant, who = EVERYONE[0]
    r = rpc(client, tenant, who, "tools/list", url="http://evil.example/mcp")
    assert r.status_code == 421


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_each_person_gets_their_own_catalog_and_figures(client, tenant, who):
    p = person(tenant, who)
    err, cat = call(client, tenant, who, "catalog")
    assert not err and cat["client"] == tenant and cat["person"] == p.name
    readable = {o.id for o in catalog.available(p)}
    assert set(cat["objects"]) == {"cases"} | readable
    with appdb.session_scope(tenant) as s:
        bind_viewer(s, p)
        for oid in sorted(readable):
            err, out = call(client, tenant, who, "breakdown", {"object": oid})
            assert not err, out
            assert out["matching"] == query.breakdown(s, p, catalog.get(oid))["matching"], (who, oid)
        cases = s.scalar(select(func.count()).select_from(m.Case))
    err, out = call(client, tenant, who, "breakdown", {})
    assert not err and out["matching_cases"] == cases
    for oid in set(catalog.objects()) - readable:  # refused, with the reason, and nothing shown
        err, text = call(client, tenant, who, "list_records", {"object": oid})
        assert err and "permission" in text


def test_records_from_another_client_or_out_of_view_are_not_found(client):
    limited = next((t, p) for t, p in EVERYONE if person(t, p).views is not None)
    tenant, who = limited
    other = next(t for t in CLIENTS if t != tenant)
    with appdb.session_scope(other) as s:
        foreign = s.scalars(select(m.Hold.id)).first() or s.scalars(select(m.Alert.id)).first()
    kind = "holds" if foreign.startswith("hld-") else "alerts"
    err, text = call(client, tenant, who, "record", {"object": kind, "id": foreign})
    assert err and "No " in text
    with appdb.session_scope(tenant) as s:
        hidden = s.scalars(select(m.Case.id)).all()
    seen = {c["id"] for c in client.get("/api/cases?status=all", headers=h(tenant, who)).json()}
    out_of_view = next(c for c in hidden if c not in seen)
    err, text = call(client, tenant, who, "record", {"object": "cases", "id": out_of_view})
    assert err


def test_every_call_runs_as_its_own_request_not_the_previous_one(client):
    """Stateless: two people calling the same server in turn each get their own view."""
    tenant = CLIENTS[0]
    wide = next(p for t, p in EVERYONE if t == tenant and person(t, p).views is None and person(t, p).can("view_restricted"))
    narrow = next(p for t, p in EVERYONE if t == tenant and person(t, p).views is not None)
    a = call(client, tenant, wide, "breakdown", {})[1]["matching_cases"]
    b = call(client, tenant, narrow, "breakdown", {})[1]["matching_cases"]
    c = call(client, tenant, wide, "breakdown", {})[1]["matching_cases"]
    assert a == c > b


def test_bad_arguments_come_back_as_errors_the_model_can_read(client):
    tenant, who = EVERYONE[0]
    err, text = call(client, tenant, who, "breakdown", {"object": "transactions", "group_by": ["password"]})
    assert err and "Cannot group" in text
    err, text = call(client, tenant, who, "calculate", {"expression": "__import__('os')"})
    assert err
    err, out = call(client, tenant, who, "calculate", {"expression": "round(2/3, 2)"})
    assert not err and out["value"] == 0.67


def test_ask_answers_with_sources_and_is_recorded_as_the_person(client):
    tenant, who = next((t, p) for t, p in EVERYONE if person(t, p).can("view_team"))
    err, out = call(client, tenant, who, "ask", {"question": "Show the active holds"})
    assert not err and out["status"] == "answered" and out["sources"] and out["figures"]["unverified"] == []
    assert out["lookups"][0]["tool"] == "list_records"
    with appdb.session_scope(tenant) as s:
        row = s.scalars(select(m.AskLog).order_by(m.AskLog.created.desc())).first()
    assert row.question == "Show the active holds" and row.asked_by == who
    err, text = call(client, tenant, who, "ask", {"question": "?"})
    assert err


def test_the_stdio_server_runs_as_the_person_it_was_started_for(client):
    """The same tools in-process, as the stdio entry point builds them: one fixed person."""
    from mcp import Client

    tenant, who = next((t, p) for t, p in EVERYONE if person(t, p).views is not None)
    p = person(tenant, who)

    async def go():
        async with Client(build(fixed(tenant, p))) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            res = await c.call_tool("breakdown", {"object": "alerts"})
            return names, json.loads(res.content[0].text)

    names, out = asyncio.run(go())
    assert names == TOOLS
    with appdb.session_scope(tenant) as s:
        bind_viewer(s, p)
        assert out["matching"] == query.breakdown(s, p, catalog.get("alerts"))["matching"]


def test_provider_staff_reach_a_client_over_mcp_only_while_engaged_and_the_client_sees_each_request(client):
    """The managed service's rules hold at /mcp as on every other route."""
    err, out = call(client, "kestrel", "ms.priya", "breakdown", {})
    assert not err and out["matching_cases"] >= 1
    for other in ("meridian", "alder"):
        assert rpc(client, other, "ms.priya", "tools/list").status_code == 403
    view = client.get("/api/access/managed-service", headers=h("kestrel", "pat")).json()
    assert any(x["actor"] == "ms.priya" and x["subject"] == "POST /mcp" for x in view["log"])
