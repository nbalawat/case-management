"""The model gateway: endpoints from configuration, each client's model policy, residency, fallbacks, spend limits,
the usage meter, and the open-weight adapter. No network: hosted calls are stand-ins, open-weight calls go to an
in-process HTTP transport.
"""
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.ai import endpoints as eps
from app.ai import gateway, policy, usage
from app.ai.providers.openai_compat import OpenAICompatibleChatModel
from app.ai.types import ModelTurn, ModelUnavailable, ToolCall, ToolOutput, ToolSpec
from app.ai.usage import ModelUsage, OverBudget
from app.ask import service
from app.domain import models as m
from app.domain.personas import get_persona
from app.tenancy import TenantError
from ask_support import build_book
from support import T

FILE = """
endpoints:
  hosted-us:   {kind: anthropic, label: Hosted US, model: claude-opus-5-5, api_key_env: GW_TEST_KEY, needs: [GW_TEST_KEY], region: us, price: {input: 4, output: 20}}
  hosted-eu:   {kind: anthropic, label: Hosted EU, model: claude-opus-5-5, api_key_env: GW_TEST_KEY, needs: [GW_TEST_KEY], region: eu}
  in-house:    {kind: openai, label: In-house vLLM, base_url_env: GW_TEST_URL, model: llama-3.3-70b, needs: [GW_TEST_URL], region: local, price: {input: 0, output: 0}}
"""


@pytest.fixture
def conf(tmp_path, monkeypatch):
    """A deployment with three endpoints; the hosted ones have a key, the in-house one an address."""
    path = tmp_path / "endpoints.yaml"
    path.write_text(FILE)
    monkeypatch.setenv("AI_ENDPOINTS", str(path))
    monkeypatch.setenv("GW_TEST_KEY", "k")
    monkeypatch.setenv("GW_TEST_URL", "http://vllm.test/v1")
    monkeypatch.delenv("ASK_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_MODE", raising=False)
    eps.reload()
    yield path
    eps.reload()


class Fake:
    """A model that plays back turns and fails on request, standing in for a provider behind an endpoint."""

    def __init__(self, name, *turns):
        self.name, self.turns, self.opened = name, list(turns), 0

    def start(self, **kw):
        self.opened += 1
        return self

    def _next(self):
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t

    def ask(self, q):
        return self._next()

    def results(self, outputs):
        return self._next()


def answer(text="ok", i=100, o=20, model="claude-opus-5-5"):
    return ModelTurn("answer", text, usage={"input_tokens": i, "output_tokens": o}, model=model)


def plug(monkeypatch, **fakes):
    """Make each named endpoint connect to the given fake instead of a real provider."""
    real = eps.Endpoint.connect

    def connect(self):
        if self.id in fakes:
            if not self.ready:
                raise ModelUnavailable("not configured")
            return fakes[self.id]
        return real(self)
    monkeypatch.setattr(eps.Endpoint, "connect", connect)


def set_policy(db, raw, tenant=T):
    with db.platform() as p:
        policy.set_policy(p, tenant, raw)
        p.commit()


# --- endpoints ---------------------------------------------------------------------
def test_the_shipped_endpoints_file_parses_and_names_no_secret():
    raw = (eps.ROOT / "backend" / "ai" / "endpoints.yaml").read_text()
    found = eps.parse(__import__("yaml").safe_load(raw))
    assert {"claude", "open-weight"} <= set(found)
    assert all(e.needs for e in found.values()), "every shipped endpoint waits for its own configuration"
    assert "sk-" not in raw


def test_endpoints_read_configuration_and_report_what_is_missing(conf, monkeypatch):
    found = eps.endpoints()
    assert list(found) == ["offline", "hosted-us", "hosted-eu", "in-house"]
    assert found["in-house"].base_url == "http://vllm.test/v1" and found["in-house"].ready
    assert found["hosted-us"].cost(1_000_000, 100_000) == 6.0 and found["hosted-eu"].cost(10, 10) is None
    monkeypatch.delenv("GW_TEST_KEY")
    eps.reload()
    assert not eps.endpoints()["hosted-us"].ready and eps.endpoints()["hosted-us"].missing == ["GW_TEST_KEY"]
    with pytest.raises(ModelUnavailable, match="not configured"):
        eps.endpoints()["hosted-us"].connect()


@pytest.mark.parametrize("raw,why", [
    ({}, "expected a mapping"),
    ({"endpoints": {"offline": {"kind": "openai", "base_url": "x"}}}, "built-in"),
    ({"endpoints": {"x": {"kind": "gpt"}}}, "needs a kind"),
    ({"endpoints": {"x": {"kind": "openai"}}}, "base_url"),
    ({"endpoints": {"x": {"kind": "anthropic", "price": {"input": 1}}}}, "price"),
])
def test_bad_endpoint_files_are_refused_with_a_reason(raw, why):
    with pytest.raises(eps.EndpointError, match=why):
        eps.parse(raw)


# --- policy ------------------------------------------------------------------------
@pytest.mark.parametrize("raw,why", [
    ({"ask": "gpt-9"}, "no endpoint 'gpt-9'"),
    ({"agents": "offline"}, "answers questions only"),
    ({"fallback": "hosted-us"}, "list of endpoint ids"),
    ({"fallback": ["nope"]}, "no endpoint 'nope'"),
    ({"regions": "us"}, "list of region names"),
    ({"monthly_tokens": -1}, "whole number"),
    ({"monthly_tokens": True}, "whole number"),
    ({"question_tokens": 1.5}, "whole number"),
    ({"temperature": 1}, "Unknown setting"),
    ({"ask": "hosted-us", "regions": ["eu"]}, "residency rule"),
    ({"ask": "offline", "fallback": ["hosted-us"], "regions": ["local"]}, "residency rule"),
])
def test_policies_are_checked_against_this_server(conf, raw, why):
    with pytest.raises(policy.PolicyError, match=why):
        policy.parse(raw)


def test_a_valid_policy_round_trips(conf):
    pol = policy.parse({"ask": "hosted-eu", "agents": "in-house", "fallback": ["in-house", "in-house"], "regions": ["EU", "local"],
                        "monthly_tokens": 1000, "question_tokens": 50})
    assert pol.fallback == ("in-house",) and pol.regions == ("eu", "local")
    assert policy.parse(pol.as_dict()) == pol


def test_only_platform_staff_can_set_a_policy_and_it_lands_in_one_client(db, conf):
    db.onboard(T)
    db.onboard("kestrel")
    with db.tenant(T) as s, pytest.raises(TenantError):
        policy.set_policy(s, T, {"ask": "offline"})
    set_policy(db, {"ask": "in-house", "monthly_tokens": 500})
    with db.tenant(T) as s:
        assert policy.for_session(s).ask == "in-house"
    with db.tenant("kestrel") as s:
        assert policy.for_session(s) == policy.ModelPolicy()
    set_policy(db, None)  # back to the deployment default
    with db.tenant(T) as s:
        assert policy.for_session(s) == policy.ModelPolicy()


# --- resolution: what serves each purpose ------------------------------------------
def test_auto_means_the_first_configured_endpoint_for_questions_and_rules_for_agents(conf, monkeypatch):
    pol = policy.ModelPolicy()
    assert gateway.resolve(pol, "ask").endpoint.id == "hosted-us"
    assert gateway.resolve(pol, "agents").endpoint is None  # agents use models only when asked to
    monkeypatch.setenv("AGENT_MODE", "model")
    assert gateway.resolve(pol, "agents").endpoint.id == "hosted-us"
    monkeypatch.setenv("ASK_PROVIDER", "offline")
    assert gateway.resolve(pol, "ask").endpoint.id == "offline"
    monkeypatch.delenv("ASK_PROVIDER")
    monkeypatch.delenv("GW_TEST_KEY")
    monkeypatch.delenv("GW_TEST_URL")
    assert gateway.resolve(pol, "ask").endpoint.id == "offline"
    assert gateway.resolve(pol, "agents").endpoint is None


def test_residency_is_never_bypassed_by_auto_or_by_a_fallback(conf):
    pol = policy.ModelPolicy(regions=("eu",))  # the deployment default, hosted-us, is outside it
    res = gateway.resolve(pol, "ask")
    assert res.endpoint.id == "offline" and "outside this client's residency rule" in res.reason
    pol = policy.ModelPolicy(ask="hosted-eu", fallback=("hosted-us", "in-house"), regions=("eu",))
    assert gateway.resolve(pol, "ask").endpoint.id == "hosted-eu"
    stored = policy.of({"ai": {"ask": "hosted-us", "regions": ["eu"]}})  # saved before a rule tightened: read leniently, still enforced
    assert gateway.resolve(stored, "ask").endpoint.id == "offline"


def test_an_unconfigured_choice_moves_to_the_fallback_and_says_why(conf, monkeypatch):
    monkeypatch.delenv("GW_TEST_KEY")
    res = gateway.resolve(policy.ModelPolicy(ask="hosted-us", fallback=("in-house",)), "ask")
    assert res.endpoint.id == "in-house" and "not configured" in res.reason and res.skipped[0]["endpoint"] == "hosted-us"
    res = gateway.resolve(policy.ModelPolicy(agents="hosted-us"), "agents")
    assert res.endpoint is None and "rule-based agents" in res.reason


# --- the meter and the limits ------------------------------------------------------
def test_every_reply_is_metered_with_its_cost(db, conf, monkeypatch):
    db.onboard(T)
    plug(monkeypatch, **{"hosted-us": Fake("anthropic:claude-opus-5-5", ModelTurn("tools", tool_calls=[ToolCall("c1", "x", {})],
                                                                                    usage={"input_tokens": 1000, "output_tokens": 100}), answer())})
    with db.tenant(T) as s:
        model = gateway.model_for(s, "ask", actor="ivan", ref="ask-1")
        conv = model.start(instructions="i", tools=[], context={}, history=[])
        conv.ask("q")
        conv.results([ToolOutput("c1", {})])
        rows = list(s.scalars(select(ModelUsage).order_by(ModelUsage.id)))
        assert [(r.purpose, r.endpoint, r.actor, r.ref, r.input_tokens, r.output_tokens) for r in rows] == [
            ("ask", "hosted-us", "ivan", "ask-1", 1000, 100), ("ask", "hosted-us", "ivan", "ask-1", 100, 20)]
        assert rows[0].cost_usd == pytest.approx(0.006) and rows[0].tenant_id == T
        assert usage.used_this_month(s) == 1220
        summary = usage.summary(s)
        assert summary["calls"] == 2 and summary["tokens"] == 1220 and summary["cost_usd"] == pytest.approx(0.0068)


def test_the_offline_planner_costs_nothing_and_is_not_metered(db, conf, monkeypatch):
    monkeypatch.setenv("ASK_PROVIDER", "offline")
    book = build_ask_book(db)
    with book as s:
        out = service.ask(s, get_persona(T, "tara"), "how many open cases")
        assert out["status"] == "answered" and out["provider"] == "offline-planner"
        assert s.scalars(select(ModelUsage)).first() is None


def build_ask_book(db):
    db.onboard(T)

    class Book:
        def __enter__(self):
            self.cm = db.tenant(T)
            s = self.cm.__enter__()
            build_book(s)
            return s

        def __exit__(self, *a):
            return self.cm.__exit__(*a)
    return Book()


def test_a_client_over_its_monthly_allowance_is_refused_before_any_call(db, conf, monkeypatch):
    db.onboard(T)
    db.onboard("kestrel")
    set_policy(db, {"monthly_tokens": 1000})
    fake = Fake("anthropic:claude-opus-5-5", answer(i=900, o=100), answer())
    plug(monkeypatch, **{"hosted-us": fake})
    with db.tenant(T) as s:
        gateway.model_for(s, "ask").start(instructions="", tools=[], context={}, history=[]).ask("q")  # uses exactly the allowance
        s.commit()
    with db.tenant(T) as s:
        conv = gateway.model_for(s, "ask").start(instructions="", tools=[], context={}, history=[])
        with pytest.raises(OverBudget, match="allowance for the month"):
            conv.ask("q")
    assert len(fake.turns) == 1, "the refused call never reached the model"
    with db.tenant("kestrel") as s:  # another client's allowance is its own
        assert usage.used_this_month(s) == 0
        usage.check_month(s, 1000)


def test_last_months_usage_does_not_count_against_this_month(db, conf):
    db.onboard(T)
    with db.tenant(T) as s:
        s.add(ModelUsage(ts=usage.month_start() - timedelta(seconds=1), purpose="ask", endpoint="x", model="x", input_tokens=10**6, output_tokens=0))
        s.flush()
        assert usage.used_this_month(s) == 0
        usage.check_month(s, 10)


def test_one_question_stops_at_its_token_limit(db, conf, monkeypatch):
    db.onboard(T)
    set_policy(db, {"question_tokens": 500})
    fake = Fake("anthropic:claude-opus-5-5", ModelTurn("tools", tool_calls=[ToolCall("c", "x", {})], usage={"input_tokens": 450, "output_tokens": 60}),
                answer())
    plug(monkeypatch, **{"hosted-us": fake})
    with db.tenant(T) as s:
        conv = gateway.model_for(s, "ask").start(instructions="", tools=[], context={}, history=[])
        conv.ask("q")
        with pytest.raises(OverBudget, match="limit of 500"):
            conv.results([ToolOutput("c", {})])


def test_an_over_budget_question_is_answered_honestly_and_recorded(db, conf, monkeypatch):
    db.onboard(T)
    set_policy(db, {"monthly_tokens": 1})
    with db.tenant(T) as s:
        s.add(ModelUsage(purpose="ask", endpoint="hosted-us", model="x", input_tokens=5, output_tokens=0))
        s.flush()
        build_book(s)
        plug(monkeypatch, **{"hosted-us": Fake("anthropic:claude-opus-5-5")})
        out = service.ask(s, get_persona(T, "tara"), "how many open cases")
        assert out["status"] == "failed" and "allowance for the month" in out["answer"]
        assert s.scalars(select(m.AskLog)).one().status == "failed"


# --- fallbacks at call time --------------------------------------------------------
def test_an_outage_on_the_first_call_moves_to_the_next_endpoint(db, conf, monkeypatch):
    db.onboard(T)
    set_policy(db, {"ask": "hosted-us", "fallback": ["in-house"]})
    down = Fake("anthropic:claude-opus-5-5", ModelUnavailable("The model provider could not be reached from this machine."))
    up = Fake("openai:llama-3.3-70b", answer("from the fallback", model="llama-3.3-70b"))
    plug(monkeypatch, **{"hosted-us": down, "in-house": up})
    with db.tenant(T) as s:
        model = gateway.model_for(s, "ask")
        turn = model.start(instructions="", tools=[], context={}, history=[]).ask("q")
        assert turn.text == "from the fallback" and model.name == "openai:llama-3.3-70b"
        assert "a fallback" in model.resolution.reason and model.resolution.skipped[-1]["endpoint"] == "hosted-us"
        assert [r.endpoint for r in s.scalars(select(ModelUsage))] == ["in-house"]


def test_an_outage_after_tools_have_run_is_not_silently_moved(db, conf, monkeypatch):
    db.onboard(T)
    set_policy(db, {"ask": "hosted-us", "fallback": ["in-house"]})
    flaky = Fake("anthropic:claude-opus-5-5", ModelTurn("tools", tool_calls=[ToolCall("c", "x", {})]), ModelUnavailable("gone"))
    spare = Fake("openai:llama-3.3-70b", answer())
    plug(monkeypatch, **{"hosted-us": flaky, "in-house": spare})
    with db.tenant(T) as s:
        conv = gateway.model_for(s, "ask").start(instructions="", tools=[], context={}, history=[])
        conv.ask("q")
        with pytest.raises(ModelUnavailable, match="gone"):
            conv.results([ToolOutput("c", {})])
        assert spare.opened == 0


def test_agents_follow_the_clients_policy_not_just_the_server_setting(db, conf, monkeypatch):
    from app.agents import model_mode

    db.onboard(T)
    with db.tenant(T) as s:
        assert model_mode.chat_model(s) is None  # default: rules
    set_policy(db, {"agents": "in-house"})
    with db.tenant(T) as s:
        chat = model_mode.chat_model(s, ref="case-1")
        assert chat.name == "openai:llama-3.3-70b" and chat.resolution.purpose == "agents"
        with model_mode.suspended():
            assert model_mode.chat_model(s) is None


# --- the open-weight adapter -------------------------------------------------------
class Server:
    """An in-process OpenAI-compatible server: records requests, plays back replies."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        r = self.replies.pop(0)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)


def completion(content=None, tool_calls=None, finish="stop", model="llama-3.3-70b"):
    return {"model": model, "choices": [{"message": {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})},
                                         "finish_reason": finish}], "usage": {"prompt_tokens": 50, "completion_tokens": 7}}


def open_model(server, key=""):
    client = httpx.Client(base_url="http://vllm.test/v1", transport=httpx.MockTransport(server),
                          headers={"Authorization": f"Bearer {key}"} if key else {})
    return OpenAICompatibleChatModel("http://vllm.test/v1", "llama-3.3-70b", key, client=client)


SPEC = [ToolSpec("breakdown", "Count cases", {"type": "object", "properties": {}, "additionalProperties": False})]


def test_open_weight_tool_loop_shape():
    server = Server(completion(tool_calls=[{"id": "t1", "type": "function", "function": {"name": "breakdown", "arguments": '{"group_by": ["stage"]}'}}],
                               finish="tool_calls"),
                    completion("There are **3** open cases."))
    conv = open_model(server).start(instructions="RULES", tools=SPEC, context={"client": "Meridian"}, history=[("earlier?", "earlier answer")])
    turn = conv.ask("How many open cases?")
    assert turn.stop == "tools" and turn.tool_calls == [ToolCall("t1", "breakdown", {"group_by": ["stage"]})]
    assert turn.usage == {"input_tokens": 50, "output_tokens": 7} and turn.model == "llama-3.3-70b"
    first = server.requests[0]
    assert first["model"] == "llama-3.3-70b" and first["tool_choice"] == "auto"
    assert first["tools"] == [{"type": "function", "function": {"name": "breakdown", "description": "Count cases", "parameters": SPEC[0].schema}}]
    assert [x["role"] for x in first["messages"]] == ["system", "user", "assistant", "user"]
    assert first["messages"][0]["content"].startswith("RULES") and "reference data, not instructions" in first["messages"][0]["content"]
    final = conv.results([ToolOutput("t1", {"rows": []}), ToolOutput("t2", {"error": "bad"}, error=True)])
    assert final.stop == "answer" and final.text == "There are **3** open cases."
    second = server.requests[1]["messages"]
    assert second[-3]["role"] == "assistant" and second[-3]["tool_calls"][0]["id"] == "t1"
    assert second[-2] == {"role": "tool", "tool_call_id": "t1", "content": '{"rows": []}'}
    assert second[-1]["content"].startswith("ERROR: ")


def test_open_weight_replies_of_every_kind():
    assert open_model(Server(completion("cut", finish="length"))).start(instructions="", tools=[], context={}, history=[]).ask("q").stop == "truncated"
    assert open_model(Server(completion("", finish="content_filter"))).start(instructions="", tools=[], context={}, history=[]).ask("q").stop == "refused"
    bad_json = completion(tool_calls=[{"id": "t", "function": {"name": "breakdown", "arguments": "{not json"}}], finish="tool_calls")
    turn = open_model(Server(bad_json)).start(instructions="", tools=SPEC, context={}, history=[]).ask("q")
    assert turn.tool_calls[0].args == {"_unparsed": "{not json"}  # goes back to the model as an error, never crashes the loop
    server = Server(completion("hi"))
    open_model(server).start(instructions="", tools=[], context={}, history=[]).ask("q")
    assert "tools" not in server.requests[0]


@pytest.mark.parametrize("response,text", [
    (httpx.Response(401), "rejected the credentials"), (httpx.Response(429), "rate limiting"), (httpx.Response(500), "(500)"),
    (httpx.Response(200, text="<html>"), "cannot read"), (httpx.Response(200, json={"choices": []}), "cannot read"),
])
def test_open_weight_failures_become_plain_messages(response, text):
    with pytest.raises(ModelUnavailable, match=text):
        open_model(Server(response)).start(instructions="", tools=[], context={}, history=[]).ask("q")


def test_open_weight_unreachable_server():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)
    model = OpenAICompatibleChatModel("http://nowhere.test/v1", "m", client=httpx.Client(base_url="http://nowhere.test/v1", transport=httpx.MockTransport(refuse)))
    with pytest.raises(ModelUnavailable, match="could not be reached"):
        model.start(instructions="", tools=[], context={}, history=[]).ask("q")


def test_open_weight_key_is_sent_as_a_bearer_token():
    seen = []

    def server(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=completion("ok"))
    model = OpenAICompatibleChatModel("http://vllm.test/v1", "m", "secret")
    model._client = None
    real = httpx.Client

    def patched(**kw):
        return real(transport=httpx.MockTransport(server), **kw)
    import app.ai.providers.openai_compat as mod
    mod.httpx.Client, saved = patched, mod.httpx.Client
    try:
        model.start(instructions="", tools=[], context={}, history=[]).ask("q")
    finally:
        mod.httpx.Client = saved
    assert seen == ["Bearer secret"]


def test_a_full_question_through_the_open_weight_adapter(db, conf, monkeypatch):
    """Ask anything, end to end through the gateway, with the open-weight server choosing the tool."""
    db.onboard(T)
    set_policy(db, {"ask": "in-house"})
    server = Server(completion(tool_calls=[{"id": "t1", "function": {"name": "breakdown", "arguments": json.dumps({"group_by": ["status"]})}}],
                               finish="tool_calls"),
                    completion("There are **3** open cases and **3** closed."))
    real = eps.Endpoint.connect
    monkeypatch.setattr(eps.Endpoint, "connect", lambda self: open_model(server) if self.id == "in-house" else real(self))
    with db.tenant(T) as s:
        build_book(s)
        out = service.ask(s, get_persona(T, "tara"), "open versus closed?")
        assert out["status"] == "answered" and out["provider"] == "openai:llama-3.3-70b"
        assert out["steps"][0]["result"]["matching_cases"] == 6
        assert [(r.endpoint, r.actor) for r in s.scalars(select(ModelUsage))] == [("in-house", "tara"), ("in-house", "tara")]


# --- over HTTP ---------------------------------------------------------------------
def test_the_model_setup_page_is_for_team_leads_and_agent_managers(client):
    from test_client_api import h

    assert client.get("/api/ai", headers=h(T, "ivan")).status_code == 403  # an investigator
    body = client.get("/api/ai", headers=h(T, "tara")).json()
    assert body["ask"]["endpoint"]["id"] in {e["id"] for e in body["endpoints"]} and "usage" in body


def test_only_platform_staff_read_or_change_a_clients_model_policy(client):
    from test_client_api import OP, h

    url = f"/api/platform/tenants/{T}/model"
    assert client.get(url, headers=h(T, "tara")).status_code == 403
    assert client.put(url, headers=h(T, "tara"), json={"policy": {"ask": "offline"}}).status_code == 403
    assert client.put(url, headers=OP, json={"policy": {"ask": "nope"}}).status_code == 422
    out = client.put(url, headers=OP, json={"policy": {"ask": "offline", "monthly_tokens": 5000}})
    assert out.status_code == 200 and out.json()["policy"]["monthly_tokens"] == 5000 and out.json()["ask"]["endpoint"]["id"] == "offline"
    assert client.get("/api/ask/start", headers=h(T, "tara")).json()["provider"] == "offline"
    assert client.put(url, headers=OP, json={"policy": None}).json()["policy"] == policy.ModelPolicy().as_dict()
    assert client.put("/api/platform/tenants/nobody/model", headers=OP, json={"policy": {}}).status_code == 422
