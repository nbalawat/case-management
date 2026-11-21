"""A metered record of every model call, and the spend limits that read it.

One row per model reply: which client, what for (a question, an agent run), which endpoint and model served it,
the tokens either way and what that cost at the endpoint's configured price. The rows are the client's own
usage record (shown on the Agents page) and the basis for metering a managed service. Limits are checked before
every call, so a client over its monthly allowance gets a plain refusal, not a bill.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..db import Base
from ..tenancy import TenantScoped
from .types import ModelUnavailable


def _now() -> datetime:
    from ..domain.models import now

    return now()


class ModelUsage(TenantScoped, Base):
    __tablename__ = "model_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # ask | agents
    endpoint: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))  # the model that actually served the reply
    actor: Mapped[str] = mapped_column(String(60), default="")  # the person asking, or the case an agent worked
    ref: Mapped[str] = mapped_column(String(60), default="")  # the conversation or run this belongs to
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # None when the endpoint has no price


class OverBudget(ModelUnavailable):
    """A spend limit in the client's model policy stops this call."""


def month_start(at: datetime | None = None) -> datetime:
    at = at or _now()
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def used_this_month(s: Session, tenant_id: str | None = None) -> int:
    """Tokens used since the first of the month. A client session counts its own; platform staff name the client."""
    q = select(func.coalesce(func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens), 0)).where(ModelUsage.ts >= month_start())
    if tenant_id:
        q = q.where(ModelUsage.tenant_id == tenant_id)
    return int(s.scalar(q) or 0)


def check_month(s: Session, limit: int) -> None:
    if limit and used_this_month(s) >= limit:
        raise OverBudget(f"This client has used its model allowance for the month ({limit:,} tokens). "
                         "Platform staff can raise the limit in the client's model policy.")


def record(s: Session, *, purpose: str, endpoint, model: str, actor: str, ref: str, usage: dict) -> ModelUsage:
    i, o = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    row = ModelUsage(purpose=purpose, endpoint=endpoint.id, model=(model or endpoint.model)[:80], actor=actor[:60], ref=ref[:60],
                     input_tokens=i, output_tokens=o, cost_usd=endpoint.cost(i, o))
    s.add(row)
    s.flush()
    return row


def summary(s: Session, since: datetime | None = None, tenant_id: str | None = None) -> dict:
    """Usage this month, by purpose and by model."""
    since = since or month_start()
    where = [ModelUsage.ts >= since] + ([ModelUsage.tenant_id == tenant_id] if tenant_id else [])
    rows = s.execute(select(ModelUsage.purpose, ModelUsage.endpoint, ModelUsage.model, func.count(),
                            func.sum(ModelUsage.input_tokens), func.sum(ModelUsage.output_tokens), func.sum(ModelUsage.cost_usd),
                            func.count(ModelUsage.cost_usd))
                     .where(*where).group_by(ModelUsage.purpose, ModelUsage.endpoint, ModelUsage.model)).all()
    lines = [{"purpose": p, "endpoint": e, "model": m, "calls": n, "input_tokens": int(i or 0), "output_tokens": int(o or 0),
              "cost_usd": round(c, 4) if priced == n and c is not None else None} for p, e, m, n, i, o, c, priced in rows]
    priced = [x["cost_usd"] for x in lines if x["cost_usd"] is not None]
    return {"since": since.date().isoformat(), "lines": sorted(lines, key=lambda x: -(x["input_tokens"] + x["output_tokens"])),
            "tokens": sum(x["input_tokens"] + x["output_tokens"] for x in lines), "calls": sum(x["calls"] for x in lines),
            "cost_usd": round(sum(priced), 4) if priced else 0.0, "unpriced_calls": sum(x["calls"] for x in lines if x["cost_usd"] is None)}
