"""Agents that work on an alert before it reaches a case.

At L2 the triage agent reads every alert and records what it would do. At L3 it may close a low-risk alert on its own,
and the sanctions adjudicator may discount a weak screening hit, both strictly inside the client's limits. A share of
what they close is sampled for a person to check, and a person can reopen any of it.
"""
from __future__ import annotations

import difflib
import hashlib
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import case_type_for_scenario
from ..detection.scenarios import DEMO_WATCHLIST, HIGH_RISK_COUNTRIES
from ..domain.access import Persona
from ..domain.models import Alert, Case, Party, Transaction, new_id, now
from ..tenancy import tenant_of
from . import ledger, policy as policies
from .catalog import catalog
from .tools import ToolDenied, Toolbox

POSITIVE = {"sar_filed", "fraud_confirmed", "true_match", "exit", "declined"}  # outcomes that mean the concern was real
SCREENING = {"sanctions_name"}


class TriageError(ValueError):
    pass


def _sampled(alert_id: str, rate: float) -> bool:
    """Deterministic, so the same alert is always in or out of the sample and nobody can pick."""
    return int(hashlib.sha256(alert_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < rate


def reading(s: Session, a: Alert, limits: dict) -> dict:
    """The triage agent's view of one alert. Reads only."""
    party = s.get(Party, {"tenant_id": tenant_of(s), "id": a.party_id})
    others = list(s.scalars(select(Alert).where(Alert.party_id == a.party_id, Alert.id != a.id,
                                                Alert.created >= a.created - timedelta(days=30), Alert.created <= a.created)))
    # As things stood when the alert arrived, so that replaying history gives the answer the agent would have given then.
    cases = [c for c in s.scalars(select(Case).where(Case.party_id == a.party_id, Case.created <= a.created)) if c.id != a.case_id]
    open_cases = [c for c in cases if c.closed is None or c.closed > a.created]
    confirmed = [c for c in cases if c.disposition in POSITIVE and c.closed and c.closed <= a.created]
    reasons, score = [f"Engine score {a.score:.2f} for {a.scenario.replace('_', ' ')}"], a.score
    if party.risk_rating == "high":
        reasons.append("Customer is rated high risk"); score += 0.10
    if party.pep:
        reasons.append("Customer is politically exposed"); score += 0.10
    if confirmed:
        reasons.append(f"{len(confirmed)} earlier case(s) on this customer ended in a filing or confirmed concern"); score += 0.10
    if others:
        reasons.append(f"{len(others)} other alert(s) on this customer in the last 30 days"); score += 0.05
    if open_cases:
        reasons.append(f"{len(open_cases)} case(s) already open on this customer")
    blockers = []
    if a.score >= limits["close_alert_below"]:
        blockers.append(f"score is not under {limits['close_alert_below']:.2f}")
    if party.risk_rating == "high":
        blockers.append("high-risk customer")
    if party.pep:
        blockers.append("politically exposed customer")
    if confirmed:
        blockers.append("earlier confirmed concern")
    if others or open_cases:
        blockers.append("other alerts or open cases on the customer")
    if a.scenario in SCREENING:
        blockers.append("screening hits are for the adjudicator")
    return {"score": round(min(score, 1.0), 2), "reasons": reasons, "linked_alerts": [o.id for o in others], "closable": not blockers,
            "why_not": blockers, "sources": [f"Alert {a.id}", f"Customer profile {a.party_id}", f"Case history {a.party_id}"]}


def match_strength(s: Session, a: Alert) -> dict:
    """How close the screened names are to the watchlist, for the adjudicator."""
    txns = list(s.scalars(select(Transaction).where(Transaction.id.in_(a.txn_ids)))) if a.txn_ids else []
    best, name, risky = 0.0, "", False
    for t in txns:
        for w in DEMO_WATCHLIST:
            r = difflib.SequenceMatcher(None, (t.counterparty or "").lower(), w.lower()).ratio()
            if r > best:
                best, name = r, w
        risky = risky or t.counterparty_country in HIGH_RISK_COUNTRIES
    return {"similarity": round(best, 3), "entry": name, "high_risk_country": risky, "screened": len(txns)}


def _close(a: Alert, agent: str, why: str, proposal: dict, sample: float) -> None:
    a.status, a.closed_by, a.qa_sample = "auto_closed", f"agent:{agent}", _sampled(a.id, sample)
    a.triage = proposal | {"action": "closed", "by": agent, "why": why}


def gate(s: Session, alerts: list[Alert]) -> list[Alert]:
    """Let each alert through to a case, or close it where an agent is allowed to. Returns the alerts that go on."""
    pol = policies.current(s)
    tenant, onward = tenant_of(s), []
    run_id = new_id("tri")
    for a in sorted(alerts, key=lambda x: x.created):
        case_type = case_type_for_scenario(tenant, a.scenario)["case_type"]
        closed = False
        for agent in ("triage", "sanctions_adjudicator"):
            spec, level = catalog()[agent], pol.level(agent, case_type)
            if agent == "sanctions_adjudicator" and (a.scenario not in SCREENING or level != "L3"):
                continue  # below L3 the adjudicator works inside the case, as a briefing step
            if level in ("L0", "L1"):
                continue
            box = Toolbox(spec, level)
            if agent == "triage":
                for tool in spec.tools:
                    box.use(tool, a.party_id)
                view = reading(s, a, pol.limits)
                ok = view["closable"]
                why = (f"Scored {a.score:.2f}, under the client's limit of {pol.limits['close_alert_below']:.2f}, with nothing else of "
                       "concern on the customer") if ok else ""
                action = "close_alert"
            else:
                box.use("transactions", f"{len(a.txn_ids)} screened")
                box.use("watchlist", f"{len(DEMO_WATCHLIST)} entries")
                m = match_strength(s, a)
                ok = bool(m["screened"]) and m["similarity"] < pol.limits["discount_hit_below"] and not m["high_risk_country"]
                why = (f"Best name similarity {m['similarity']:.0%} against '{m['entry']}', under the client's limit of "
                       f"{pol.limits['discount_hit_below']:.0%}, and no high-risk country involved") if ok else ""
                view = (a.triage or {}) | {"match": m, "sources": [f"Alert {a.id}", f"Watchlist entry {m['entry']}"]}
                action = "discount_hit"
            outcome = "done"
            if ok and level == "L3":
                try:
                    box.act(action, a.id)
                    _close(a, agent, why, view, pol.limits["qa_sample"])
                    outcome, closed = "acted", True
                except ToolDenied as e:  # declared limits say no: the alert goes to a person
                    outcome, why = "refused", str(e)
            if not closed:
                a.triage = view | {"action": "proposed_close" if ok else "routed", "by": agent, "why": why}
            ledger.append(s, run_id=run_id, alert_id=a.id, agent=agent, agent_version=spec.version, level=level, policy_version=pol.version,
                          outcome=outcome, summary=why or "Sent on to a case", tools=box.calls, prompt=spec.goal, ts=a.created,
                          findings=[{"text": r, "cites": [], "sources": view.get("sources", [])} for r in view.get("reasons", [])])
            if closed:
                break
        if not closed:
            onward.append(a)
    s.flush()
    return onward


def closed_by_agents(s: Session, limit: int = 200) -> list[dict]:
    rows = s.scalars(select(Alert).where(Alert.closed_by.is_not(None)).order_by(Alert.created.desc()).limit(limit))
    return [{"id": a.id, "scenario": a.scenario, "party_id": a.party_id, "party_name": a.party.name, "score": a.score,
             "created": a.created.isoformat(), "status": a.status, "closed_by": a.closed_by, "why": (a.triage or {}).get("why", ""),
             "explanation": a.explanation, "qa_sample": a.qa_sample, "qa_by": a.qa_by, "qa_result": a.qa_result, "case_id": a.case_id}
            for a in rows]


def review(s: Session, alert_id: str, persona: Persona, confirm: bool) -> Alert:
    """A person checks an alert an agent closed: agree, or reopen it into a case."""
    from ..alerts.hub import route_alerts

    a = s.get(Alert, alert_id)
    if a is None or a.closed_by is None:
        raise TriageError("That alert was not closed by an agent")
    if a.qa_result:
        raise TriageError("That alert has already been checked")
    a.qa_by, a.qa_result = persona.id, "confirmed" if confirm else "reopened"
    agent = a.closed_by.split(":", 1)[1]
    ledger.append(s, run_id=new_id("qa"), alert_id=a.id, agent=agent, agent_version=catalog()[agent].version, level="L3",
                  policy_version=policies.current(s).version, outcome="done", ts=now(),
                  summary=f"{persona.name} {'agreed with the closure' if confirm else 'disagreed and reopened the alert'}")
    if not confirm:
        a.status = "new"
        route_alerts(s, [a], screened=True)
    return a
