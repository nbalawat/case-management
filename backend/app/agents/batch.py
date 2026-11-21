"""Agents that work over many items and hand people a list: the lookback and the tuning advisor."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..detection.scenarios import REGISTRY
from ..domain.models import AgentJob, Alert, Case, new_id, now
from . import ledger, policy as policies
from .catalog import catalog
from .evals import NEGATIVE
from .runtime import assess
from .tools import Toolbox
from .triage import POSITIVE
from ..visibility import system_work


class BatchError(ValueError):
    pass


def _gate(s: Session, agent: str) -> tuple[policies.Policy, Toolbox]:
    pol = policies.current(s)
    if not pol.enabled(agent):
        raise BatchError(f"{catalog()[agent].title} is switched off for this client")
    return pol, Toolbox(catalog()[agent], "L2")


def _job(s: Session, kind: str, agent: str, by: str, params: dict, result: dict, pol: policies.Policy, box: Toolbox, summary: str) -> AgentJob:
    job = AgentJob(id=new_id(kind[:4]), kind=kind, started_by=by, started=now(), finished=now(), params=params, result=result)
    s.add(job)
    spec = catalog()[agent]
    ledger.append(s, run_id=job.id, agent=agent, agent_version=spec.version, level="L2", policy_version=pol.version, summary=summary,
                  tools=box.calls, prompt=spec.goal)
    s.flush()
    return job


@system_work
def lookback(s: Session, by: str, days: int = 90, case_type: str | None = None) -> AgentJob:
    """Re-research cases closed in a period with today's agents and settings. Lists the ones worth a second look; changes none."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
        raise BatchError("Days must be a whole number between 1 and 3650")
    pol, box = _gate(s, "lookback_batch")
    since = now() - timedelta(days=days)
    q = select(Case).where(Case.closed.is_not(None), Case.closed >= since).order_by(Case.closed)
    if case_type:
        q = q.where(Case.case_type == case_type)
    cases = [c for c in s.scalars(q) if c.disposition in POSITIVE | NEGATIVE]
    box.use("closed_cases", f"{len(cases)} closed in the last {days} days")
    items, reviewed = [], 0
    for c in cases:
        a = assess(s, c, pol, decision=pol.decision)
        if not a.recommendation:
            continue
        reviewed += 1
        then = "escalate" if c.disposition in POSITIVE else "close"
        if a.recommendation != then:
            reasons = [r for r, w in a.signals if (w > 0) == (a.recommendation == "escalate")][:3]
            items.append({"case_id": c.id, "case_type": c.case_type, "party_id": c.party_id, "party_name": c.party.name,
                          "closed": c.closed.isoformat(), "outcome_then": c.disposition, "agents_now": a.recommendation,
                          "confidence": a.confidence, "reasons": reasons,
                          "concern": "closed without action, would now be escalated" if a.recommendation == "escalate"
                          else "escalated, would now be recommended for closure"})
    box.use("case_pipeline", f"{reviewed} re-researched")
    items.sort(key=lambda i: (i["agents_now"] != "escalate", -i["confidence"]))
    result = {"reviewed": reviewed, "flagged": len(items), "missed_then": sum(i["agents_now"] == "escalate" for i in items), "items": items}
    return _job(s, "lookback", "lookback_batch", by, {"days": days, "case_type": case_type}, result, pol, box,
                f"Re-researched {reviewed} closed case(s); {len(items)} worth a second look")


@system_work
def tuning(s: Session, by: str) -> AgentJob:
    """For each scenario: how much of what it raised mattered, and what a higher score cut-off would have cost. Proposes; changes nothing."""
    pol, box = _gate(s, "tuning_advisor")
    by_scenario: dict[str, list[tuple[float, bool]]] = {}
    pending: dict[str, int] = {}
    for a in s.scalars(select(Alert).where(Alert.case_id.is_not(None))):
        c = s.get(Case, a.case_id)
        if c and c.closed and c.disposition in POSITIVE | NEGATIVE:
            by_scenario.setdefault(a.scenario, []).append((a.score, c.disposition in POSITIVE))
        else:
            pending[a.scenario] = pending.get(a.scenario, 0) + 1
    box.use("alert_history", f"{sum(map(len, by_scenario.values()))} alert(s) with a known outcome")
    box.use("case_outcomes", f"{len(by_scenario)} scenario(s)")
    scenarios = []
    for name, rows in sorted(by_scenario.items()):
        n, productive = len(rows), sum(p for _, p in rows)
        options = []
        for cut in sorted({round(sc, 2) for sc, _ in rows})[1:]:
            dropped = [(sc, p) for sc, p in rows if sc < cut]
            options.append({"cutoff": cut, "alerts_removed": len(dropped), "productive_lost": sum(p for _, p in dropped),
                            "share_removed": round(len(dropped) / n, 3)})
        safe = [o for o in options if o["productive_lost"] == 0 and o["alerts_removed"] > 0]
        best = max(safe, key=lambda o: o["alerts_removed"]) if safe and n >= 5 else None
        rate = productive / n
        scenarios.append({"scenario": name, "title": REGISTRY[name]["title"] if name in REGISTRY else name.replace("_", " ").capitalize(),
                          "alerts": n, "productive": productive, "productive_rate": round(rate, 3), "awaiting_outcome": pending.get(name, 0),
                          "noisy": n >= 5 and rate < 0.5, "backtest": options[:8],
                          "proposal": None if best is None else {
                              "cutoff": best["cutoff"], "alerts_removed": best["alerts_removed"], "share_removed": best["share_removed"],
                              "text": f"Raising the cut-off to {best['cutoff']:.2f} would have removed {best['alerts_removed']} of {n} alerts "
                                      "and none that led to a filing or confirmed concern."},
                          "note": "Too few outcomes to propose anything" if n < 5 else
                                  "No higher cut-off removes alerts without losing productive ones" if best is None else ""})
    result = {"scenarios": scenarios, "proposals": sum(1 for x in scenarios if x["proposal"]),
              "caution": "A backtest on this little history is a starting point for a tuning study, not evidence for a change."}
    return _job(s, "tuning", "tuning_advisor", by, {}, result, pol, box, f"Reviewed {len(scenarios)} scenario(s); {result['proposals']} proposal(s)")


def jobs(s: Session, kind: str | None = None, limit: int = 20) -> list[dict]:
    q = select(AgentJob).order_by(AgentJob.started.desc(), AgentJob.id.desc()).limit(limit)
    if kind:
        q = q.where(AgentJob.kind == kind)
    return [{"id": j.id, "kind": j.kind, "status": j.status, "params": j.params, "result": j.result, "started_by": j.started_by,
             "started": j.started.isoformat()} for j in s.scalars(q)]
