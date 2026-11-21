"""The objects Ask can read, beyond cases. Each one is its vocabulary, its scope rule and who may read it.

Every object that names a customer carries the same customer fields (risk rating, PEP, country, and figures from
that customer's cases), so a question can cross from transactions to customers to cases to filings with filters
alone, and the model can chain several lookups with `in` filters on the id fields. Case-derived figures count
only cases the asker may see.

Adding an object is adding an entry here; the tools, the API and the MCP server pick it up.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import and_, case, exists, func, select, true
from sqlalchemy.orm import aliased

from ...domain.access import Persona
from ...domain.models import (Account, AgentRun, AgentStep, Alert, Case, DataSource, Dispute, Filing, Hold, KycTrigger, Lookback, Party,
                              PipelineRun, RiskAssessment, ScenarioVersion, Transaction)
from ...reporting.metrics import ESCALATED
from .query import Dim, Measure, Obj, Q, count_if, days_between, now_sql, share
from .scope import Scope


# --- shared pieces ---------------------------------------------------------------------
def _party(base_col):
    return (Party, lambda b, a: a.id == getattr(b, base_col))


def _case(base_col="case_id"):
    return (Case, lambda b, a: a.id == getattr(b, base_col))


def customer_dims(via: str | None = "party", pid=lambda q: q.base.party_id) -> list[Dim]:
    """The customer fields every customer-linked object offers. `via` is the join to the party table, or None on Party itself;
    `pid` gives the customer id column."""
    def p(q: Q):
        return q.j(via) if via else q.base

    if via is None:
        pid = lambda q: q.base.id  # noqa: E731
    return [
        Dim("customer_id", "Customer id", "text", pid, group=False),
        Dim("customer", "Customer name", "text", lambda q: p(q).name, group=False),
        Dim("customer_risk_rating", "Customer risk rating", "text", lambda q: p(q).risk_rating, "low, medium or high"),
        Dim("customer_pep", "Customer is politically exposed", "bool", lambda q: p(q).pep),
        Dim("customer_country", "Customer country", "text", lambda q: p(q).country, "two-letter code"),
        Dim("customer_kind", "Customer kind", "text", lambda q: p(q).kind, "person or business"),
        Dim("customer_open_cases", "Customer's open cases", "number", lambda q: _open_cases(q, pid(q)), "cases the asker can see", group=False, show=False),
        Dim("customer_has_sar", "Customer has a SAR filed on a case", "bool",
            lambda q: _case_count(q, pid(q), _disp("sar_filed")) > 0, "from cases the asker can see", show=False),
    ]


def _open_cases(q: Q, party):
    c = aliased(Case)
    return select(func.count()).select_from(c).where(c.party_id == party, c.tenant_id == q.base.tenant_id, c.closed.is_(None), q.scope.case_ok(c.id)) \
        .correlate(q.base).scalar_subquery()


def _disp(value):
    """A condition on the inner case alias of _case_count, built lazily because the alias is created there."""
    return _DispMarker(value)


class _DispMarker:
    def __init__(self, value):
        self.value = value


def _case_count(q: Q, party, *conds):
    """How many cases this customer has that the asker may see, with extra conditions."""
    c = aliased(Case)
    built = [c.disposition == x.value if isinstance(x, _DispMarker) else x for x in conds]
    return select(func.count()).select_from(c).where(c.party_id == party, c.tenant_id == q.base.tenant_id, q.scope.case_ok(c.id), *built) \
        .correlate(q.base).scalar_subquery()


def case_dims(via: str = "case") -> list[Dim]:
    return [
        Dim("case_id", "Case id", "text", lambda q: q.base.case_id, group=False),
        Dim("case_type", "Case type", "text", lambda q: q.j(via).case_type, "the case type id, for example fraud_claim or sanctions_hit"),
        Dim("case_open", "Its case is open", "bool", lambda q: and_(q.j(via).id.is_not(None), q.j(via).closed.is_(None)), show=False),
    ]


def month(label: str, col_of):
    return Dim(f"{label}_month", f"Month {label.replace('_', ' ')}", "text", lambda q: q.month(col_of(q)), "YYYY-MM")


def needs(*perms: str):
    def check(p: Persona) -> str | None:
        if any(p.can(x) for x in perms):
            return None
        words = " or ".join(f"'{x.replace('_', ' ')}'" for x in perms)
        return f"Reading these needs the {words} permission, which the {p.title} role does not hold."
    return check


def objects() -> dict[str, Obj]:
    return _objects()


@lru_cache(maxsize=1)
def _objects() -> dict[str, Obj]:
    T, A, P, F, H, D, R, K, S, G, V, L, PR = (Transaction, Alert, Party, Filing, Hold, Dispute, RiskAssessment, KycTrigger, AgentStep, AgentRun,
                                              ScenarioVersion, Lookback, PipelineRun)
    out = [
        Obj("transactions", "transactions", "Payments in and out of customers' accounts, from the client's own systems.", T, "transaction", "id",
            {d.id: d for d in [
                Dim("id", "Transaction id", "text", lambda q: q.base.id, group=False),
                Dim("date", "Date", "date", lambda q: q.base.ts, group=False),
                month("booked", lambda q: q.base.ts),
                Dim("amount", "Amount", "number", lambda q: q.base.amount, "in the transaction's currency", group=False),
                Dim("currency", "Currency", "text", lambda q: q.base.currency),
                Dim("direction", "Direction", "text", lambda q: q.base.direction, "in or out"),
                Dim("channel", "Channel", "text", lambda q: q.base.channel, "cash, wire, ach, card or p2p"),
                Dim("counterparty", "Counterparty", "text", lambda q: q.base.counterparty),
                Dim("counterparty_country", "Counterparty country", "text", lambda q: q.base.counterparty_country, "two-letter code"),
                Dim("account_id", "Account id", "text", lambda q: q.base.account_id, group=False),
                Dim("product", "Account product", "text", lambda q: q.j("account").product),
                *customer_dims(),
            ]},
            {m.id: m for m in [
                Measure("transactions", "Number of transactions", lambda q: func.count()),
                Measure("total_amount", "Total amount", lambda q: func.sum(q.base.amount), "usd"),
                Measure("avg_amount", "Average amount", lambda q: func.avg(q.base.amount), "usd"),
                Measure("max_amount", "Largest amount", lambda q: func.max(q.base.amount), "usd"),
                Measure("customers", "Number of customers", lambda q: func.count(func.distinct(q.base.party_id))),
                Measure("counterparties", "Number of counterparties", lambda q: func.count(func.distinct(q.base.counterparty))),
                Measure("share", "Share of the matching transactions, in percent", lambda q: 100.0 * func.count() / func.nullif(func.sum(func.count()).over(), 0), "percent"),
            ]},
            lambda sc, q: sc.party_ok(q.base.party_id), ("id", "date", "customer", "amount", "currency", "direction", "channel", "counterparty",
                                                         "counterparty_country"), "date",
            joins={"party": _party("party_id"), "account": (Account, lambda b, a: a.id == b.account_id)}),

        Obj("alerts", "alerts", "Alerts raised by detection (monitoring scenarios, sanctions screening, fraud rules), whether or not they became cases.",
            A, "alert", "id",
            {d.id: d for d in [
                Dim("id", "Alert id", "text", lambda q: q.base.id, group=False),
                Dim("scenario", "Scenario", "text", lambda q: q.base.scenario, "for example structuring, rapid_movement, sanctions_name"),
                Dim("status", "Status", "text", lambda q: q.base.status, "new, in_case, hibernated, auto_closed, closed"),
                Dim("score", "Score", "number", lambda q: q.base.score, "0 to 1", group=False),
                Dim("source", "Source system", "text", lambda q: q.base.source),
                Dim("raised", "Raised", "date", lambda q: q.base.created, group=False),
                month("raised", lambda q: q.base.created),
                Dim("in_case", "Became a case", "bool", lambda q: q.base.case_id.is_not(None)),
                Dim("closed_by_agent", "Closed by an agent", "bool", lambda q: q.base.closed_by.is_not(None)),
                Dim("qa_sample", "Picked for a QA check", "bool", lambda q: q.base.qa_sample),
                Dim("qa_result", "QA result", "text", lambda q: q.base.qa_result, "confirmed or reopened"),
                *case_dims(), *customer_dims(),
            ]},
            {m.id: m for m in [
                Measure("alerts", "Number of alerts", lambda q: func.count()),
                Measure("customers", "Number of customers", lambda q: func.count(func.distinct(q.base.party_id))),
                Measure("avg_score", "Average score", lambda q: func.avg(q.base.score), "score"),
                Measure("to_case_rate", "Share that became cases, in percent", lambda q: share(q.base.case_id.is_not(None)), "percent"),
                Measure("auto_closed", "Closed by an agent", lambda q: count_if(q.base.status == "auto_closed")),
                Measure("reopened_by_qa", "Reopened by a QA check", lambda q: count_if(q.base.qa_result == "reopened")),
            ]},
            lambda sc, q: sc.case_or_party_ok(q.base.case_id, q.base.party_id), ("id", "raised", "scenario", "status", "score", "customer", "case_id"),
            "raised", joins={"party": _party("party_id"), "case": _case()}, case_dim="case_id"),

        Obj("customers", "customers", "The client's customers: profile, risk and figures from their cases.", P, "customer", "customer_id",
            {d.id: d for d in [
                *customer_dims(via=None),
                Dim("occupation", "Occupation or business", "text", lambda q: q.base.occupation),
                Dim("customer_since", "Customer since", "date", lambda q: q.base.onboarded, group=False),
                month("onboarded", lambda q: q.base.onboarded),
                Dim("cases", "Cases", "number", lambda q: _case_count(q, q.base.id), "cases the asker can see", group=False),
                Dim("kyc_band", "Current KYC risk band", "text", lambda q: select(R.final_band).where(R.party_id == q.base.id, R.current.is_(True),
                                                                                                   R.tenant_id == q.base.tenant_id).limit(1).scalar_subquery()),
                Dim("active_holds", "Active holds", "number", lambda q: select(func.count()).select_from(H).where(
                    H.party_id == q.base.id, H.status == "active", H.tenant_id == q.base.tenant_id, q.scope.case_ok(H.case_id)).scalar_subquery(), group=False),
                Dim("transactions_90d", "Transactions in the last 90 days", "number", lambda q: select(func.count()).select_from(T).where(
                    T.party_id == q.base.id, T.tenant_id == q.base.tenant_id, T.ts >= _days_ago(90)).scalar_subquery(), group=False),
            ]},
            {m.id: m for m in [
                Measure("customers", "Number of customers", lambda q: func.count()),
                Measure("with_open_cases", "Customers with an open case", lambda q: count_if(_open_cases(q, q.base.id) > 0)),
                Measure("with_sar", "Customers with a SAR filed", lambda q: count_if(_case_count(q, q.base.id, _disp("sar_filed")) > 0)),
                Measure("pep", "Politically exposed customers", lambda q: count_if(q.base.pep.is_(True))),
                Measure("share", "Share of the matching customers, in percent", lambda q: 100.0 * func.count() / func.nullif(func.sum(func.count()).over(), 0), "percent"),
            ]},
            lambda sc, q: sc.party_ok(q.base.id), ("customer_id", "customer", "customer_kind", "customer_country", "customer_risk_rating", "customer_pep",
                                                   "occupation", "customer_since", "cases", "kyc_band"), "customer_since"),

        Obj("filings", "regulatory filings", "Reports to regulators (SAR, STR, CTR, blocking reports): their state, deadlines and answers.", F, "filing", "id",
            {d.id: d for d in [
                Dim("id", "Filing id", "text", lambda q: q.base.id, group=False),
                Dim("kind", "Kind", "text", lambda q: q.base.kind, "SAR, STR, CTR or another report"),
                Dim("status", "Status", "text", lambda q: q.base.status, "prepared, submitted, acknowledged, rejected, superseded"),
                Dim("prepared", "Prepared", "date", lambda q: q.base.filed_at, group=False),
                month("prepared", lambda q: q.base.filed_at),
                Dim("due", "Due", "date", lambda q: q.base.due, group=False),
                Dim("submitted", "Submitted", "date", lambda q: q.base.submitted_at, group=False),
                Dim("past_due", "Past its deadline and not sent", "bool", lambda q: and_(q.base.due.is_not(None), q.base.due < now_sql(), q.base.submitted_at.is_(None),
                                                                                     q.base.status == "prepared")),
                Dim("days_to_submit", "Days from prepared to submitted", "number", lambda q: days_between(q, q.base.submitted_at, q.base.filed_at), group=False),
                Dim("channel", "Channel", "text", lambda q: q.base.channel),
                Dim("filed_by", "Prepared by", "text", lambda q: q.base.filed_by),
                Dim("is_amendment", "Corrects an earlier filing", "bool", lambda q: q.base.amends_id != ""),
                Dim("rejected", "Rejected by the regulator", "bool", lambda q: q.base.status == "rejected"),
                Dim("reference", "Regulator's reference", "text", lambda q: q.base.reference, group=False),
                *case_dims(), *customer_dims(),
            ]},
            {m.id: m for m in [
                Measure("filings", "Number of filings", lambda q: func.count()),
                Measure("past_due", "Past their deadline", lambda q: count_if(and_(q.base.due < now_sql(), q.base.submitted_at.is_(None), q.base.status == "prepared"))),
                Measure("avg_days_to_submit", "Average days from prepared to submitted", lambda q: func.avg(days_between(q, q.base.submitted_at, q.base.filed_at)), "days"),
                Measure("rejection_rate", "Share rejected, in percent", lambda q: share(q.base.status == "rejected"), "percent"),
            ]},
            lambda sc, q: sc.case_or_party_ok(q.base.case_id, q.base.party_id), ("id", "kind", "status", "prepared", "due", "submitted", "customer", "case_id"),
            "prepared", joins={"party": _party("party_id"), "case": _case()}, permission=needs("submit_filings", "view_team"), case_dim="case_id"),

        Obj("holds", "holds", "Payments, accounts, cards and online access held while a case is worked (sanctions blocks, fraud holds).", H, "hold", "id",
            {d.id: d for d in [
                Dim("id", "Hold id", "text", lambda q: q.base.id, group=False),
                Dim("target", "What is held", "text", lambda q: q.base.target, "payment, account, card or online_access"),
                Dim("amount", "Amount held", "number", lambda q: q.base.amount, group=False),
                Dim("reason", "Reason code", "text", lambda q: q.base.reason_code),
                Dim("status", "Status", "text", lambda q: q.base.status, "active, released, blocked or rejected"),
                Dim("placed", "Placed", "date", lambda q: q.base.placed_at, group=False),
                month("placed", lambda q: q.base.placed_at),
                Dim("review_by", "Review due", "date", lambda q: q.base.review_by, group=False),
                Dim("review_overdue", "Active past its review date", "bool", lambda q: and_(q.base.status == "active", q.base.review_by < now_sql())),
                Dim("waiting_second_person", "Waiting for a second person", "bool", lambda q: and_(q.base.proposal.is_not(None), q.base.status == "active")),
                Dim("placed_by", "Placed by", "text", lambda q: q.base.placed_by),
                Dim("decided_by", "Decided by", "text", lambda q: q.base.decided_by),
                Dim("days_held", "Days held", "number", lambda q: days_between(q, func.coalesce(q.base.decided_at, now_sql()), q.base.placed_at), group=False),
                *case_dims(), *customer_dims(),
            ]},
            {m.id: m for m in [
                Measure("holds", "Number of holds", lambda q: func.count()),
                Measure("total_amount", "Total amount held", lambda q: func.sum(q.base.amount), "usd"),
                Measure("active", "Active holds", lambda q: count_if(q.base.status == "active")),
                Measure("review_overdue", "Active past their review date", lambda q: count_if(and_(q.base.status == "active", q.base.review_by < now_sql()))),
                Measure("avg_days_held", "Average days held", lambda q: func.avg(days_between(q, func.coalesce(q.base.decided_at, now_sql()), q.base.placed_at)), "days"),
            ]},
            lambda sc, q: sc.case_ok(q.base.case_id), ("id", "target", "amount", "status", "reason", "placed", "review_by", "customer", "case_id"), "placed",
            joins={"party": _party("party_id"), "case": _case()}, case_dim="case_id"),

        Obj("disputes", "disputes", "Customer disputes and fraud claims on card, ACH, wire and P2P payments: credits, recoveries and losses.", D, "dispute", "id",
            {d.id: d for d in [
                Dim("id", "Dispute id", "text", lambda q: q.base.id, group=False),
                Dim("rail", "Payment rail", "text", lambda q: q.base.rail, "card, ach, wire or p2p"),
                Dim("reason", "Reason code", "text", lambda q: q.base.reason_code),
                Dim("status", "Status", "text", lambda q: q.base.status, "open, credited, recovery or closed"),
                Dim("outcome", "Outcome", "text", lambda q: q.base.outcome, "recovered, partly_recovered, written_off, customer_liable, withdrawn"),
                Dim("amount", "Amount disputed", "number", lambda q: q.base.amount, group=False),
                Dim("credited", "Amount credited", "number", lambda q: q.base.credit_amount, group=False),
                Dim("recovered", "Amount recovered", "number", lambda q: q.base.recovered, group=False),
                Dim("loss", "Loss", "number", lambda q: q.base.loss, group=False),
                Dim("reported", "Reported", "date", lambda q: q.base.reported_at, group=False),
                month("reported", lambda q: q.base.reported_at),
                Dim("credit_overdue", "Provisional credit overdue", "bool", lambda q: and_(q.base.credit_due < now_sql(), q.base.credit_at.is_(None),
                                                                                         q.base.status == "open")),
                Dim("recovery_window_open", "Recovery still possible", "bool", lambda q: and_(q.base.recovery_due >= now_sql(), q.base.recovery_ref == "")),
                *case_dims(), *customer_dims(),
            ]},
            {m.id: m for m in [
                Measure("disputes", "Number of disputes", lambda q: func.count()),
                Measure("total_amount", "Total disputed", lambda q: func.sum(q.base.amount), "usd"),
                Measure("total_credited", "Total credited", lambda q: func.sum(q.base.credit_amount), "usd"),
                Measure("total_recovered", "Total recovered", lambda q: func.sum(q.base.recovered), "usd"),
                Measure("total_loss", "Total loss", lambda q: func.sum(q.base.loss), "usd"),
                Measure("recovery_rate", "Recovered as a share of the amount disputed, in percent",
                        lambda q: 100.0 * func.sum(q.base.recovered) / func.nullif(func.sum(q.base.amount), 0), "percent"),
                Measure("credit_overdue", "Provisional credits overdue", lambda q: count_if(and_(q.base.credit_due < now_sql(), q.base.credit_at.is_(None),
                                                                                                q.base.status == "open"))),
            ]},
            lambda sc, q: sc.case_ok(q.base.case_id), ("id", "rail", "reason", "status", "outcome", "amount", "loss", "reported", "customer", "case_id"),
            "reported", joins={"party": _party("party_id"), "case": _case()}, case_dim="case_id"),

        Obj("risk_assessments", "KYC risk assessments", "Customer risk ratings worked out by the KYC model, with any override by a person.", R, "risk_assessment", "id",
            {d.id: d for d in [
                Dim("id", "Assessment id", "text", lambda q: q.base.id, group=False),
                Dim("current", "Current assessment", "bool", lambda q: q.base.current),
                Dim("assessed", "Assessed", "date", lambda q: q.base.at, group=False),
                month("assessed", lambda q: q.base.at),
                Dim("score", "Score", "number", lambda q: q.base.score, group=False),
                Dim("model_band", "Band the model gave", "text", lambda q: q.base.band, "low, medium or high"),
                Dim("final_band", "Band that stands", "text", lambda q: q.base.final_band, "after any override"),
                Dim("overridden", "Overridden by a person", "bool", lambda q: q.base.band != q.base.final_band),
                Dim("override_pending", "Override waiting for approval", "bool", lambda q: q.base.override.is_not(None) & (q.base.band == q.base.final_band)),
                Dim("assessed_by", "Assessed by", "text", lambda q: q.base.by),
                *customer_dims(),
            ]},
            {m.id: m for m in [
                Measure("assessments", "Number of assessments", lambda q: func.count()),
                Measure("avg_score", "Average score", lambda q: func.avg(q.base.score), "score"),
                Measure("override_rate", "Share overridden by a person, in percent", lambda q: share(q.base.band != q.base.final_band), "percent"),
                Measure("customers", "Number of customers", lambda q: func.count(func.distinct(q.base.party_id))),
            ]},
            lambda sc, q: sc.party_ok(q.base.party_id), ("id", "customer", "assessed", "score", "model_band", "final_band", "overridden", "current"), "assessed",
            joins={"party": _party("party_id")}),

        Obj("kyc_triggers", "KYC review triggers", "Events that started or joined a KYC review: new adverse media, a change of ownership, a risk change.", K,
            "kyc_trigger", "id",
            {d.id: d for d in [
                Dim("id", "Trigger id", "text", lambda q: q.base.id, group=False),
                Dim("kind", "Kind of event", "text", lambda q: q.base.kind),
                Dim("outcome", "What it did", "text", lambda q: q.base.outcome, "opened_review, joined_review or noted"),
                Dim("at", "When", "date", lambda q: q.base.at, group=False),
                month("triggered", lambda q: q.base.at),
                Dim("detail", "Detail", "text", lambda q: q.base.detail, group=False),
                *case_dims(), *customer_dims(),
            ]},
            {m.id: m for m in [Measure("triggers", "Number of triggers", lambda q: func.count()),
                               Measure("opened_reviews", "Reviews opened", lambda q: count_if(q.base.outcome == "opened_review"))]},
            lambda sc, q: sc.case_or_party_ok(q.base.case_id, q.base.party_id), ("id", "at", "kind", "outcome", "customer", "case_id"), "at",
            joins={"party": _party("party_id"), "case": _case()}, case_dim="case_id"),

        Obj("agent_runs", "agent runs", "Each time the agents briefed a case: their recommendation, confidence, and whether the reviewer agreed.", G, "agent_run", "id",
            {d.id: d for d in [
                Dim("id", "Run id", "text", lambda q: q.base.id, group=False),
                Dim("recommendation", "Recommendation", "text", lambda q: q.base.recommendation, "escalate or close"),
                Dim("confidence", "Confidence", "number", lambda q: q.base.confidence, "0 to 1", group=False),
                Dim("model", "Model or rules", "text", lambda q: q.base.provider),
                Dim("started", "Started", "date", lambda q: q.base.started, group=False),
                month("run", lambda q: q.base.started),
                Dim("on_request", "Run on a person's request", "bool", lambda q: q.base.requested_by.is_not(None)),
                Dim("reviewer_agreed", "Reviewer agreed with the agent", "bool", lambda q: _agreed(q), "closed cases only; empty while the case is open"),
                Dim("policy_version", "Agent policy version", "number", lambda q: q.base.policy_version),
                *case_dims(),
                *customer_dims(pid=lambda q: q.j("case").party_id),
            ]},
            {m.id: m for m in [
                Measure("runs", "Number of runs", lambda q: func.count()),
                Measure("avg_confidence", "Average confidence", lambda q: func.avg(q.base.confidence), "score"),
                Measure("agreement", "Share of decided cases where the reviewer agreed, in percent",
                        lambda q: 100.0 * func.sum(case((_agreed(q).is_(True), 1), else_=0)) / func.nullif(func.sum(case((_agreed(q).is_not(None), 1), else_=0)), 0),
                        "percent"),
                Measure("override_rate", "Share of decided cases where the reviewer overrode the agent, in percent",
                        lambda q: 100.0 * func.sum(case((_agreed(q).is_(False), 1), else_=0)) / func.nullif(func.sum(case((_agreed(q).is_not(None), 1), else_=0)), 0),
                        "percent"),
            ]},
            lambda sc, q: sc.case_ok(q.base.case_id), ("id", "started", "case_type", "recommendation", "confidence", "reviewer_agreed", "case_id"), "started",
            joins={"case": _case(), "party": (Party, lambda b, a, q: a.id == q.j("case").party_id)}, case_dim="case_id"),

        Obj("agent_actions", "agent actions", "The agents' ledger: every step an agent took, at what autonomy level, what it did and what it refused.", S,
            "agent_action", "id",
            {d.id: d for d in [
                Dim("id", "Ledger entry", "number", lambda q: q.base.id, group=False),
                Dim("agent", "Agent", "text", lambda q: q.base.agent, "for example triage, entity_research, sanctions_adjudicator"),
                Dim("level", "Autonomy level", "text", lambda q: q.base.level),
                Dim("outcome", "What happened", "text", lambda q: q.base.outcome, "done, skipped, refused or acted"),
                Dim("model", "Model or rules", "text", lambda q: q.base.model),
                Dim("at", "When", "date", lambda q: q.base.ts, group=False),
                month("logged", lambda q: q.base.ts),
                Dim("shadow", "Challenger run (not shown on the case)", "bool", lambda q: q.base.shadow),
                Dim("summary", "Summary", "text", lambda q: q.base.summary, group=False),
                Dim("case_id", "Case id", "text", lambda q: q.base.case_id, group=False),
                Dim("alert_id", "Alert id", "text", lambda q: q.base.alert_id, group=False),
                Dim("case_type", "Case type", "text", lambda q: q.j("case").case_type),
            ]},
            {m.id: m for m in [
                Measure("actions", "Number of ledger entries", lambda q: func.count()),
                Measure("acted", "Entries where the agent acted on its own", lambda q: count_if(q.base.outcome == "acted")),
                Measure("refused", "Entries refused by policy", lambda q: count_if(q.base.outcome == "refused")),
                Measure("acted_share", "Share where the agent acted on its own, in percent", lambda q: share(q.base.outcome == "acted"), "percent"),
            ]},
            lambda sc, q: sc.case_or_party_ok(q.base.case_id, None),
            ("id", "at", "agent", "level", "outcome", "summary", "case_id", "alert_id"), "at", joins={"case": _case()},
            permission=needs("view_team"), customer_dim=None, case_dim="case_id"),

        Obj("detection_changes", "detection changes", "Proposed and approved changes to detection scenario settings, with their backtests.", V,
            "detection_change", "id",
            {d.id: d for d in [
                Dim("id", "Change id", "text", lambda q: q.base.id, group=False),
                Dim("scenario", "Scenario", "text", lambda q: q.base.scenario),
                Dim("status", "Status", "text", lambda q: q.base.status, "pending, live, refused, withdrawn, superseded"),
                Dim("enabled", "Scenario switched on", "bool", lambda q: q.base.enabled),
                Dim("proposed", "Proposed", "date", lambda q: q.base.proposed_at, group=False),
                month("proposed", lambda q: q.base.proposed_at),
                Dim("proposed_by", "Proposed by", "text", lambda q: q.base.proposed_by),
                Dim("decided_by", "Decided by", "text", lambda q: q.base.decided_by),
                Dim("reason", "Reason", "text", lambda q: q.base.reason, group=False),
            ]},
            {m.id: m for m in [Measure("changes", "Number of changes", lambda q: func.count()),
                               Measure("pending", "Waiting for approval", lambda q: count_if(q.base.status == "pending"))]},
            lambda sc, q: true(), ("id", "scenario", "status", "proposed", "proposed_by", "decided_by", "reason"), "proposed", customer_dim=None),

        Obj("lookbacks", "lookbacks", "Lookback projects: past periods re-run under new settings after a finding or a new typology.", L, "lookback", "id",
            {d.id: d for d in [
                Dim("id", "Lookback id", "text", lambda q: q.base.id, group=False),
                Dim("name", "Name", "text", lambda q: q.base.name, group=False),
                Dim("scenario", "Scenario", "text", lambda q: q.base.scenario),
                Dim("status", "Status", "text", lambda q: q.base.status, "planned, working, closed or cancelled"),
                Dim("due", "Due", "date", lambda q: q.base.due, group=False),
                Dim("overdue", "Past its due date and not closed", "bool", lambda q: and_(q.base.due < now_sql(), q.base.status.in_(["planned", "working"]))),
                Dim("created", "Created", "date", lambda q: q.base.created, group=False),
                Dim("reason", "Why", "text", lambda q: q.base.reason, group=False),
            ]},
            {m.id: m for m in [Measure("lookbacks", "Number of lookbacks", lambda q: func.count())]},
            lambda sc, q: true(), ("id", "name", "scenario", "status", "due", "created"), "created", customer_dim=None),

        Obj("pipeline_runs", "data pipeline runs", "Loads of customers, accounts and transactions from the client's object storage.", PR, "pipeline_run", "id",
            {d.id: d for d in [
                Dim("id", "Run id", "text", lambda q: q.base.id, group=False),
                Dim("source", "Data source", "text", lambda q: q.j("source").name),
                Dim("dataset", "Dataset", "text", lambda q: q.j("source").dataset, "customers, accounts or transactions"),
                Dim("status", "Status", "text", lambda q: q.base.status),
                Dim("trigger", "Started by", "text", lambda q: q.base.trigger, "manual or schedule"),
                Dim("started", "Started", "date", lambda q: q.base.started, group=False),
                month("run", lambda q: q.base.started),
                Dim("minutes", "Minutes taken", "number", lambda q: days_between(q, q.base.finished, q.base.started) * 1440, group=False),
                Dim("error", "Error", "text", lambda q: q.base.error, group=False),
            ]},
            {m.id: m for m in [Measure("runs", "Number of runs", lambda q: func.count()),
                               Measure("failed", "Failed runs", lambda q: count_if(q.base.status == "failed"))]},
            lambda sc, q: true(), ("id", "source", "dataset", "status", "trigger", "started"), "started",
            joins={"source": (DataSource, lambda b, a: a.id == b.source_id)}, permission=needs("load_data", "view_team"), customer_dim=None),
    ]
    return {o.id: o for o in out}


def _agreed(q: Q):
    """Whether the reviewer's decision matched the agent's recommendation; null while the case is open."""
    c = q.j("case")
    confirmed = c.disposition.in_(ESCALATED)
    return case((c.closed.is_(None), None), (c.case_type == "qa_review", None), (q.base.recommendation == "", None),
                else_=(confirmed == (q.base.recommendation == "escalate")))


def _days_ago(n: int):
    from datetime import timedelta

    from ...domain.models import now

    from sqlalchemy import literal
    return literal(now() - timedelta(days=n))


def available(p: Persona) -> list[Obj]:
    return [o for o in objects().values() if o.permission(p) is None]


def get(object_id: str) -> Obj:
    found = objects().get(object_id)
    if found is None:
        raise KeyError(object_id)
    return found


__all__ = ["objects", "available", "get", "Scope", "exists"]
