"""The governed query layer behind "ask anything".

An agent never writes SQL. It asks this layer for a breakdown or a list of cases using a fixed vocabulary of
fields and measures, and the layer answers from the asker's own client session. So an answer can only ever
contain what that session can see, and every figure can be traced to the cases behind it.

At demo scale the layer reads the client's cases into memory and computes there. In the cloud build the same
vocabulary is served by the lakehouse; the tools above it do not change.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import get_definition
from ..cases.service import stage_due
from ..domain.models import Alert, Case, Party, Transaction, now
from ..domain.personas import Persona, policy_of
from ..reporting.metrics import ESCALATED
from ..tenancy import tenant_of


class QueryError(ValueError):
    """The request used a field, measure or operator outside the vocabulary. The message says what is allowed."""


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    kind: str  # text | number | bool | date | list
    help: str = ""
    group: bool = True


FIELDS: dict[str, Field] = {f.id: f for f in [
    Field("case_type", "Case type", "text", "The workflow, for example 'Fraud claim'"),
    Field("status", "Status", "text", "open or closed"),
    Field("stage", "Stage", "text", "Current workflow stage, for example 'Investigation'"),
    Field("waiting", "Waiting on someone outside the team", "bool"),
    Field("priority", "Priority", "text", "low, medium, high or critical"),
    Field("owner", "Owner", "text", "Person the case is assigned to, or 'Unassigned'"),
    Field("queue", "Queue", "text", "Team queue the case sits in"),
    Field("outcome", "Outcome", "text", "How a closed case ended, for example 'sar filed' or 'no sar'"),
    Field("sar_filed", "SAR filed", "bool"),
    Field("agent_recommendation", "Agent recommendation", "text", "escalate, close or none"),
    Field("agent_agreed", "Reviewer agreed with the agent", "bool", "Closed cases only; empty where the agent made no recommendation"),
    Field("risk_rating", "Customer risk rating", "text", "low, medium or high"),
    Field("pep", "Politically exposed customer", "bool"),
    Field("customer", "Customer name", "text", group=False),
    Field("customer_country", "Customer country", "text", "Two-letter country code"),
    Field("scenario", "Alert scenario", "list", "Detection scenarios behind the case, for example 'structuring'"),
    Field("counterparty_country", "Counterparty country", "list", "Countries of the counterparties in the alerted transactions, two-letter codes"),
    Field("alerts", "Alerts", "number", "Number of alerts on the case", group=False),
    Field("alerted_amount", "Alerted amount", "number", "Total value of the alerted transactions, in dollars", group=False),
    Field("age_days", "Age in days", "number", "Open cases: days since opened. Closed cases: days it took", group=False),
    Field("days_to_close", "Days to close", "number", "Closed cases only", group=False),
    Field("opened", "Opened", "date", "ISO date", group=False),
    Field("closed", "Closed", "date", "ISO date; empty while open", group=False),
    Field("opened_month", "Month opened", "text", "YYYY-MM"),
    Field("closed_month", "Month closed", "text", "YYYY-MM"),
    Field("opened_week", "Week opened", "text", "Monday of the week, ISO date"),
    Field("stage_overdue", "Past the stage clock", "bool"),
    Field("past_deadline", "Past the regulatory deadline", "bool"),
    Field("is_qa_review", "Is a QA review", "bool"),
    Field("mine", "Assigned to the person asking", "bool", "Use for 'my cases'"),
    Field("in_my_queues", "In a queue the person asking works", "bool"),
]}

MEASURES = {
    "cases": "Number of cases",
    "share": "Share of the matching cases, in percent",
    "alerted_amount": "Total alerted amount in dollars",
    "avg_alerted_amount": "Average alerted amount per case in dollars",
    "avg_days_to_close": "Average days from opened to closed (closed cases)",
    "avg_age_days": "Average age in days",
    "stage_overdue": "Number of cases past their stage clock",
    "sars_filed": "Number of cases closed with a SAR filed",
    "agent_agreement": "Share of closed cases where the reviewer agreed with the agent, in percent",
}
OPS = {"=", "!=", "in", ">", ">=", "<", "<=", "contains"}
OPS_BY_KIND = {"text": {"=", "!=", "in", "contains"}, "list": {"=", "!=", "in"}, "bool": {"=", "!="},
               "number": {"=", "!=", ">", ">=", "<", "<="}, "date": {">", ">=", "<", "<=", "="}}
MAX_ROWS, MAX_LIST = 50, 25


def _norm(v: Any) -> str:
    return str(v).strip().lower().replace("_", " ")


def case_facts(s: Session, asker: Persona | None = None) -> list[dict]:
    """One flat record per case the session can see, with everything the vocabulary offers."""
    tenant = tenant_of(s)
    policy = policy_of(tenant)
    today = now()
    cases = list(s.scalars(select(Case)))
    parties = {p.id: p for p in s.scalars(select(Party).where(Party.id.in_({c.party_id for c in cases})))} if cases else {}
    alerts_by: dict[str, list[Alert]] = defaultdict(list)
    for a in s.scalars(select(Alert).where(Alert.case_id.is_not(None))):
        alerts_by[a.case_id].append(a)
    txn_ids = {i for alerts in alerts_by.values() for a in alerts for i in a.txn_ids}
    txns = {t.id: t for t in s.scalars(select(Transaction).where(Transaction.id.in_(txn_ids)))} if txn_ids else {}
    out = []
    for c in cases:
        defn = get_definition(tenant, c.case_type, c.type_version)
        state = defn["states"].get(c.state, {})
        party = parties[c.party_id]
        alerted = [txns[i] for a in alerts_by[c.id] for i in a.txn_ids if i in txns]
        due = stage_due(c, defn) if c.state in defn["states"] else None
        end = c.closed or today
        agreed = None
        if c.closed and c.agent_recommendation and c.case_type != "qa_review":
            agreed = (c.disposition in ESCALATED) == (c.agent_recommendation == "escalate")
        out.append({
            "id": c.id, "case_type": defn["title"], "case_type_id": c.case_type, "status": "closed" if c.closed else "open",
            "stage": state.get("label", c.state), "waiting": state.get("kind") == "wait", "priority": c.priority,
            "owner": policy.people[c.owner].name if c.owner in policy.people else (c.owner or "Unassigned"),
            "queue": policy.queues.get(c.queue or "", "") or "None", "outcome": (c.disposition or "").replace("_", " ") or None,
            "sar_filed": c.disposition == "sar_filed", "agent_recommendation": c.agent_recommendation or "none", "agent_agreed": agreed,
            "risk_rating": party.risk_rating, "pep": bool(party.pep), "customer": party.name, "customer_id": party.id,
            "customer_country": party.country, "scenario": sorted({a.scenario for a in alerts_by[c.id]}),
            "counterparty_country": sorted({t.counterparty_country for t in alerted}), "alerts": len(alerts_by[c.id]),
            "alerted_amount": round(sum(t.amount for t in alerted), 2), "age_days": round((end - c.created).total_seconds() / 86400, 1),
            "days_to_close": round((c.closed - c.created).total_seconds() / 86400, 1) if c.closed else None,
            "opened": c.created, "closed": c.closed, "opened_month": c.created.strftime("%Y-%m"),
            "closed_month": c.closed.strftime("%Y-%m") if c.closed else None,
            "opened_week": (c.created - timedelta(days=c.created.weekday())).date().isoformat(),
            "stage_overdue": bool(due and due < today), "past_deadline": bool(c.closed is None and c.sla_due and c.sla_due < today),
            "is_qa_review": c.case_type == "qa_review",
            "mine": bool(asker and c.owner == asker.id), "in_my_queues": bool(asker and c.queue in asker.queues),
        })
    return out


# --- filters ----------------------------------------------------------------------
def _coerce(field: Field, value: Any) -> Any:
    try:
        if field.kind == "number":
            return float(value)
        if field.kind == "bool":
            if isinstance(value, bool):
                return value
            if _norm(value) in ("true", "yes", "1"):
                return True
            if _norm(value) in ("false", "no", "0"):
                return False
            raise ValueError
        if field.kind == "date":
            return value if isinstance(value, datetime) else datetime.fromisoformat(str(value)[:19])
    except (TypeError, ValueError):
        raise QueryError(f"'{value}' is not a valid {field.kind} for {field.id}") from None
    return _norm(value)


def check_filters(filters: list[dict] | None) -> list[tuple[Field, str, Any]]:
    out = []
    for f in filters or []:
        if not isinstance(f, dict) or not {"field", "op", "value"} <= set(f):
            raise QueryError("Each filter needs field, op and value")
        field = FIELDS.get(f["field"])
        if field is None:
            raise QueryError(f"Unknown field '{f['field']}'. Fields: {', '.join(FIELDS)}")
        op = f["op"]
        if op not in OPS_BY_KIND[field.kind]:
            raise QueryError(f"Operator '{op}' does not apply to {field.id}. Use one of: {', '.join(sorted(OPS_BY_KIND[field.kind]))}")
        value = f["value"]
        if op == "in":
            if not isinstance(value, list) or not value:
                raise QueryError(f"'in' on {field.id} needs a non-empty list")
            value = [_coerce(field, v) for v in value]
        else:
            value = _coerce(field, value)
        out.append((field, op, value))
    return out


def _test(field: Field, op: str, want: Any, row: dict) -> bool:
    have = row[field.id]
    if field.kind == "list":
        have = {_norm(x) for x in have}
        hit = bool(have & set(want)) if op == "in" else want in have
        return not hit if op == "!=" else hit
    if have is None:
        return op == "!="
    if field.kind == "text":
        have = _norm(have)
        if field.id == "case_type" and op != "contains":  # accept the id as well as the title
            ids = _norm(row["case_type_id"])
            if op == "in":
                return have in want or ids in want
            return (want in (have, ids)) == (op == "=")
        if op == "contains":
            return want in have
        if op == "in":
            return have in want
    return {"=": have == want, "!=": have != want, ">": have > want, ">=": have >= want, "<": have < want, "<=": have <= want}[op]


def select_cases(facts: list[dict], filters: list[dict] | None) -> list[dict]:
    checked = check_filters(filters)
    return [r for r in facts if all(_test(f, op, v, r) for f, op, v in checked)]


# --- measures ---------------------------------------------------------------------
def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _measure(name: str, rows: list[dict], total: int) -> Any:
    if name == "cases":
        return len(rows)
    if name == "share":
        return round(100 * len(rows) / total, 1) if total else None
    if name == "alerted_amount":
        return round(sum(r["alerted_amount"] for r in rows), 2)
    if name == "avg_alerted_amount":
        return _avg([r["alerted_amount"] for r in rows if r["alerts"]])
    if name == "avg_days_to_close":
        return _avg([r["days_to_close"] for r in rows if r["days_to_close"] is not None])
    if name == "avg_age_days":
        return _avg([r["age_days"] for r in rows])
    if name == "stage_overdue":
        return sum(r["stage_overdue"] for r in rows)
    if name == "sars_filed":
        return sum(r["sar_filed"] for r in rows)
    if name == "agent_agreement":
        judged = [r["agent_agreed"] for r in rows if r["agent_agreed"] is not None]
        return round(100 * sum(judged) / len(judged), 1) if judged else None
    raise QueryError(f"Unknown measure '{name}'. Measures: {', '.join(MEASURES)}")


def _show(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str) and not v.startswith("case-"):
        return v.replace("_", " ")
    return v


def breakdown(facts: list[dict], *, group_by: list[str] | None = None, measures: list[str] | None = None,
              filters: list[dict] | None = None, sort_by: str | None = None, descending: bool = True, limit: int = MAX_ROWS) -> dict:
    """Counts and other measures over the matching cases, optionally split by up to two fields."""
    group_by, measures = list(group_by or []), list(measures or ["cases"])
    if len(group_by) > 2:
        raise QueryError("Group by at most two fields")
    for g in group_by:
        if g not in FIELDS or not FIELDS[g].group:
            raise QueryError(f"Cannot group by '{g}'. Groupable fields: {', '.join(k for k, f in FIELDS.items() if f.group)}")
    for m in measures:
        if m not in MEASURES:
            raise QueryError(f"Unknown measure '{m}'. Measures: {', '.join(MEASURES)}")
    if sort_by is not None and sort_by not in measures and sort_by not in group_by:
        raise QueryError("sort_by must be one of the measures or group_by fields in this request")
    rows = select_cases(facts, filters)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        keys = [()]
        for g in group_by:
            values = r[g] if FIELDS[g].kind == "list" else [r[g]]
            keys = [k + (_show(v) if v is not None else "none",) for k in keys for v in (values or ["none"])]
        for k in keys:
            groups[k].append(r)
    if not group_by and not groups:
        groups[()] = []
    table = [{"key": list(k), "values": [_measure(m, members, len(rows)) for m in measures],
              "case_ids": [x["id"] for x in sorted(members, key=lambda x: x["opened"], reverse=True)[:5]]} for k, members in groups.items()]
    by = sort_by or measures[0]
    if by in measures:
        i = measures.index(by)
        table.sort(key=lambda t: (t["values"][i] is None, -(t["values"][i] or 0) if descending else (t["values"][i] or 0), t["key"]))
    else:
        table.sort(key=lambda t: str(t["key"][group_by.index(by)]), reverse=descending)
    limit = max(1, min(int(limit), MAX_ROWS))
    out = {"columns": [FIELDS[g].label for g in group_by] + [MEASURES[m] for m in measures], "group_by": group_by, "measures": measures,
           "rows": table[:limit], "groups": len(table), "truncated": len(table) > limit, "matching_cases": len(rows)}
    if any(FIELDS[g].kind == "list" for g in group_by):
        out["note"] = "A case with several values is counted in each of its groups, so the groups can add up to more than the total."
    return out


LIST_COLUMNS = ["id", "customer", "case_type", "stage", "status", "priority", "owner", "alerted_amount", "age_days", "outcome", "agent_recommendation"]


def list_cases(facts: list[dict], *, filters: list[dict] | None = None, sort_by: str = "opened", descending: bool = True,
               limit: int = 10) -> dict:
    """The matching cases themselves, most relevant first."""
    if sort_by not in FIELDS or FIELDS[sort_by].kind == "list":
        raise QueryError(f"Cannot sort by '{sort_by}'")
    rows = select_cases(facts, filters)
    rows.sort(key=lambda r: (r[sort_by] is None, r[sort_by] if r[sort_by] is not None else 0), reverse=descending)
    if descending:  # keep empty values last in either direction
        rows.sort(key=lambda r: r[sort_by] is None)
    limit = max(1, min(int(limit), MAX_LIST))
    extra = [sort_by] if sort_by not in LIST_COLUMNS else []
    cols = LIST_COLUMNS + extra
    return {"columns": cols, "cases": [{k: _show(r[k]) for k in cols} for r in rows[:limit]], "matching_cases": len(rows),
            "truncated": len(rows) > limit}


def vocabulary(s: Session) -> dict:
    """The words this client uses, so a question can be mapped to exact values."""
    tenant = tenant_of(s)
    policy = policy_of(tenant)
    from ..cases.case_types import load_case_types

    types = load_case_types(tenant)
    return {
        "case_types": [ct["title"] for ct in types.values()],
        "stages": {ct["title"]: [st["label"] for st in ct["states"].values()] for ct in types.values()},
        "outcomes": sorted({st["disposition"].replace("_", " ") for ct in types.values() for st in ct["states"].values() if st.get("disposition")}),
        "queues": list(policy.queues.values()), "people": [p.name for p in policy.people.values() if p.can("work")],
        "scenarios": sorted({sc for ct in types.values() for sc in ct.get("scenarios", [])} | {"sanctions_name"}),
    }
