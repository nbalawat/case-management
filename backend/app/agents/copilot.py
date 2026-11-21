"""The investigator copilot: answers questions about one case from that case's record, and drafts for a person to send.

It runs only when asked. It reads the case; it changes nothing on it. Every answer names the records it rests on.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.service import record
from ..domain.access import Persona
from ..domain.models import AgentRun, Case, new_id
from . import ledger, policy as policies
from .catalog import catalog
from .runtime import context
from .tools import Toolbox

STANDARD_QUESTIONS = ["Why is this the recommendation?", "Summarise this case", "What are the largest transactions?",
                      "Draft a request to the customer", "Draft a closure note"]
AGENT = "investigator_copilot"


class CopilotOff(PermissionError):
    pass


def _run(s: Session, case: Case) -> AgentRun | None:
    return s.scalars(select(AgentRun).where(AgentRun.case_id == case.id).order_by(AgentRun.started.desc(), AgentRun.id.desc())).first()


def _findings(run: AgentRun | None, *agents: str) -> list[dict]:
    return [f for st in (run.steps if run else []) if st["agent"] in agents and st.get("outcome", "done") == "done" for f in st["findings"]]


def _merge(findings: list[dict]) -> tuple[list[str], list[str]]:
    cites = list(dict.fromkeys(c for f in findings for c in f.get("cites", [])))[:12]
    sources = list(dict.fromkeys(x for f in findings for x in f.get("sources", [])))
    return cites, sources


def compose(s: Session, case: Case, question: str) -> dict:
    """Work out an answer. Reads only."""
    q = question.lower()
    run, ctx = _run(s, case), context(s, case)
    briefing = f"Briefing for {case.id}"
    p = case.party
    drafting = bool(re.search(r"\b(draft|write|prepare|compose)\b", q))

    if drafting and re.search(r"\b(request|rfi|question|ask|customer|letter|email)\b", q):
        kyc = next((st["extra"].get("draft_request") for st in (run.steps if run else []) if st["agent"] == "kyc_refresh" and st.get("extra")), None)
        if kyc:
            draft, basis = kyc, _findings(run, "kyc_refresh")
        else:
            big = sorted(ctx.alerted, key=lambda t: -t.amount)[:3]
            lines = "; ".join(f"{t.ts.date().isoformat()} ${t.amount:,.0f}" + (f" to {t.counterparty}" if t.counterparty and t.direction == "out"
                              else f" from {t.counterparty}" if t.counterparty else f" ({t.channel})") for t in big)
            draft = (f"Dear {p.name},\n\nAs part of a routine review we would like to understand some recent activity on your account"
                     + (f": {lines}" if lines else "") + ". Please tell us the purpose of these transactions and your relationship with the "
                     "other parties, and send any supporting documents.\n\nThank you for your help.")
            basis = [{"cites": [t.id for t in big], "sources": [ctx.profile]}]
        cites, sources = _merge(basis)
        return {"intent": "draft_request", "answer": "Here is a draft request. Edit it, then raise it from the information requests panel; "
                "nothing has been sent.", "draft": draft, "cites": cites, "sources": sources or [ctx.profile]}

    if drafting:
        signals = _findings(run, "orchestrator")
        rec = run.recommendation if run and run.recommendation else "no recommendation"
        reasons = " ".join(f["text"].lstrip("+− ").capitalize() + "." for f in signals[:5])
        draft = (f"Reviewed {p.name} ({p.id}) following {len(ctx.alerts)} alert(s) covering {len(ctx.alerted)} transaction(s) totalling "
                 f"${sum(t.amount for t in ctx.alerted):,.0f}. {reasons} Agent recommendation: {rec}. Reviewer's conclusion: ")
        return {"intent": "draft_note", "answer": "Here is a draft note that stops at your conclusion. Finish it in your own words.",
                "draft": draft, "cites": [t.id for t in ctx.alerted][:12], "sources": [briefing]}

    if re.search(r"\b(why|explain|reason|recommend|rationale|score|confiden)", q):
        if not run or not run.recommendation:
            return {"intent": "explain", "answer": "There is no agent recommendation on this case yet.", "cites": [], "sources": [briefing]}
        signals = _findings(run, "orchestrator")
        up = [f["text"][2:] for f in signals if f["text"].startswith("+")]
        down = [f["text"][2:] for f in signals if f["text"].startswith("−")]
        text = (f"The briefing recommends {'escalating' if run.recommendation == 'escalate' else 'closing'} with {run.confidence:.0%} confidence. "
                + (f"Pointing towards concern: {'; '.join(up)}. " if up else "Nothing pointed towards concern. ")
                + (f"Pointing away: {'; '.join(down)}. " if down else "") + "It is a recommendation; the decision is yours.")
        cites, sources = _merge(_findings(run, "entity_research", "typology_analyst", "fraud_analyst", "sanctions_adjudicator", "adverse_media"))
        return {"intent": "explain", "answer": text, "cites": cites, "sources": sources or [briefing]}

    if re.search(r"\b(media|news|press|article|reputation)", q):
        found = _findings(run, "adverse_media")
        text = " ".join(f["text"] for f in found) or "The adverse media agent has not run on this case."
        cites, sources = _merge(found)
        return {"intent": "media", "answer": text, "cites": cites, "sources": sources or [briefing]}

    if re.search(r"\b(transaction|payment|largest|biggest|activity|counterpart|payee|wire|cash|deposit)", q):
        big = sorted(ctx.alerted or ctx.history, key=lambda t: -t.amount)[:5]
        if not big:
            return {"intent": "activity", "answer": "There is no account activity on this case.", "cites": [], "sources": [ctx.record]}
        rows = "; ".join(f"{t.ts.date().isoformat()} ${t.amount:,.0f} {t.direction} by {t.channel}" + (f", {t.counterparty} ({t.counterparty_country})"
                         if t.counterparty else "") for t in big)
        scope = "alerted" if ctx.alerted else "recent"
        return {"intent": "activity", "answer": f"The largest {scope} transactions: {rows}.", "cites": [t.id for t in big], "sources": [ctx.record]}

    if re.search(r"\b(who|customer|profile|occupation|risk rating|pep|onboard)", q):
        text = (f"{p.name} ({p.id}) is a {p.kind}, {p.occupation or 'occupation not recorded'}, in {p.country}, rated {p.risk_rating} risk"
                f"{', politically exposed' if p.pep else ''}, a customer since {p.onboarded.date().isoformat()}, with {ctx.prior_cases} earlier case(s).")
        return {"intent": "customer", "answer": text, "cites": [], "sources": [ctx.profile, f"Case history {p.id}"]}

    if re.search(r"\b(summar|overview|what happened|brief|tell me about|key (points|facts))", q) or not q.strip("?. "):
        found = _findings(run, "data_assembly") + _findings(run, "entity_research", "typology_analyst", "fraud_analyst", "sanctions_adjudicator",
                                                           "kyc_refresh", "adverse_media")[:4]
        text = " ".join(f["text"] for f in found) or "No briefing has been prepared for this case yet."
        cites, sources = _merge(found)
        return {"intent": "summary", "answer": text, "cites": cites, "sources": sources or [briefing]}

    return {"intent": "help", "answer": "I can explain the recommendation, summarise the case, list the largest transactions, describe the "
            "customer, report adverse media, and draft a request to the customer or a closure note.", "cites": [], "sources": [briefing]}


def ask(s: Session, case: Case, persona: Persona, question: str) -> dict:
    question = question.strip()
    if not question or len(question) > 500:
        raise ValueError("Ask a question of up to 500 characters")
    pol = policies.current(s)
    level = pol.level(AGENT, case.case_type)
    if level == "L0":
        raise CopilotOff("The copilot is switched off for this case type")
    spec = catalog()[AGENT]
    box = Toolbox(spec, level)
    for tool in spec.tools:
        box.use(tool, case.id)
    out = compose(s, case, question)
    ledger.append(s, run_id=new_id("cop"), case_id=case.id, agent=AGENT, agent_version=spec.version, level=level, policy_version=pol.version,
                  summary=question, tools=box.calls, prompt=spec.goal,
                  findings=[{"text": out["answer"], "cites": out["cites"], "sources": out["sources"]}])
    record(s, case.id, persona.id, "human", "copilot.asked", {"question": question, "intent": out["intent"]})
    return out | {"question": question, "level": level}
