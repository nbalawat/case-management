"""Oversight of the people using agents, and the paperwork for the people overseeing the agents.

Agreement rates show whether reviewers are exercising judgement or accepting whatever the briefing says.
The inventory is the documentation pack a model risk function asks for.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import load_case_types
from ..domain.models import Case, CaseEvent
from ..domain.personas import policy_of
from ..tenancy import tenant_of
from . import evals, ledger, policy as policies
from .catalog import ACTS, HUMAN_ONLY, LEVEL_LABELS, catalog
from .triage import POSITIVE
from ..visibility import system_work

RUBBER_STAMP = {"min_decisions": 8, "agreement": 0.98}


@system_work
def agreement(s: Session) -> dict:
    """For each person who closed cases that had a recommendation: how often they agreed with it, and which way they overrode it."""
    tenant = tenant_of(s)
    people = policy_of(tenant).people
    cases = {c.id: c for c in s.scalars(select(Case).where(Case.closed.is_not(None), Case.agent_recommendation.is_not(None)))}
    rows: dict[str, dict] = {}
    for ev in s.scalars(select(CaseEvent).where(CaseEvent.kind == "case.state_changed", CaseEvent.actor_kind == "human").order_by(CaseEvent.ts)):
        c = cases.get(ev.case_id)
        if c is None or c.closed != ev.ts or not c.disposition:
            continue
        human = "escalate" if c.disposition in POSITIVE else "close"
        r = rows.setdefault(ev.actor, {"decisions": 0, "agreed": 0, "overrode_to_escalate": 0, "overrode_to_close": 0})
        r["decisions"] += 1
        if human == c.agent_recommendation:
            r["agreed"] += 1
        elif human == "escalate":
            r["overrode_to_escalate"] += 1
        else:
            r["overrode_to_close"] += 1
    out = []
    for who, r in rows.items():
        rate = r["agreed"] / r["decisions"]
        flag = r["decisions"] >= RUBBER_STAMP["min_decisions"] and rate >= RUBBER_STAMP["agreement"]
        out.append({"person": who, "name": people[who].name if who in people else who, "title": people[who].title if who in people else "",
                    "decisions": r["decisions"], "agreed": r["agreed"], "agreement": round(rate, 3),
                    "overrode_to_escalate": r["overrode_to_escalate"], "overrode_to_close": r["overrode_to_close"], "review": flag})
    total = sum(r["decisions"] for r in out)
    return {"people": sorted(out, key=lambda r: -r["decisions"]), "decisions": total,
            "agreement": round(sum(r["agreed"] for r in out) / total, 3) if total else None,
            "rule": f"Flagged for review at {RUBBER_STAMP['min_decisions']} or more decisions with {RUBBER_STAMP['agreement']:.0%} or more agreement."}


@system_work
def inventory(s: Session) -> dict:
    """Every agent this client runs: what it is for, what it may touch, how far it may go here, and how it has performed."""
    tenant, pol = tenant_of(s), policies.current(s)
    types = load_case_types(tenant)
    use = ledger.usage(s)
    ev = evals.latest(s) or {}
    current = evals.latest(s, policies.fingerprint(pol.body))
    agents = []
    for aid, spec in catalog().items():
        levels = {ct: pol.level(aid, ct) for ct in types}
        result = (current or {}).get("sets", {}).get(spec.evaluation)
        agents.append({
            "id": aid, "title": spec.title, "version": spec.version, "kind": spec.kind, "goal": spec.goal, "tools": list(spec.tools),
            "reads_outside_content": spec.untrusted_input, "max_level": spec.max_level, "acts": spec.acts,
            "acts_label": ACTS.get(spec.acts or "", None), "enabled": pol.enabled(aid), "levels": levels,
            "highest_level_here": max(levels.values(), default="L0"), "evaluation_set": spec.evaluation,
            "evaluation": None if result is None else {"passed": result["passed"], "why": result["why"], "items": result["items"],
                                                       "at": current["at"]},
            "usage": use.get(aid, {"done": 0, "skipped": 0, "refused": 0, "acted": 0, "rejected_claims": 0, "last": None}),
            "model": "Client's pinned model through the gateway" if "model_gateway" in spec.tools else "Deterministic rules; no model call"})
    return {"client": tenant, "policy_version": pol.version, "fingerprint": policies.fingerprint(pol.body), "limits": pol.limits,
            "decision": pol.decision, "challenger": pol.challenger, "agents": agents,
            "case_types": [{"id": k, "title": v.get("title", k)} for k, v in types.items()],
            "levels": [{"id": k, "label": v} for k, v in LEVEL_LABELS.items()],
            "human_only": list(HUMAN_ONLY.values()), "ledger": ledger.verify(s),
            "last_evaluation": None if not ev else {"at": ev["at"], "by": ev["by"], "passed": ev["passed"],
                                                    "covers_current_settings": ev["fingerprint"] == policies.fingerprint(pol.body)},
            "controls": ["Agents call only the tools their declaration lists; anything else is refused and logged.",
                         "No agent can file a report, close or move a case, approve a step or exit a customer. These are not tools.",
                         "A finding that cites no record is dropped from the briefing and counted.",
                         "Outside content reaches an agent only as typed fields from an extractor that has no tools.",
                         "Every step is written to a hash-chained ledger with the agent version, level, policy version, model and prompt hash.",
                         "An agent may act on its own only under settings whose evaluation on this client's history passed.",
                         "Each agent has a kill switch that takes effect on the next item."]}


def inventory_markdown(inv: dict) -> str:
    lines = [f"# Agent inventory: {inv['client']}", "", f"Agent policy version {inv['policy_version']} (settings fingerprint {inv['fingerprint']}).",
             f"Ledger: {inv['ledger']['rows']} entries, chain {'intact' if inv['ledger']['intact'] else 'BROKEN'}.", "", "## Controls", ""]
    lines += [f"- {c}" for c in inv["controls"]]
    lines += ["", "## Reserved for people", ""] + [f"- {h}" for h in inv["human_only"]]
    for a in inv["agents"]:
        ev = a["evaluation"]
        lines += ["", f"## {a['title']} ({a['id']} {a['version']})", "", a["goal"], "",
                  f"- Status: {'on' if a['enabled'] else 'switched off'}; highest autonomy here {a['highest_level_here']} of a possible {a['max_level']}",
                  f"- Tools: {', '.join(a['tools'])}", f"- May do on its own at L3: {a['acts_label'] or 'nothing'}",
                  f"- Model: {a['model']}", f"- Reads outside content: {'yes, through the extractor' if a['reads_outside_content'] else 'no'}",
                  f"- Evaluation ({a['evaluation_set']}): " + ("not run for the current settings" if ev is None else
                                                              f"{'passed' if ev['passed'] else 'did not pass'} on {ev['items']} item(s): {ev['why']}"),
                  f"- Use: {a['usage']['done']} step(s), {a['usage']['acted']} action(s), {a['usage']['refused']} refused, "
                  f"{a['usage']['rejected_claims']} uncited claim(s) dropped"]
    return "\n".join(lines) + "\n"
