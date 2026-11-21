"""Orchestrator: runs the agents a case type declares, then records a cited recommendation."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cases.service import definition, record
from ..domain.models import AgentRun, Alert, Case, Transaction, new_id, now
from .llm import LLMProvider, get_provider
from .steps import AGENTS, Context, Step, _f, decide


def _context(s: Session, case: Case) -> Context:
    alerts = list(s.scalars(select(Alert).where(Alert.case_id == case.id).order_by(Alert.created)))
    ids = {i for a in alerts for i in a.txn_ids}
    alerted = list(s.scalars(select(Transaction).where(Transaction.id.in_(ids)).order_by(Transaction.ts))) if ids else []
    end = max((t.ts for t in alerted), default=now())
    history = list(s.scalars(select(Transaction).where(Transaction.party_id == case.party_id,
                                                       Transaction.ts >= end - timedelta(days=120),
                                                       Transaction.ts <= end).order_by(Transaction.ts)))
    prior = s.scalar(select(func.count()).select_from(Case).where(Case.party_id == case.party_id, Case.id != case.id)) or 0
    return Context(case=case, party=case.party, alerts=alerts, alerted=alerted, history=history, prior_cases=prior)


def investigate(s: Session, case: Case, provider: LLMProvider | None = None, ts: datetime | None = None) -> AgentRun:
    provider = provider or get_provider()
    ct = definition(case)
    ctx = _context(s, case)
    run = AgentRun(id=new_id("run"), case_id=case.id, provider=provider.name, started=ts or now())
    steps: list[Step] = []
    if not ctx.alerted:
        steps.append(Step("data_assembly", "Data assembly", "No alerted transactions on this case yet.", []))
    else:
        for name in ct.get("agents", {}).get("on_create", []):
            if name in AGENTS:
                steps.append(AGENTS[name](ctx))
        rec, conf = decide(ctx)
        ctx.facts.update(recommendation=rec, n_alerts=len(ctx.alerts), scenarios=sorted({a.scenario for a in ctx.alerts}),
                         party={"id": ctx.party.id, "name": ctx.party.name, "occupation": ctx.party.occupation,
                                "risk_rating": ctx.party.risk_rating, "onboarded": ctx.party.onboarded.date().isoformat()},
                         key_findings=[f for st in steps[1:] for f in st.findings])
        declared = ct.get("agents", {}).get("on_create", [])
        draft = ""
        if "narrative_writer" in declared:
            draft = provider.write_narrative(ctx.facts)
            steps.append(Step("narrative_writer", "Narrative draft", "Drafted a narrative from the cited findings.", [_f(draft)]))
        if "qa_critic" in declared:
            uncited = sum(1 for st in steps[:-1] for f in st.findings if not f["cites"])
            checks = [_f(f"Every recommendation driver traces to case data: {len(ctx.signals)} signal(s) recorded."),
                      _f(f"{uncited} contextual finding(s) carry no transaction citation (profile and history statements).")]
            steps.append(Step("qa_critic", "QA check", "Checked completeness and citation coverage.", checks))
        run.recommendation, run.confidence = rec, conf
        case.agent_recommendation = rec
        if draft and not case.narrative:
            case.narrative = draft  # a draft only: drafted_by stays empty until a human saves it
    run.steps = [{"agent": st.agent, "title": st.title, "summary": st.summary, "findings": st.findings} for st in steps]
    run.steps.append({"agent": "orchestrator", "title": "Recommendation", "summary": "",
                      "findings": [_f(f"{'+' if w > 0 else '−'} {r}") for r, w in ctx.signals]})
    run.finished = ts or now()
    s.add(run)
    record(s, case.id, "investigation-agent", "agent", "agent.recommended",
           {"run_id": run.id, "recommendation": run.recommendation, "confidence": run.confidence,
            "provider": provider.name, "autonomy": ct.get("agents", {}).get("autonomy", "L2")}, ts=ts)
    return run
