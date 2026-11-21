"""The agent ledger. Everything an agent does is written here once and never changed.

Each row carries the hash of the row before it for the same client, so a row that was altered, removed or
inserted later is detectable by anyone who recomputes the chain.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import AgentStep, now
from ..visibility import system_work

CHAINED = ("run_id", "case_id", "alert_id", "agent", "agent_version", "level", "policy_version", "model", "prompt_sha", "outcome",
           "summary", "tools", "findings", "rejected", "shadow")


def _digest(row: AgentStep) -> str:
    body = {k: getattr(row, k) for k in CHAINED} | {"ts": row.ts.isoformat(), "prev": row.prev_hash}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def append(s: Session, *, run_id: str, agent: str, agent_version: str, level: str, policy_version: int, outcome: str = "done",
           summary: str = "", case_id: str | None = None, alert_id: str | None = None, tools: list | None = None,
           findings: list | None = None, rejected: list | None = None, model: str = "rules", prompt: str = "", shadow: bool = False,
           ts: datetime | None = None) -> AgentStep:
    s.flush()
    last = s.scalars(select(AgentStep).order_by(AgentStep.id.desc()).limit(1)).first()
    row = AgentStep(run_id=run_id, case_id=case_id, alert_id=alert_id, agent=agent, agent_version=agent_version, level=level,
                    policy_version=policy_version, model=model, prompt_sha=hashlib.sha256(prompt.encode()).hexdigest() if prompt else "",
                    outcome=outcome, summary=summary, tools=tools or [], findings=findings or [], rejected=rejected or [], shadow=shadow,
                    ts=(ts or now()).replace(microsecond=0), prev_hash=last.hash if last else "")
    row.hash = _digest(row)
    s.add(row)
    s.flush()
    return row


@system_work
def verify(s: Session) -> dict:
    """Recompute the whole chain for this client."""
    prev, n = "", 0
    for row in s.scalars(select(AgentStep).order_by(AgentStep.id)):
        n += 1
        if row.prev_hash != prev or row.hash != _digest(row):
            return {"intact": False, "rows": n, "broken_at": row.id, "head": prev}
        prev = row.hash
    return {"intact": True, "rows": n, "broken_at": None, "head": prev}


def entries(s: Session, *, case_id: str | None = None, agent: str | None = None, limit: int = 100) -> list[dict]:
    q = select(AgentStep).order_by(AgentStep.id.desc()).limit(limit)
    if case_id:
        q = q.where(AgentStep.case_id == case_id)
    if agent:
        q = q.where(AgentStep.agent == agent)
    return [{"id": r.id, "ts": r.ts.isoformat(), "run_id": r.run_id, "case_id": r.case_id, "alert_id": r.alert_id, "agent": r.agent,
             "agent_version": r.agent_version, "level": r.level, "policy_version": r.policy_version, "model": r.model,
             "prompt_sha": r.prompt_sha, "outcome": r.outcome, "summary": r.summary, "tools": r.tools, "findings": len(r.findings),
             "rejected": r.rejected, "shadow": r.shadow, "hash": r.hash} for r in s.scalars(q)]


@system_work
def usage(s: Session) -> dict[str, dict]:
    """Per agent: how often it ran, acted, was refused, and how many claims were dropped for citing nothing."""
    out: dict[str, dict] = {}
    for agent, outcome, n in s.execute(select(AgentStep.agent, AgentStep.outcome, func.count()).where(AgentStep.shadow.is_(False))
                                       .group_by(AgentStep.agent, AgentStep.outcome)):
        out.setdefault(agent, {"done": 0, "skipped": 0, "refused": 0, "acted": 0, "rejected_claims": 0, "last": None})[outcome] = n
    for row in s.scalars(select(AgentStep).where(AgentStep.shadow.is_(False))):
        u = out[row.agent]
        u["rejected_claims"] += len(row.rejected or [])
        u["last"] = max(u["last"] or row.ts.isoformat(), row.ts.isoformat())
    return out
