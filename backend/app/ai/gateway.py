"""The model gateway: every model call in the platform goes through here.

`model_for(session, purpose)` reads the client's model policy, picks the endpoint for that purpose, checks it is
configured and allowed to run where the client's residency rule says, and returns it wrapped in a meter. The meter
checks the spend limits before every call and records every reply's tokens and cost. If the chosen endpoint cannot
be reached on the first call, the policy's fallbacks are tried in order. Callers never name a provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from . import endpoints as eps
from . import policy as policies
from . import usage
from .types import ChatModel, Conversation, ModelTurn, ModelUnavailable, ToolOutput, ToolSpec


@dataclass
class Resolution:
    """What the policy chose and why, for the screen and the audit record."""

    purpose: str
    endpoint: eps.Endpoint | None  # None: no model (rule-based agents)
    reason: str = ""
    skipped: list[dict] = field(default_factory=list)  # endpoints passed over, each with the reason

    def describe(self) -> dict:
        return {"purpose": self.purpose, "endpoint": self.endpoint.describe() if self.endpoint else None,
                "reason": self.reason, "skipped": self.skipped}


def _usable(pol: policies.ModelPolicy, eid: str) -> tuple[eps.Endpoint | None, str]:
    found = eps.endpoints().get(eid)
    if found is None:
        return None, f"there is no endpoint '{eid}' on this server"
    if not policies.allowed_region(pol, found):
        return None, f"it runs in region '{found.region}', outside this client's residency rule ({', '.join(pol.regions)})"
    if not found.ready:
        return None, f"it is not configured on this server (set {', '.join(found.missing)})"
    return found, ""


def resolve(pol: policies.ModelPolicy, purpose: str) -> Resolution:
    """The endpoint that serves `purpose` under this policy, after residency and configuration checks."""
    if purpose not in policies.PURPOSES:
        raise ValueError(f"Unknown purpose '{purpose}'")
    first = policies.chosen(pol, purpose)
    if first == "rules":
        return Resolution(purpose, None, "the rule-based agents (no model)")
    out = Resolution(purpose, None)
    for eid in dict.fromkeys([first, *pol.fallback]):
        found, why = _usable(pol, eid)
        if found:
            out.endpoint = found
            out.reason = "the client's choice" if eid == first else f"a fallback, because {out.skipped[0]['why']}"
            return out
        out.skipped.append({"endpoint": eid, "why": why})
    # Nothing allowed and configured: questions still get the offline planner, agents the rules. Never a silent upgrade.
    if purpose == "ask":
        out.endpoint, out.reason = eps.OFFLINE, f"the offline planner, because {out.skipped[0]['why']}"
    else:
        out.reason = f"the rule-based agents, because {out.skipped[0]['why']}"
    return out


def model_for(s: Session, purpose: str, *, actor: str = "", ref: str = "", pol: policies.ModelPolicy | None = None) -> "MeteredModel | None":
    """A metered model for this client and purpose, or None when the policy says no model (rule-based agents)."""
    pol = pol or policies.for_session(s)
    res = resolve(pol, purpose)
    if res.endpoint is None:
        return None
    fallbacks = [e for e in (eps.endpoints().get(x) for x in pol.fallback) if e and e.id != res.endpoint.id and _usable(pol, e.id)[0]]
    return MeteredModel(s, res, pol, fallbacks, actor=actor, ref=ref)


class MeteredModel:
    """A model with the client's limits and meter around it. Looks like any ChatModel to its caller."""

    def __init__(self, s: Session, res: Resolution, pol: policies.ModelPolicy, fallbacks: list[eps.Endpoint], *, actor: str, ref: str):
        self.s, self.resolution, self.pol, self.actor, self.ref = s, res, pol, actor, ref
        self.endpoint = res.endpoint
        self.fallbacks = fallbacks
        self.inner: ChatModel = self.endpoint.connect()
        self.name = self.inner.name

    def start(self, *, instructions: str, tools: list[ToolSpec], context: dict, history: list[tuple[str, str]]) -> "MeteredConversation":
        return MeteredConversation(self, dict(instructions=instructions, tools=tools, context=context, history=history))


class MeteredConversation:
    def __init__(self, owner: MeteredModel, opening: dict):
        self.owner, self.opening = owner, opening
        self.conv: Conversation = owner.inner.start(**opening)
        self.tokens = 0
        self.calls = 0

    def _guard(self) -> None:
        o = self.owner
        if o.endpoint.kind == "offline":
            return  # no model, no spend
        usage.check_month(o.s, o.pol.monthly_tokens)
        if o.pol.question_tokens and self.tokens >= o.pol.question_tokens:
            raise usage.OverBudget(f"This question reached its limit of {o.pol.question_tokens:,} model tokens. Try a narrower question.")

    def _meter(self, turn: ModelTurn) -> ModelTurn:
        o = self.owner
        self.calls += 1
        if o.endpoint.kind != "offline":
            usage.record(o.s, purpose=o.resolution.purpose, endpoint=o.endpoint, model=turn.model, actor=o.actor, ref=o.ref, usage=turn.usage)
            self.tokens += int(turn.usage.get("input_tokens", 0) or 0) + int(turn.usage.get("output_tokens", 0) or 0)
        return turn

    def ask(self, question: str) -> ModelTurn:
        self._guard()
        try:
            return self._meter(self.conv.ask(question))
        except usage.OverBudget:
            raise
        except ModelUnavailable as first_error:
            # Only the opening call can move to a fallback: later calls carry tool results the next model never saw.
            o = self.owner
            while o.fallbacks:
                nxt = o.fallbacks.pop(0)
                o.resolution.skipped.append({"endpoint": o.endpoint.id, "why": str(first_error)})
                o.resolution.reason = f"a fallback, because {o.endpoint.label} failed: {first_error}"
                o.endpoint, o.inner = nxt, nxt.connect()
                o.name = o.inner.name
                self.conv = o.inner.start(**self.opening)
                try:
                    return self._meter(self.conv.ask(question))
                except ModelUnavailable as e:
                    first_error = e
            raise first_error from None

    def results(self, outputs: list[ToolOutput]) -> ModelTurn:
        self._guard()
        return self._meter(self.conv.results(outputs))


def describe(s: Session) -> dict:
    """The client's model set-up in words the screen can show: policy, what each purpose resolves to, usage so far."""
    pol = policies.for_session(s)
    return {"policy": pol.as_dict(), "ask": resolve(pol, "ask").describe(), "agents": resolve(pol, "agents").describe(),
            "usage": usage.summary(s), "used_this_month": usage.used_this_month(s),
            "endpoints": [e.describe() for e in eps.endpoints().values()]}
