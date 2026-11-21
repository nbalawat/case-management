"""The hosted-model adapter, exercised against a stand-in client so no key or network is needed.

These tests pin the shape of what is sent and how each kind of reply is read. They do not prove answer quality:
that needs a real model and is not run here.
"""
from types import SimpleNamespace as NS

import pytest

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx")

from app.ask import service  # noqa: E402
from app.ask.gateway import ModelUnavailable, ToolOutput, ToolSpec  # noqa: E402
from app.ask.providers.anthropic import AnthropicChatModel  # noqa: E402
from app.domain.personas import get_persona  # noqa: E402
from ask_support import C, build_book  # noqa: E402
from support import T  # noqa: E402

SPEC = [ToolSpec("breakdown", "Count cases", {"type": "object", "properties": {}, "additionalProperties": False})]


def text(t):
    return NS(type="text", text=t)


def thinking():
    return NS(type="thinking", thinking="", signature="sig")


def use(id_, name, **args):
    return NS(type="tool_use", id=id_, name=name, input=args)


def reply(*content, stop="end_turn", model="claude-opus-5-5"):
    return NS(content=list(content), stop_reason=stop, model=model, usage=NS(input_tokens=100, output_tokens=20))


class Stub:
    """Stands in for anthropic.Anthropic(): records each request and plays back replies."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw | {"messages": list(kw["messages"])})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def start(stub, history=()):
    model = AnthropicChatModel("claude-opus-5-5", "medium", client=stub)
    return model.start(instructions="RULES", tools=SPEC, context={"client": "Meridian"}, history=list(history))


def test_request_shape():
    stub = Stub(reply(text("Hello")))
    turn = start(stub, [("earlier q", "earlier a")]).ask("how many?")
    req = stub.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["max_tokens"] >= 4000
    assert req["thinking"] == {"type": "adaptive"} and req["output_config"] == {"effort": "medium"}
    assert req["betas"] == ["server-side-fallback-2026-07-01"] and req["fallbacks"] == "default"
    assert "tool_choice" not in req and "temperature" not in req
    assert req["tools"] == [{"name": "breakdown", "description": "Count cases", "input_schema": SPEC[0].schema}]
    assert req["system"][0] == {"type": "text", "text": "RULES", "cache_control": {"type": "ephemeral"}}
    assert "Meridian" in req["system"][1]["text"] and "cache_control" not in req["system"][1]
    assert req["messages"] == [{"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"},
                               {"role": "user", "content": "how many?"}]
    assert (turn.stop, turn.text, turn.model) == ("answer", "Hello", "claude-opus-5-5")
    assert turn.usage == {"input_tokens": 100, "output_tokens": 20}


def test_tool_calls_and_results_round_trip_with_the_reply_kept_intact():
    first = reply(thinking(), text("Let me check."), use("tu_1", "breakdown", group_by=["status"]), use("tu_2", "breakdown"), stop="tool_use")
    stub = Stub(first, reply(text("Done")))
    conv = start(stub)
    turn = conv.ask("q")
    assert turn.stop == "tools" and [(c.id, c.name, c.args) for c in turn.tool_calls] == [("tu_1", "breakdown", {"group_by": ["status"]}),
                                                                                         ("tu_2", "breakdown", {})]
    done = conv.results([ToolOutput("tu_1", {"rows": []}), ToolOutput("tu_2", {"error": "no"}, error=True)])
    sent = stub.requests[1]["messages"]
    assert sent[1] == {"role": "assistant", "content": first.content}  # thinking and tool_use blocks go back unchanged
    assert sent[2]["role"] == "user" and len(sent) == 3  # every result in one message
    assert sent[2]["content"] == [{"type": "tool_result", "tool_use_id": "tu_1", "content": '{"rows": []}'},
                                  {"type": "tool_result", "tool_use_id": "tu_2", "content": '{"error": "no"}', "is_error": True}]
    assert done.stop == "answer" and done.text == "Done"


def test_refusal_truncation_and_fallback_model():
    assert start(Stub(reply(stop="refusal"))).ask("q").stop == "refused"
    cut = start(Stub(reply(text("Half"), stop="max_tokens"))).ask("q")
    assert (cut.stop, cut.text) == ("truncated", "Half")
    served = start(Stub(reply(text("ok"), model="claude-opus-4-8"))).ask("q")
    assert served.model == "claude-opus-4-8"  # the turn reports the model that actually answered


def test_a_paused_turn_is_sent_back_to_continue():
    paused = reply(text("partial"), stop="pause_turn")
    stub = Stub(paused, reply(text("finished")))
    turn = start(stub).ask("q")
    assert turn.text == "finished" and stub.requests[1]["messages"][-1] == {"role": "assistant", "content": paused.content}
    assert start(Stub(paused, paused, paused)).ask("q").stop == "truncated"


def _status(cls, code):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(code, request=request), body=None)


@pytest.mark.parametrize("error,says", [
    (lambda: _status(anthropic.AuthenticationError, 401), "API key"),
    (lambda: _status(anthropic.RateLimitError, 429), "rate limiting"),
    (lambda: _status(anthropic.InternalServerError, 500), "(500)"),
    (lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), "could not be reached"),
])
def test_provider_errors_become_a_plain_message(error, says):
    with pytest.raises(ModelUnavailable) as e:
        start(Stub(error())).ask("q")
    assert says in str(e.value) and "boom" not in str(e.value)


def test_full_answer_loop_through_the_adapter(s):
    """The real loop, the real tools and the adapter together; only the HTTP client is a stand-in."""
    build_book(s)
    stub = Stub(reply(thinking(), use("tu_1", "list_cases", filters=[{"field": "status", "op": "=", "value": "open"}],
                                      sort_by="alerted_amount", limit=1), stop="tool_use"),
                reply(text(f"The largest open case is {C[2]}, with **$80,000** alerted.")))
    out = service.ask(s, get_persona(T, "ana"), "What is the largest open case?", model=AnthropicChatModel("claude-opus-5-5", client=stub))
    assert out["status"] == "answered" and [c["id"] for c in out["cited"]] == [C[2]] and out["unverified"] == []
    assert out["provider"] == "anthropic:claude-opus-5-5" and out["usage"] == {"input_tokens": 200, "output_tokens": 40}
    offered = {t["name"] for t in stub.requests[0]["tools"]}
    assert offered == {"breakdown", "list_cases", "case_detail", "customer"}  # ana does not hold the team view
    result = stub.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "tu_1" and C[2] in result["content"] and "is_error" not in result
    assert stub.requests[0]["system"][0]["text"] == service.INSTRUCTIONS
