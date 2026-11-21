"""Evaluation: agents are measured against what this client's own people decided, before they are trusted with more.

Three golden sets are built from the client's history, never from a fixed file:
  case_dispositions   closed cases: does the briefing's recommendation match the outcome a person reached?
  alert_outcomes      alerts whose case is closed: would triage have closed any alert that turned out to matter?
  screening_outcomes  screening hits whose case is closed: would the adjudicator have discounted a true match?

An evaluation is tied to a fingerprint of the agent versions and settings it covered. The policy can only let an agent
act on its own under settings whose evaluation passed.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import AgentJob, Alert, Case, new_id, now
from ..tenancy import tenant_of
from . import policy as policies
from .runtime import assess
from .triage import POSITIVE, SCREENING, match_strength, reading

NEGATIVE = {"no_sar", "false_positive", "genuine", "claim_declined", "refreshed", "onboarded"}
THRESHOLDS = {
    "case_dispositions": {"min_items": 10, "min_agreement": 0.75, "max_missed": 0.15},
    "alert_outcomes": {"min_items": 10, "max_wrongly_closed": 0},
    "screening_outcomes": {"min_items": 3, "max_wrongly_closed": 0},
}


def _closed_cases(s: Session) -> list[Case]:
    return [c for c in s.scalars(select(Case).where(Case.closed.is_not(None)).order_by(Case.created))
            if c.disposition in POSITIVE | NEGATIVE]


def case_dispositions(s: Session, pol: policies.Policy) -> dict:
    th = THRESHOLDS["case_dispositions"]
    rows, by_type = [], {}
    for c in _closed_cases(s):
        a = assess(s, c, pol, decision=pol.decision)
        if not a.recommendation:
            continue
        human = "escalate" if c.disposition in POSITIVE else "close"
        rows.append((c, human, a.recommendation))
        t = by_type.setdefault(c.case_type, {"items": 0, "agreed": 0})
        t["items"] += 1
        t["agreed"] += human == a.recommendation
    n = len(rows)
    agreed = sum(h == r for _, h, r in rows)
    positives = sum(h == "escalate" for _, h, _ in rows)
    missed = [(c, h, r) for c, h, r in rows if h == "escalate" and r == "close"]
    over = [(c, h, r) for c, h, r in rows if h == "close" and r == "escalate"]
    agreement = agreed / n if n else 0.0
    missed_rate = len(missed) / positives if positives else 0.0
    why = (f"only {n} closed case(s) to learn from; {th['min_items']} are needed" if n < th["min_items"] else
           f"agreement with people is {agreement:.0%}; {th['min_agreement']:.0%} is needed" if agreement < th["min_agreement"] else
           f"{missed_rate:.0%} of cases that people escalated would have been recommended for closure; the limit is {th['max_missed']:.0%}"
           if missed_rate > th["max_missed"] else "")
    return {"passed": not why, "why": why or f"agrees with people on {agreed} of {n} closed cases", "items": n, "agreed": agreed,
            "agreement": round(agreement, 3), "missed": len(missed), "missed_rate": round(missed_rate, 3), "over_escalated": len(over),
            "by_case_type": by_type, "thresholds": th,
            "disagreements": [{"case_id": c.id, "case_type": c.case_type, "people": c.disposition, "agents": r} for c, _, r in (missed + over)[:25]]}


def _alerts_with_outcomes(s: Session, screening: bool) -> list[tuple[Alert, Case]]:
    out = []
    for a in s.scalars(select(Alert).where(Alert.case_id.is_not(None)).order_by(Alert.created)):
        c = s.get(Case, a.case_id)
        if c and c.closed and c.disposition in POSITIVE | NEGATIVE and (a.scenario in SCREENING) == screening:
            out.append((a, c))
    return out


def _act_set(name: str, items: list[tuple[Alert, Case]], would_close) -> dict:
    th = THRESHOLDS[name]
    closes = [(a, c) for a, c in items if would_close(a)]
    wrong = [(a, c) for a, c in closes if c.disposition in POSITIVE]
    n = len(items)
    why = (f"only {n} past item(s) with a known outcome; {th['min_items']} are needed" if n < th["min_items"] else
           f"{len(wrong)} item(s) it would have closed ended in a filing or confirmed match" if len(wrong) > th["max_wrongly_closed"] else "")
    return {"passed": not why, "why": why or f"would have closed {len(closes)} of {n} past items, none of which mattered", "items": n,
            "would_close": len(closes), "wrongly_closed": len(wrong), "workload_saved": round(len(closes) / n, 3) if n else 0.0,
            "thresholds": th, "disagreements": [{"alert_id": a.id, "case_id": c.id, "score": a.score, "people": c.disposition, "agents": "close"}
                                                for a, c in wrong[:25]]}


def alert_outcomes(s: Session, pol: policies.Policy) -> dict:
    return _act_set("alert_outcomes", _alerts_with_outcomes(s, screening=False), lambda a: reading(s, a, pol.limits)["closable"])


def screening_outcomes(s: Session, pol: policies.Policy) -> dict:
    def would(a: Alert) -> bool:
        m = match_strength(s, a)
        return bool(m["screened"]) and m["similarity"] < pol.limits["discount_hit_below"] and not m["high_risk_country"]

    return _act_set("screening_outcomes", _alerts_with_outcomes(s, screening=True), would)


def copilot_questions(s: Session, pol: policies.Policy) -> dict:
    from . import copilot

    cases = [c for c in s.scalars(select(Case).where(Case.agent_recommendation.is_not(None)).order_by(Case.created).limit(12))]
    asked = cited = 0
    for c in cases:
        for q in copilot.STANDARD_QUESTIONS:
            asked += 1
            cited += bool(copilot.compose(s, c, q)["sources"])
    why = "" if asked and asked == cited else "no cases to ask about" if not asked else f"{asked - cited} answer(s) carried no source"
    return {"passed": not why, "why": why or f"all {asked} answers cite the case record", "items": asked, "cited": cited, "thresholds": {}}


SETS = {"case_dispositions": case_dispositions, "alert_outcomes": alert_outcomes, "screening_outcomes": screening_outcomes,
        "copilot_questions": copilot_questions}


def run(s: Session, by: str, body: dict | None = None) -> AgentJob:
    """Evaluate the policy in force, or a draft. Reads the client's history; changes nothing but the job record."""
    cur = policies.current(s)
    clean = policies.clean(s, body) if body is not None else cur.body
    pol = policies.Policy(cur.version, clean, tenant_of(s))
    sets = {name: fn(s, pol) for name, fn in SETS.items()}
    job = AgentJob(id=new_id("eval"), kind="evaluation", started_by=by, started=now(), finished=now(), status="done",
                   params={"fingerprint": policies.fingerprint(clean), "draft": body is not None, "policy_version": cur.version,
                           "limits": pol.limits, "decision": pol.decision},
                   result={"fingerprint": policies.fingerprint(clean), "sets": sets, "passed": all(v["passed"] for v in sets.values())})
    s.add(job)
    s.flush()
    return job


def latest(s: Session, fingerprint: str | None = None) -> dict | None:
    for job in s.scalars(select(AgentJob).where(AgentJob.kind == "evaluation").order_by(AgentJob.started.desc(), AgentJob.id.desc())):
        if fingerprint is None or job.result.get("fingerprint") == fingerprint:
            return job.result | {"job_id": job.id, "at": job.started.isoformat(), "by": job.started_by}
    return None


def challenger(s: Session) -> dict | None:
    """Champion against challenger on closed cases, both measured against what people decided."""
    pol = policies.current(s)
    if not pol.challenger:
        return None
    champion = case_dispositions(s, pol)
    alt = policies.Policy(pol.version, pol.body | {"decision": pol.challenger["decision"]}, tenant_of(s))
    contender = case_dispositions(s, alt)
    from ..domain.models import AgentRun

    live = [r for r in s.scalars(select(AgentRun)) if r.shadow and r.shadow.get("name") == pol.challenger["name"]]
    differ = [r for r in live if r.shadow["recommendation"] != r.recommendation]
    keys = ("items", "agreement", "missed", "over_escalated")
    return {"name": pol.challenger["name"], "champion": {k: champion[k] for k in keys} | {"escalate_at": pol.decision["escalate_at"]},
            "challenger": {k: contender[k] for k in keys} | {"escalate_at": pol.challenger["decision"]["escalate_at"]},
            "live_runs": len(live), "live_differences": len(differ), "differing_cases": [r.case_id for r in differ[:20]],
            "better": contender["agreement"] > champion["agreement"] and contender["missed"] <= champion["missed"]}
