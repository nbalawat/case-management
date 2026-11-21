"""Usage per client per day, for the provider's billing and capacity planning.

Every figure is counted from records the platform keeps anyway (cases, alerts, documents, Ask questions, model calls,
the managed-service team's actions), so there is nothing to keep in step and nothing a client could dispute against
a separate counter: the meter reads the same rows the client's own pages do.

A meter is a row of `METERS`: which table, which timestamp, and what to add up. Adding one is one line.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai.usage import ModelUsage
from ..domain.models import AccessEvent, Alert, AskLog, Case, CaseEvent, Evidence, Filing, Tenant, Transaction


@dataclass(frozen=True)
class Meter:
    id: str
    label: str
    unit: str
    model: type
    at: str  # the timestamp column that places a row on a day
    measure: str = "count"  # count | sum:<column> | sum:<a>+<b> | distinct:<column>
    where: tuple = ()  # (column, value) pairs a row must match
    billable: bool = True


METERS: tuple[Meter, ...] = (
    Meter("cases_opened", "Cases opened", "cases", Case, "created"),
    Meter("alerts", "Alerts received", "alerts", Alert, "created"),
    Meter("transactions", "Transactions monitored, by transaction date", "transactions", Transaction, "ts"),
    Meter("documents", "Documents stored", "documents", Evidence, "uploaded_at"),
    Meter("document_bytes", "Document storage added", "bytes", Evidence, "uploaded_at", "sum:size"),
    Meter("filings", "Regulatory reports prepared", "reports", Filing, "filed_at"),
    Meter("ask_questions", "Questions asked of Ask", "questions", AskLog, "created"),
    Meter("model_tokens", "Model tokens", "tokens", ModelUsage, "ts", "sum:input_tokens+output_tokens"),
    Meter("model_cost", "Model cost at list price", "USD", ModelUsage, "ts", "sum:cost_usd"),
    Meter("active_people", "People who worked a case", "people", CaseEvent, "ts", "distinct:actor", (("actor_kind", "human"),), billable=False),
    Meter("provider_actions", "Managed-service team actions", "actions", AccessEvent, "ts", "count", (("kind", "provider.request"),)),
)
BY_ID = {m.id: m for m in METERS}


def _measure(m: Meter):
    kind, _, arg = m.measure.partition(":")
    if kind == "count":
        return func.count()
    if kind == "distinct":
        return func.count(func.distinct(getattr(m.model, arg)))
    cols = [func.coalesce(getattr(m.model, c), 0) for c in arg.split("+")]
    expr = cols[0]
    for c in cols[1:]:
        expr = expr + c
    return func.coalesce(func.sum(expr), 0)


def usage(s: Session, start: date, end: date, tenants: list[str] | None = None) -> dict:
    """Every meter, per client and day, from `start` to `end` inclusive. `s` is a platform session."""
    if end < start:
        raise ValueError("The period ends before it starts")
    if (end - start).days > 366:
        raise ValueError("Ask for at most a year at a time")
    lo, hi = datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)
    names = {t.id: t.name for t in s.scalars(select(Tenant)) if tenants is None or t.id in tenants}
    clients = {t: {"tenant": t, "name": n, "totals": {m.id: 0 for m in METERS}, "days": {}} for t, n in names.items()}
    for m in METERS:
        at = getattr(m.model, m.at)
        day = func.date(at)  # a date on Postgres, 'YYYY-MM-DD' on SQLite
        q = (select(m.model.tenant_id, day, _measure(m)).where(at >= lo, at < hi, m.model.tenant_id.in_(list(names)))
             .group_by(m.model.tenant_id, day))
        for col, value in m.where:
            q = q.where(getattr(m.model, col) == value)
        for tenant, d, value in s.execute(q):
            v = round(float(value), 4) if m.unit == "USD" else int(value or 0)
            key = d.isoformat() if isinstance(d, date) else str(d)[:10]
            clients[tenant]["days"].setdefault(key, {})[m.id] = v
            if m.id != "active_people":  # people are counted per day; a sum over days would count the same person again
                clients[tenant]["totals"][m.id] += v
        if m.id == "active_people":
            q = select(m.model.tenant_id, _measure(m)).where(at >= lo, at < hi, m.model.tenant_id.in_(list(names)), m.model.actor_kind == "human")
            for tenant, value in s.execute(q.group_by(m.model.tenant_id)):
                clients[tenant]["totals"][m.id] = int(value)
    for c in clients.values():
        c["totals"]["model_cost"] = round(c["totals"]["model_cost"], 4)
        c["days"] = [{"day": k} | v for k, v in sorted(c["days"].items())]
    return {"from": start.isoformat(), "to": end.isoformat(),
            "meters": [{"id": m.id, "label": m.label, "unit": m.unit, "billable": m.billable} for m in METERS],
            "clients": sorted(clients.values(), key=lambda c: c["name"])}


def as_csv(report: dict) -> str:
    """One row per client, day and meter: what a billing system imports."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["client", "client_name", "day", "meter", "unit", "value"])
    units = {m["id"]: m["unit"] for m in report["meters"]}
    for c in report["clients"]:
        for d in c["days"]:
            for mid, value in d.items():
                if mid != "day":
                    w.writerow([c["tenant"], c["name"], d["day"], mid, units[mid], value])
    return out.getvalue()
