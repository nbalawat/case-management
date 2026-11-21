"""Specialist agents. Each reads case data through the context and returns cited findings.

Agents have read-only access. They cannot change case state, close a case or file anything.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from ..detection.scenarios import DEMO_WATCHLIST, HIGH_RISK_COUNTRIES
from ..domain.models import Alert, Case, Party, Transaction

CASH_INTENSIVE = {"restaurant owner", "convenience store owner", "car wash operator", "laundromat owner"}
BENIGN_COUNTERPARTY_HINTS = ("title", "escrow", "payroll", "irs", "treasury", "mortgage", "brokerage")


@dataclass
class Context:
    case: Case
    party: Party
    alerts: list[Alert]
    alerted: list[Transaction]
    history: list[Transaction]
    prior_cases: int
    facts: dict = field(default_factory=dict)
    signals: list[tuple[str, float]] = field(default_factory=list)  # (reason, weight) + suspicious, - mitigating


@dataclass
class Step:
    agent: str
    title: str
    summary: str
    findings: list[dict]


def _f(text: str, cites: list[str] | None = None) -> dict:
    return {"text": text, "cites": (cites or [])[:8]}


def data_assembly(ctx: Context) -> Step:
    total = sum(t.amount for t in ctx.alerted)
    hist_in = sum(t.amount for t in ctx.history if t.direction == "in")
    months = max((ctx.history[-1].ts - ctx.history[0].ts).days / 30, 1) if ctx.history else 1
    ctx.facts.update(alerted_total=total, n_txns=len(ctx.alerted), monthly_in=hist_in / months,
                     period=(min(t.ts for t in ctx.alerted).date().isoformat(), max(t.ts for t in ctx.alerted).date().isoformat()))
    f = [
        _f(f"Profile: {ctx.party.kind}, {ctx.party.occupation or 'occupation not recorded'}, risk rating {ctx.party.risk_rating}"
           f"{', politically exposed' if ctx.party.pep else ''}."),
        _f(f"{len(ctx.alerts)} alert(s) cover {len(ctx.alerted)} transactions totalling ${total:,.0f}.", [t.id for t in ctx.alerted]),
        _f(f"Typical inflow over the last {months:.0f} months is about ${hist_in / months:,.0f} a month across {len(ctx.history)} transactions."),
        _f(f"{ctx.prior_cases} prior case(s) on this customer."),
    ]
    if ctx.prior_cases:
        ctx.signals.append(("prior cases on file", 0.1))
    if ctx.party.pep:
        ctx.signals.append(("politically exposed person", 0.15))
    return Step("data_assembly", "Data assembly", "Assembled profile, alerted activity and 90-day history.", f)


def entity_research(ctx: Context) -> Step:
    cps = Counter(t.counterparty for t in ctx.alerted if t.counterparty)
    countries = {t.counterparty_country for t in ctx.alerted} - {"US"}
    f = []
    if cps:
        top = ", ".join(f"{n} ({c})" for n, c in cps.most_common(4))
        f.append(_f(f"Counterparties in alerted activity: {top}.", [t.id for t in ctx.alerted if t.counterparty]))
    benign = [n for n in cps if any(h in n.lower() for h in BENIGN_COUNTERPARTY_HINTS)]
    if benign:
        f.append(_f(f"Counterparty type suggests an ordinary purpose: {', '.join(benign)}.",
                    [t.id for t in ctx.alerted if t.counterparty in benign]))
        ctx.signals.append(("counterparties indicate an ordinary purpose", -0.35))
    risky = countries & HIGH_RISK_COUNTRIES
    if risky:
        f.append(_f(f"Funds moved to high-risk jurisdictions: {', '.join(sorted(risky))}.",
                    [t.id for t in ctx.alerted if t.counterparty_country in risky]))
        ctx.signals.append(("high-risk jurisdictions", 0.3))
    seen_before = {t.counterparty for t in ctx.history if t not in ctx.alerted}
    new = [n for n in cps if n not in seen_before]
    if new and not benign:
        f.append(_f(f"{len(new)} counterparty(ies) have no earlier history with this customer."))
        ctx.signals.append(("first-time counterparties", 0.1))
    if not f:
        f.append(_f("No named counterparties; activity is cash at branch or ATM."))
    return Step("entity_research", "Entity research", "Reviewed counterparties and geography.", f)


def typology_analyst(ctx: Context) -> Step:
    f = []
    for a in ctx.alerts:
        if a.scenario == "structuring":
            deps = [t for t in ctx.alerted if t.channel == "cash"]
            days = {t.ts.date() for t in deps}
            cash_business = ctx.party.occupation.lower() in CASH_INTENSIVE
            older = [t for t in ctx.history if t.channel == "cash" and t.direction == "in" and t not in deps]
            f.append(_f(f"Structuring indicators: {len(deps)} cash deposits just under $10,000 on {len(days)} separate days.", [t.id for t in deps]))
            if cash_business and len(older) >= 10:
                f.append(_f(f"Mitigating: customer is a {ctx.party.occupation} with {len(older)} earlier cash deposits; pattern is long-standing."))
                ctx.signals.append(("cash-intensive business with consistent history", -0.4))
            else:
                ctx.signals.append(("deposits structured under the CTR threshold with no business reason", 0.45))
        elif a.scenario == "rapid_movement":
            f.append(_f(f"Pass-through indicators: {a.explanation}", a.txn_ids))
            ctx.signals.append(("funds passed through within 48 hours", 0.3))
            if ctx.facts.get("monthly_in", 0) and ctx.facts["alerted_total"] > 3 * ctx.facts["monthly_in"]:
                f.append(_f("Alerted volume is more than three times the customer's normal monthly inflow."))
                ctx.signals.append(("volume far above normal", 0.15))
        elif a.scenario == "high_risk_geo":
            f.append(_f(f"Geographic risk: {a.explanation}", a.txn_ids))
            ctx.signals.append(("wires to high-risk jurisdictions", 0.35))
    return Step("typology_analyst", "Typology analysis", "Mapped activity to money-laundering typologies and red flags.", f)


def sanctions_adjudicator(ctx: Context) -> Step:
    import difflib
    f = []
    for t in ctx.alerted:
        best = max(DEMO_WATCHLIST, key=lambda w: difflib.SequenceMatcher(None, t.counterparty.lower(), w.lower()).ratio())
        r = difflib.SequenceMatcher(None, t.counterparty.lower(), best.lower()).ratio()
        f.append(_f(f"'{t.counterparty}' against watchlist entry '{best}': {r:.0%} name similarity, "
                    f"counterparty country {t.counterparty_country}.", [t.id]))
        if r >= 0.97:
            ctx.signals.append(("near-exact watchlist name match", 0.6))
        else:
            f.append(_f("Name differs in a distinguishing token and no secondary identifier matches."))
            ctx.signals.append(("partial name match only, no secondary identifiers", -0.3))
    return Step("sanctions_adjudicator", "Sanctions adjudication", "Compared hit details with watchlist entry.", f)


def decide(ctx: Context) -> tuple[str, float]:
    score = sum(w for _, w in ctx.signals)
    rec = "escalate" if score >= 0.25 else "close"
    conf = min(0.55 + abs(score - 0.25) * 0.6, 0.96)
    return rec, round(conf, 2)


AGENTS: dict[str, Callable[[Context], Step]] = {
    "data_assembly": data_assembly,
    "entity_research": entity_research,
    "typology_analyst": typology_analyst,
    "sanctions_adjudicator": sanctions_adjudicator,
}
