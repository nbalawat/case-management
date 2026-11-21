"""Dashboard metrics computed from the operational store."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cases.case_types import load_case_types
from ..detection.scenarios import describe
from ..domain.models import Alert, Case, CaseEvent, Feed, Filing, Transaction, now


ESCALATED = ("sar_filed", "true_match", "fraud_confirmed")  # dispositions that confirm the alert


def dashboard(s: Session) -> dict:
    types = load_case_types()
    cases = list(s.scalars(select(Case)))
    today = now()
    open_cases = [c for c in cases if c.closed is None]
    closed = [c for c in cases if c.closed is not None]
    by_state = Counter((c.case_type, c.state) for c in open_cases)
    def label(ct: str, st: str) -> str:  # a case may sit in a stage that a newer version renamed or removed
        return types[ct]["states"].get(st, {}).get("label", st.replace("_", " ").capitalize())

    def order(ct: str, st: str) -> int:
        return list(types[ct]["states"]).index(st) if st in types[ct]["states"] else 99

    funnel = [{"case_type": ct, "title": types[ct]["title"], "state": st, "label": label(ct, st), "count": n}
              for (ct, st), n in sorted(by_state.items(), key=lambda kv: (kv[0][0], order(*kv[0])))]
    at_risk = [c for c in open_cases if c.sla_due and c.sla_due - today <= timedelta(days=7)]
    breached = [c for c in open_cases if c.sla_due and c.sla_due < today]
    # Human decision vs agent recommendation on closed cases.
    agree = total = 0
    for c in closed:
        if not c.agent_recommendation:
            continue
        total += 1
        human_escalated = c.disposition in ESCALATED
        agree += int(human_escalated == (c.agent_recommendation == "escalate"))
    days = [(today - timedelta(days=i)).date() for i in range(29, -1, -1)]
    opened, closed_by = defaultdict(int), defaultdict(int)
    for c in cases:
        opened[c.created.date()] += 1
        if c.closed:
            closed_by[c.closed.date()] += 1
    durations = [(c.closed - c.created).total_seconds() / 86400 for c in closed]
    alerts_by = Counter(s.scalars(select(Alert.scenario)))
    conv = defaultdict(lambda: [0, 0])
    for c in closed:
        for sc in s.scalars(select(Alert.scenario).where(Alert.case_id == c.id)):
            conv[sc][0] += 1
            conv[sc][1] += int(c.disposition in ESCALATED)
    return {
        "kpis": {
            "open_cases": len(open_cases), "sla_at_risk": len(at_risk), "sla_breached": len(breached),
            "sars_filed": s.scalar(select(func.count()).select_from(Filing)) or 0,
            "avg_days_to_close": round(sum(durations) / len(durations), 1) if durations else None,
            "agent_agreement": round(agree / total, 3) if total else None, "agent_reviewed": total,
            "transactions": s.scalar(select(func.count()).select_from(Transaction)) or 0,
            "alerts": s.scalar(select(func.count()).select_from(Alert)) or 0,
            "feeds": s.scalar(select(func.count()).select_from(Feed)) or 0,
        },
        "funnel": funnel,
        "trend": [{"day": d.isoformat(), "opened": opened[d], "closed": closed_by[d]} for d in days],
        "scenarios": [{"scenario": k, "title": describe(k)["title"], "alerts": v, "closed": conv[k][0], "escalated": conv[k][1]}
                      for k, v in alerts_by.most_common()],
        "priority": dict(Counter(c.priority for c in open_cases)),
        "recent_events": [{"ts": e.ts.isoformat(), "case_id": e.case_id, "actor": e.actor, "actor_kind": e.actor_kind, "kind": e.kind}
                          for e in s.scalars(select(CaseEvent).order_by(CaseEvent.ts.desc(), CaseEvent.id.desc()).limit(12))],
    }
