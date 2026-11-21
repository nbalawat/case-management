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
from .tools import Extract, Toolbox

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
    media: list[Extract] = field(default_factory=list)  # outside content, already through the extractor
    tools: Toolbox | None = None  # the toolbox of the agent whose turn it is

    def use(self, tool: str, detail: str = "") -> None:
        if self.tools is not None:
            self.tools.use(tool, detail)

    @property
    def watchlist(self) -> list[str]:
        """The names on the client's screening lists in force."""
        from sqlalchemy.orm import object_session

        from ..sanctions import lists

        session = object_session(self.case)
        return lists.names(session) if session is not None else list(DEMO_WATCHLIST)

    @property
    def profile(self) -> str:
        return f"Customer profile {self.party.id}"

    @property
    def record(self) -> str:
        return f"Account history {self.party.id}"


@dataclass
class Step:
    agent: str
    title: str
    summary: str
    findings: list[dict]
    extra: dict = field(default_factory=dict)


def _f(text: str, cites: list[str] | None = None, sources: list[str] | None = None) -> dict:
    """A finding. `cites` are transaction ids; `sources` name any other record it rests on. One or the other is required:
    the runtime drops a finding that points at nothing."""
    return {"text": text, "cites": (cites or [])[:8], "sources": sources or []}


def data_assembly(ctx: Context) -> Step:
    total = sum(t.amount for t in ctx.alerted)
    hist_in = sum(t.amount for t in ctx.history if t.direction == "in")
    months = max((ctx.history[-1].ts - ctx.history[0].ts).days / 30, 1) if ctx.history else 1
    ctx.facts.update(alerted_total=total, n_txns=len(ctx.alerted), monthly_in=hist_in / months,
                     period=(min(t.ts for t in ctx.alerted).date().isoformat(), max(t.ts for t in ctx.alerted).date().isoformat()))
    ctx.use("customer_profile", ctx.party.id)
    ctx.use("transactions", f"{len(ctx.alerted)} alerted, {len(ctx.history)} in the last 120 days")
    ctx.use("prior_cases", str(ctx.prior_cases))
    f = [
        _f(f"Profile: {ctx.party.kind}, {ctx.party.occupation or 'occupation not recorded'}, risk rating {ctx.party.risk_rating}"
           f"{', politically exposed' if ctx.party.pep else ''}.", sources=[ctx.profile]),
        _f(f"{len(ctx.alerts)} alert(s) cover {len(ctx.alerted)} transactions totalling ${total:,.0f}.", [t.id for t in ctx.alerted]),
        _f(f"Typical inflow over the last {months:.0f} months is about ${hist_in / months:,.0f} a month across {len(ctx.history)} transactions.",
           sources=[ctx.record]),
        _f(f"{ctx.prior_cases} prior case(s) on this customer.", sources=[f"Case history {ctx.party.id}"]),
    ]
    if ctx.prior_cases:
        ctx.signals.append(("prior cases on file", 0.1))
    if ctx.party.pep:
        ctx.signals.append(("politically exposed person", 0.15))
    return Step("data_assembly", "Data assembly", "Assembled profile, alerted activity and 90-day history.", f)


def entity_research(ctx: Context) -> Step:
    ctx.use("transactions", f"{len(ctx.alerted)} alerted")
    ctx.use("counterparty_history", f"{len(ctx.history)} earlier transactions")
    ctx.use("country_risk")
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
        f.append(_f(f"{len(new)} counterparty(ies) have no earlier history with this customer.",
                    [t.id for t in ctx.alerted if t.counterparty in new], [ctx.record]))
        ctx.signals.append(("first-time counterparties", 0.1))
    if not f:
        f.append(_f("No named counterparties; activity is cash at branch or ATM.", [t.id for t in ctx.alerted], [ctx.record]))
    return Step("entity_research", "Entity research", "Reviewed counterparties and geography.", f)


def typology_analyst(ctx: Context) -> Step:
    ctx.use("transactions", f"{len(ctx.alerted)} alerted")
    ctx.use("typology_library", ", ".join(sorted({a.scenario for a in ctx.alerts})))
    f = []
    for a in ctx.alerts:
        if a.scenario == "structuring":
            deps = [t for t in ctx.alerted if t.channel == "cash"]
            days = {t.ts.date() for t in deps}
            cash_business = ctx.party.occupation.lower() in CASH_INTENSIVE
            older = [t for t in ctx.history if t.channel == "cash" and t.direction == "in" and t not in deps]
            f.append(_f(f"Structuring indicators: {len(deps)} cash deposits just under $10,000 on {len(days)} separate days.", [t.id for t in deps]))
            if cash_business and len(older) >= 10:
                f.append(_f(f"Mitigating: customer is a {ctx.party.occupation} with {len(older)} earlier cash deposits; pattern is long-standing.",
                            [t.id for t in older], [ctx.profile]))
                ctx.signals.append(("cash-intensive business with consistent history", -0.4))
            else:
                ctx.signals.append(("deposits structured under the CTR threshold with no business reason", 0.45))
        elif a.scenario == "rapid_movement":
            f.append(_f(f"Pass-through indicators: {a.explanation}", a.txn_ids))
            ctx.signals.append(("funds passed through within 48 hours", 0.3))
            if ctx.facts.get("monthly_in", 0) and ctx.facts["alerted_total"] > 3 * ctx.facts["monthly_in"]:
                f.append(_f("Alerted volume is more than three times the customer's normal monthly inflow.", a.txn_ids, [ctx.record]))
                ctx.signals.append(("volume far above normal", 0.15))
        elif a.scenario == "high_risk_geo":
            f.append(_f(f"Geographic risk: {a.explanation}", a.txn_ids))
            ctx.signals.append(("wires to high-risk jurisdictions", 0.35))
    return Step("typology_analyst", "Typology analysis", "Mapped activity to money-laundering typologies and red flags.", f)


def fraud_analyst(ctx: Context) -> Step:
    ctx.use("transactions", f"{len(ctx.alerted)} disputed")
    ctx.use("counterparty_history", f"{len(ctx.history)} earlier transactions")
    f = []
    night = [t for t in ctx.alerted if t.ts.hour < 6]
    usual = [t.amount for t in ctx.history if t.direction == "out" and t not in ctx.alerted]
    typical = sorted(usual)[len(usual) // 2] if usual else 0
    seen = {t.counterparty for t in ctx.history if t not in ctx.alerted}
    new = [t for t in ctx.alerted if t.counterparty and t.counterparty not in seen]
    abroad = [t for t in ctx.alerted if t.counterparty_country != ctx.party.country]
    if new:
        f.append(_f(f"{len(new)} of {len(ctx.alerted)} disputed payments went to payees the customer has never paid before.", [t.id for t in new]))
        ctx.signals.append(("payments to first-time payees", 0.25))
    if night:
        f.append(_f(f"{len(night)} payment(s) were made between midnight and 6 AM, outside the customer's usual hours.", [t.id for t in night]))
        ctx.signals.append(("activity at unusual hours", 0.15))
    if typical and ctx.alerted:
        biggest = max(t.amount for t in ctx.alerted)
        if biggest > 5 * typical:
            f.append(_f(f"Largest disputed payment is ${biggest:,.0f}, against a typical payment of ${typical:,.0f}.",
                        [t.id for t in ctx.alerted if t.amount == biggest], [ctx.record]))
            ctx.signals.append(("amounts far above the customer's normal spending", 0.2))
    if abroad:
        f.append(_f(f"{len(abroad)} payment(s) went to payees outside {ctx.party.country}.", [t.id for t in abroad]))
        ctx.signals.append(("payees abroad", 0.1))
    if not new and not night:
        f.append(_f("Disputed payments went to payees and at times that match the customer's own history.",
                    [t.id for t in ctx.alerted], [ctx.record]))
        ctx.signals.append(("matches the customer's own payment pattern", -0.3))
    return Step("fraud_analyst", "Fraud analysis", "Compared the disputed payments with the customer's normal behaviour.", f)


def sanctions_adjudicator(ctx: Context) -> Step:
    import difflib
    ctx.use("transactions", f"{len(ctx.alerted)} screened")
    listed = ctx.watchlist
    ctx.use("watchlist", f"{len(listed)} entries")
    f = []
    if not ctx.alerted:  # the customer's own name matched, not a payment
        f.append(_f(f"The hit is on the customer's own name, '{ctx.party.name}', not on a payment. Compare identifiers with the list entry.", [], [ctx.profile]))
    for t in ctx.alerted:
        best = max(listed, key=lambda w: difflib.SequenceMatcher(None, t.counterparty.lower(), w.lower()).ratio())
        r = difflib.SequenceMatcher(None, t.counterparty.lower(), best.lower()).ratio()
        f.append(_f(f"'{t.counterparty}' against watchlist entry '{best}': {r:.0%} name similarity, "
                    f"counterparty country {t.counterparty_country}.", [t.id]))
        if r >= 0.97:
            ctx.signals.append(("near-exact watchlist name match", 0.6))
        else:
            f.append(_f("Name differs in a distinguishing token and no secondary identifier matches.", [t.id], [f"Watchlist entry {best}"]))
            ctx.signals.append(("partial name match only, no secondary identifiers", -0.3))
    return Step("sanctions_adjudicator", "Sanctions adjudication", "Compared hit details with watchlist entry.", f)


def adverse_media(ctx: Context) -> Step:
    """Works only from what the extractor returned. The documents themselves never reach this function."""
    ctx.use("media_search", f"{len(ctx.media)} document(s) found")
    ctx.use("extractor", f"{len(ctx.media)} document(s) reduced to typed fields")
    f, removed = [], sum(e.instructions_removed for e in ctx.media)
    relevant = [e for e in ctx.media if e.names_subject and e.category]
    for e in relevant:
        label = {"financial_crime": "Financial crime", "regulatory": "Regulatory action", "litigation": "Litigation"}[e.category]
        f.append(_f(f"{label} coverage, {e.source}, {e.published}: \"{e.quote}\"", sources=[f"Media {e.doc_id}"]))
    serious = [e for e in relevant if e.category == "financial_crime"]
    if serious:
        ctx.signals.append(("adverse media naming the customer in connection with financial crime", 0.3))
    elif relevant:
        ctx.signals.append(("adverse media naming the customer", 0.1))
    other = len(ctx.media) - len(relevant)
    if other:
        f.append(_f(f"{other} other document(s) either do not name the customer or carry nothing adverse.",
                    sources=[f"Media {e.doc_id}" for e in ctx.media if e not in relevant]))
    if not ctx.media:
        f.append(_f("No news or registry entries found for this customer.", sources=[f"Media search {ctx.party.id}"]))
    if removed:
        f.append(_f(f"{removed} sentence(s) in the source material were addressed to the reader as instructions. They were removed "
                    "before analysis and had no effect on this briefing.", sources=[f"Media {e.doc_id}" for e in ctx.media if e.instructions_removed]))
    return Step("adverse_media", "Adverse media", "Searched news and registries through the extractor.", f, {"instructions_removed": removed})


def kyc_refresh(ctx: Context) -> Step:
    """Compares what the bank holds about the customer with what the account shows, and drafts the request to the customer."""
    ctx.use("customer_profile", ctx.party.id)
    ctx.use("transactions", f"{len(ctx.history)} in the last 120 days")
    ctx.use("prior_cases", str(ctx.prior_cases))
    p, f, asks = ctx.party, [], []
    as_of = max((t.ts for t in ctx.history), default=ctx.case.created)
    years = (as_of - p.onboarded).days / 365
    f.append(_f(f"Customer since {p.onboarded.date().isoformat()} ({years:.1f} years); rated {p.risk_rating} risk.", sources=[ctx.profile]))
    if not p.occupation:
        f.append(_f("No occupation or business activity is recorded.", sources=[ctx.profile]))
        asks.append("your current occupation or the nature of your business")
    abroad = sorted({t.counterparty_country for t in ctx.history} - {p.country, ""})
    risky = sorted(set(abroad) & HIGH_RISK_COUNTRIES)
    if abroad:
        cites = [t.id for t in ctx.history if t.counterparty_country in abroad]
        f.append(_f(f"Payments to or from {len(abroad)} other countr{'y' if len(abroad) == 1 else 'ies'}: {', '.join(abroad[:6])}.", cites, [ctx.record]))
        asks.append(f"the purpose of your payments involving {', '.join(abroad[:4])}")
    if risky:
        ctx.signals.append(("activity with high-risk jurisdictions not explained by the profile", 0.2))
    cash = [t for t in ctx.history if t.channel == "cash"]
    if ctx.history and len(cash) / len(ctx.history) > 0.4 and p.occupation.lower() not in CASH_INTENSIVE:
        f.append(_f(f"{len(cash)} of {len(ctx.history)} recent transactions are cash, which the recorded occupation does not explain.",
                    [t.id for t in cash], [ctx.profile]))
        asks.append("the source of the cash paid into your account")
        ctx.signals.append(("cash use not explained by the profile", 0.15))
    if p.pep:
        f.append(_f("Politically exposed: enhanced due diligence and senior approval apply.", sources=[ctx.profile]))
        asks.append("your current public role and your sources of wealth")
    if ctx.prior_cases:
        f.append(_f(f"{ctx.prior_cases} earlier case(s) should be read alongside this review.", sources=[f"Case history {p.id}"]))
    asks = ["current identification and proof of address"] + asks
    draft = (f"Dear {p.name},\n\nWe are carrying out a periodic review of your account. Please send us: "
             + "; ".join(asks) + ".\n\nYou can reply through secure messaging. Thank you for your help.")
    f.append(_f(f"Drafted a request to the customer covering {len(asks)} item(s). A person sends it.", sources=[ctx.profile]))
    if len(asks) == 1 and not risky:
        ctx.signals.append(("profile and activity are consistent", -0.2))
    return Step("kyc_refresh", "KYC review", "Compared the profile on file with account activity.", f, {"draft_request": draft})


def decide(ctx: Context, escalate_at: float = 0.25) -> tuple[str, float]:
    score = sum(w for _, w in ctx.signals)
    rec = "escalate" if score >= escalate_at else "close"
    conf = min(0.55 + abs(score - escalate_at) * 0.6, 0.96)
    return rec, round(conf, 2)


AGENTS: dict[str, Callable[[Context], Step]] = {
    "data_assembly": data_assembly,
    "entity_research": entity_research,
    "typology_analyst": typology_analyst,
    "sanctions_adjudicator": sanctions_adjudicator,
    "fraud_analyst": fraud_analyst,
    "adverse_media": adverse_media,
    "kyc_refresh": kyc_refresh,
}
