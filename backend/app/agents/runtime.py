"""The agent runtime: decides which agents run on a case, runs them inside their limits, and records every step.

`assess` works out a briefing without writing anything, so evaluations, lookbacks and the challenger can reuse it.
`brief` is the one place a briefing is stored on a case.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cases.service import definition, record
from ..ask.gateway import ChatModel
from ..domain.models import AgentRun, Alert, Case, Transaction, new_id, now
from . import ledger, model_mode, policy as policies
from .catalog import AgentSpec, catalog
from .llm import LLMProvider, get_provider
from .steps import AGENTS, Context, Step, _f, decide
from .tools import ToolDenied, Toolbox, extract, media_search

NEEDS_NO_ALERT = {"adverse_media", "kyc_refresh"}  # agents that have something to say about a case with no alerted activity
DETERMINISTIC = "rules"  # recorded as the model for agents that do not call one


@dataclass
class StepResult:
    agent: str
    title: str
    summary: str = ""
    findings: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    level: str = "L2"
    outcome: str = "done"  # done | skipped | refused
    extra: dict = field(default_factory=dict)
    model: str = DETERMINISTIC
    prompt: str = ""  # the instructions given to a model, when one was used; hashed into the ledger

    def shown(self) -> dict:
        return {"agent": self.agent, "title": self.title, "summary": self.summary, "findings": self.findings, "level": self.level,
                "outcome": self.outcome, "extra": self.extra, "rejected": len(self.rejected)}


@dataclass
class Assessment:
    recommendation: str = ""
    confidence: float = 0.0
    steps: list[StepResult] = field(default_factory=list)
    signals: list[tuple[str, float]] = field(default_factory=list)
    draft: str = ""
    shadow: dict | None = None


def context(s: Session, case: Case) -> Context:
    alerts = list(s.scalars(select(Alert).where(Alert.case_id == case.id).order_by(Alert.created)))
    ids = {i for a in alerts for i in a.txn_ids}
    alerted = list(s.scalars(select(Transaction).where(Transaction.id.in_(ids)).order_by(Transaction.ts))) if ids else []
    end = max((t.ts for t in alerted), default=case.created)
    history = list(s.scalars(select(Transaction).where(Transaction.party_id == case.party_id,
                                                       Transaction.ts >= end - timedelta(days=120),
                                                       Transaction.ts <= end).order_by(Transaction.ts)))
    prior = s.scalar(select(func.count()).select_from(Case).where(Case.party_id == case.party_id, Case.id != case.id,
                                                                  Case.created < case.created)) or 0
    return Context(case=case, party=case.party, alerts=alerts, alerted=alerted, history=history, prior_cases=prior)


def _gate(spec: AgentSpec, pol: policies.Policy, case_type: str, requested: bool) -> tuple[str, str | None]:
    """The level an agent has on this case type, and the reason it does not run, if it does not."""
    if not pol.enabled(spec.id):
        return "L0", "Switched off for this client"
    level = pol.level(spec.id, case_type)
    if level == "L0":
        return level, "Off for this case type"
    if level == "L1" and not requested:
        return level, "Runs only when a person asks"
    return level, None


def _run_step(spec: AgentSpec, level: str, ctx: Context, fn) -> StepResult:
    box = Toolbox(spec, level)
    ctx.tools = box
    before = len(ctx.signals)
    try:
        step: Step = fn(ctx)
    except ToolDenied as e:
        del ctx.signals[before:]  # a refused step contributes nothing
        return StepResult(spec.id, spec.title, str(e), tools=box.calls, level=level, outcome="refused")
    finally:
        ctx.tools = None
    kept = [f for f in step.findings if f.get("cites") or f.get("sources")]
    rejected = [f for f in step.findings if not (f.get("cites") or f.get("sources"))]
    extra = dict(step.extra)
    model, prompt = (extra.pop("model", DETERMINISTIC), extra.pop("prompt", "")) if extra.get("mode") == "model" else (DETERMINISTIC, "")
    return StepResult(spec.id, step.title, step.summary, kept, rejected, box.calls, level, extra=extra, model=model, prompt=prompt)


def _narrative(ctx: Context, provider: LLMProvider) -> Step:
    ctx.use("case_findings", f"{len(ctx.facts.get('key_findings', []))} finding(s)")
    ctx.use("model_gateway", provider.name)
    draft = provider.write_narrative(ctx.facts)
    return Step("narrative_writer", "Narrative draft", "Drafted a narrative from the cited findings.",
                [_f(draft, sources=[f"Briefing for {ctx.case.id}"])], {"draft": draft})


def _qa(ctx: Context, steps: list[StepResult]) -> Step:
    ctx.use("case_findings", f"{sum(len(st.findings) for st in steps)} finding(s)")
    dropped = sum(len(st.rejected) for st in steps)
    refused = [st.title for st in steps if st.outcome == "refused"]
    by_txn = sum(1 for st in steps for f in st.findings if f["cites"])
    total = sum(len(st.findings) for st in steps)
    src = [f"Briefing for {ctx.case.id}"]
    checks = [_f(f"Every recommendation driver traces to case data: {len(ctx.signals)} signal(s) recorded.", sources=src),
              _f(f"{by_txn} of {total} findings cite transactions; the rest cite the profile, account history or another named record.", sources=src)]
    if dropped:
        checks.append(_f(f"{dropped} statement(s) cited nothing and were dropped from the briefing.", sources=src))
    if refused:
        checks.append(_f(f"Refused a step outside its permissions: {', '.join(refused)}.", sources=src))
    return Step("qa_critic", "QA check", "Checked completeness and citation coverage.", checks)


def assess(s: Session, case: Case, pol: policies.Policy | None = None, *, requested: bool = False, provider: LLMProvider | None = None,
           decision: dict | None = None, chat: ChatModel | None = None) -> Assessment:
    """Work out the briefing for a case under a policy. Reads only. `chat` overrides the configured model for the specialists."""
    pol = pol or policies.current(s)
    chat = chat or model_mode.chat_model()
    provider = provider or get_provider()
    ct = definition(case)
    declared = [a for a in ct.get("agents", {}).get("on_create", []) if a in catalog()]
    ctx = context(s, case)
    out = Assessment()

    def gate(name: str) -> tuple[AgentSpec, str, bool]:
        spec = catalog()[name]
        level, why = _gate(spec, pol, case.case_type, requested)
        if why:
            out.steps.append(StepResult(name, spec.title, why, level=level, outcome="skipped"))
        return spec, level, why is None

    has_alerts = bool(ctx.alerted)
    if not has_alerts and not any(a in NEEDS_NO_ALERT for a in declared):
        out.steps.append(StepResult("data_assembly", "Data assembly", "No alerted transactions on this case yet."))
        return out
    for name in declared:
        if name not in AGENTS or (not has_alerts and name not in NEEDS_NO_ALERT):
            continue
        spec, level, runs = gate(name)
        if not runs:
            continue
        if name == "adverse_media":
            ctx.media = [extract(doc, ctx.party.name) for doc in media_search(s, ctx.party.id)]
        fn = AGENTS[name]
        if chat is not None and name in model_mode.MODEL_AGENTS:
            fn = lambda c, spec=spec, rules=AGENTS[name]: model_mode.run(spec, c, chat, rules)  # noqa: E731
        out.steps.append(_run_step(spec, level, ctx, fn))
    out.signals = list(ctx.signals)
    if not has_alerts:  # a review with no alerted activity is prepared, not scored
        return out
    threshold = (decision or pol.decision)["escalate_at"]
    out.recommendation, out.confidence = decide(ctx, threshold)
    if pol.challenger and decision is None:
        rec, conf = decide(ctx, pol.challenger["decision"]["escalate_at"])
        out.shadow = {"name": pol.challenger["name"], "recommendation": rec, "confidence": conf,
                      "escalate_at": pol.challenger["decision"]["escalate_at"]}
    done = [st for st in out.steps if st.outcome == "done"]
    ctx.facts.update(recommendation=out.recommendation, n_alerts=len(ctx.alerts), scenarios=sorted({a.scenario for a in ctx.alerts}),
                     party={"id": ctx.party.id, "name": ctx.party.name, "occupation": ctx.party.occupation,
                            "risk_rating": ctx.party.risk_rating, "onboarded": ctx.party.onboarded.date().isoformat()},
                     key_findings=[f for st in done if st.agent != "data_assembly" for f in st.findings])
    if "narrative_writer" in declared and "alerted_total" in ctx.facts:
        spec, level, runs = gate("narrative_writer")
        if runs:
            step = _run_step(spec, level, ctx, lambda c: _narrative(c, provider))
            step.model = provider.name
            out.draft = step.extra.pop("draft", "")
            out.steps.append(step)
    if "qa_critic" in declared:
        spec, level, runs = gate("qa_critic")
        if runs:
            out.steps.append(_run_step(spec, level, ctx, lambda c: _qa(c, [st for st in out.steps if st.outcome != "skipped"])))
    return out


def brief(s: Session, case: Case, provider: LLMProvider | None = None, ts: datetime | None = None, requested_by: str | None = None,
          chat: ChatModel | None = None) -> AgentRun:
    """Run the agents a case type declares, inside the client's policy, and store the briefing and its ledger."""
    pol = policies.current(s)
    a = assess(s, case, pol, requested=requested_by is not None, provider=provider, chat=chat)
    at = ts or now()
    run = AgentRun(id=new_id("run"), case_id=case.id, provider=(provider or get_provider()).name, started=at, finished=at,
                   recommendation=a.recommendation, confidence=a.confidence, policy_version=pol.version, requested_by=requested_by,
                   shadow=a.shadow)
    run.steps = [st.shown() for st in a.steps if st.outcome != "skipped" or st.summary != "Off for this case type"]
    run.steps.append({"agent": "orchestrator", "title": "Recommendation", "summary": "", "level": "L2", "outcome": "done", "extra": {},
                      "rejected": 0, "findings": [_f(f"{'+' if w > 0 else '−'} {r}", sources=[f"Briefing for {case.id}"]) for r, w in a.signals]})
    if a.recommendation:
        case.agent_recommendation = a.recommendation
    if a.draft and not case.narrative:
        case.narrative = a.draft  # a draft only: drafted_by stays empty until a person saves it
    s.add(run)
    for st in a.steps:
        spec = catalog()[st.agent]
        ledger.append(s, run_id=run.id, case_id=case.id, agent=st.agent, agent_version=spec.version, level=st.level,
                      policy_version=pol.version, outcome=st.outcome, summary=st.summary, tools=st.tools, findings=st.findings,
                      rejected=st.rejected, model=st.model, prompt=st.prompt or spec.goal, ts=at)
    if a.shadow:
        ledger.append(s, run_id=run.id, case_id=case.id, agent="orchestrator", agent_version=pol.challenger["name"], level="L2",
                      policy_version=pol.version, summary=f"Challenger would recommend {a.shadow['recommendation']}", shadow=True, ts=at,
                      findings=[a.shadow])
    record(s, case.id, "investigation-agent", "agent", "agent.recommended",
           {"run_id": run.id, "recommendation": run.recommendation, "confidence": run.confidence, "provider": run.provider,
            "autonomy": definition(case).get("agents", {}).get("autonomy", "L2"), "policy_version": pol.version}, ts=ts)
    return run
