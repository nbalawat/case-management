"""Management information: one period of the operation as figures a committee reads, with the months before it for comparison.

Every figure is counted from the operational store when asked for, over every case at the client whoever is asking:
this is a management view, and it shows counts and people's workloads, never a case's contents.
"""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import library, load_case_types
from ..detection.scenarios import describe
from ..domain.models import Alert, Case, CaseEvent, Dispute, Filing, Hold, Lookback, ScenarioVersion, now
from ..domain.personas import policy_of
from ..tenancy import tenant_of
from ..visibility import system_work

VIEW = "view_team"
POSITIVE = {"sar_filed", "fraud_confirmed", "true_match", "exit", "declined"}
QA = "qa_review"
AGES = [("Up to 7 days", 0, 7), ("8 to 30 days", 8, 30), ("31 to 60 days", 31, 60), ("Over 60 days", 61, 10**6)]


class MiError(ValueError):
    pass


def month_of(period: str | None, today: datetime | None = None) -> tuple[datetime, datetime, str]:
    """The start and end of a period given as YYYY-MM (this month when not given), and its label."""
    today = today or now()
    if period:
        try:
            start = datetime.strptime(period, "%Y-%m")
        except ValueError:
            raise MiError("A period is a month, written YYYY-MM") from None
    else:
        start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start > today:
        raise MiError("That month has not started")
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end, start.strftime("%Y-%m")


def _months(start: datetime, n: int) -> list[tuple[datetime, datetime, str]]:
    out, cur = [], start
    for _ in range(n):
        out.append(month_of(cur.strftime("%Y-%m"), today=cur + timedelta(days=40)))
        cur = (cur - timedelta(days=1)).replace(day=1)
    return out[::-1]


def _within(ts: datetime | None, start: datetime, end: datetime) -> bool:
    return ts is not None and start <= ts < end


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


@system_work
def report(s: Session, period: str | None = None, months: int = 6) -> dict:
    today = now()
    start, end, label = month_of(period, today)
    as_of = min(end, today)
    tenant = tenant_of(s)
    types = library() | load_case_types(tenant)
    people = policy_of(tenant).people
    cases = list(s.scalars(select(Case)))
    work = [c for c in cases if c.case_type != QA]  # QA reviews are counted as quality, not as casework
    alerts = list(s.scalars(select(Alert)))
    filings = list(s.scalars(select(Filing)))
    opened = [c for c in work if _within(c.created, start, end)]
    closed = [c for c in work if _within(c.closed, start, end)]
    backlog = [c for c in work if c.created < as_of and (c.closed is None or c.closed >= as_of)]
    in_time = [c for c in closed if c.sla_due is None or c.closed <= c.sla_due]
    days = [(c.closed - c.created).total_seconds() / 86400 for c in closed]

    by_type = []
    for ct in sorted({c.case_type for c in work}):
        mine_closed = [c for c in closed if c.case_type == ct]
        by_type.append({"case_type": ct, "title": types[ct]["title"] if ct in types else ct, "opened": sum(c.case_type == ct for c in opened),
                        "closed": len(mine_closed), "backlog": sum(c.case_type == ct for c in backlog),
                        "productive": sum(c.disposition in POSITIVE for c in mine_closed),
                        "median_days": round(median((c.closed - c.created).total_seconds() / 86400 for c in mine_closed), 1) if mine_closed else None,
                        "in_time": _pct(sum(c.sla_due is None or c.closed <= c.sla_due for c in mine_closed), len(mine_closed))})

    open_now = [c for c in work if c.closed is None]
    ageing = [{"label": name, "count": sum(lo <= (today - c.created).days <= hi for c in open_now),
               "overdue": sum(lo <= (today - c.created).days <= hi and c.sla_due is not None and c.sla_due < today for c in open_now)} for name, lo, hi in AGES]

    case_of = {c.id: c for c in cases}
    scenarios = []
    for name in sorted({a.scenario for a in alerts}):
        mine = [a for a in alerts if a.scenario == name and _within(a.created, start, end)]
        ever = [a for a in alerts if a.scenario == name]
        decided = [a for a in ever if (c := case_of.get(a.case_id)) is not None and c.closed is not None and c.disposition]
        good = sum(case_of[a.case_id].disposition in POSITIVE for a in decided)
        scenarios.append({"scenario": name, "title": describe(name)["title"], "alerts": len(mine), "auto_closed": sum(a.status == "auto_closed" for a in mine),
                          "waiting": sum(a.status == "hibernated" for a in mine), "decided_ever": len(decided), "productive_ever": good, "productive_rate": _pct(good, len(decided))})

    dispositions = Counter((c.case_type, c.disposition or "closed") for c in closed)
    sent = [f for f in filings if _within(f.submitted_at, start, end)]
    late = [f for f in sent if f.due is not None and f.submitted_at > f.due]
    filing_rows = [{"kind": k, "sent": sum(f.kind == k for f in sent), "late": sum(f.kind == k for f in late),
                    "accepted": sum(f.kind == k and f.status == "acknowledged" for f in sent), "rejected": sum(f.kind == k and f.status == "rejected" for f in sent)}
                   for k in sorted({f.kind for f in sent})]

    breaches = Counter()
    closed_by: dict[str, str] = {}
    for e in s.scalars(select(CaseEvent).where(CaseEvent.ts >= start, CaseEvent.ts < end, CaseEvent.kind.in_(("sla.stage_breached", "case.state_changed")))):
        if e.kind == "sla.stage_breached":
            breaches[case_of[e.case_id].case_type if e.case_id in case_of else "unknown"] += 1
        elif e.actor_kind == "human":
            closed_by[e.case_id] = e.actor  # the last person to move it in the period: for a closed case, who closed it
    per_person: dict[str, dict] = defaultdict(lambda: {"closed": 0, "days": [], "productive": 0})
    for c in closed:
        who = closed_by.get(c.id) or c.owner
        if who:
            row = per_person[who]
            row["closed"] += 1
            row["days"].append((c.closed - c.created).total_seconds() / 86400)
            row["productive"] += int(c.disposition in POSITIVE)
    reviews = [c for c in cases if c.case_type == QA and _within(c.closed, start, end)]
    qa_by: dict[str, list[bool]] = defaultdict(list)
    for r in reviews:
        qa_by[(r.data or {}).get("closed_by") or "unknown"].append(r.disposition == "qa_passed")
    owned = Counter(c.owner for c in open_now if c.owner)
    staff = [{"id": pid, "name": people[pid].name if pid in people else pid, "title": people[pid].title if pid in people else "",
              "closed": row["closed"], "median_days": round(median(row["days"]), 1) if row["days"] else None, "productive": row["productive"],
              "open_now": owned.get(pid, 0), "qa_reviewed": len(qa_by.get(pid, [])), "qa_passed": sum(qa_by.get(pid, []))}
             for pid, row in sorted(per_person.items(), key=lambda kv: -kv[1]["closed"])]
    for pid in sorted(set(owned) - set(per_person)):
        staff.append({"id": pid, "name": people[pid].name if pid in people else pid, "title": people[pid].title if pid in people else "", "closed": 0,
                      "median_days": None, "productive": 0, "open_now": owned[pid], "qa_reviewed": len(qa_by.get(pid, [])), "qa_passed": sum(qa_by.get(pid, []))})

    advised = [c for c in closed if c.agent_recommendation]
    agreed = sum((c.disposition in POSITIVE) == (c.agent_recommendation == "escalate") for c in advised)
    disputes = [d for d in s.scalars(select(Dispute)) if _within(d.closed_at, start, end)]
    holds = [h for h in s.scalars(select(Hold)) if _within(h.decided_at, start, end)]

    trend = []
    for m_start, m_end, m_label in _months(start, months):
        cut = min(m_end, today)
        trend.append({"month": m_label, "alerts": sum(_within(a.created, m_start, m_end) for a in alerts), "opened": sum(_within(c.created, m_start, m_end) for c in work),
                      "closed": sum(_within(c.closed, m_start, m_end) for c in work),
                      "backlog": sum(c.created < cut and (c.closed is None or c.closed >= cut) for c in work),
                      "filings": sum(_within(f.submitted_at, m_start, m_end) for f in filings)})

    return {
        "period": label, "from": start.isoformat(), "to": end.isoformat(), "partial": end > today, "generated": today.isoformat(),
        "periods": [m[2] for m in _months(today.replace(day=1, hour=0, minute=0, second=0, microsecond=0), 12)][::-1],
        "headline": {"alerts": sum(_within(a.created, start, end) for a in alerts), "opened": len(opened), "closed": len(closed), "backlog": len(backlog),
                     "median_days": round(median(days), 1) if days else None, "in_time": _pct(len(in_time), len(closed)),
                     "productive": sum(c.disposition in POSITIVE for c in closed), "filings_sent": len(sent), "filings_late": len(late),
                     "stage_breaches": sum(breaches.values())},
        "trend": trend, "case_types": by_type, "ageing": ageing, "scenarios": scenarios,
        "outcomes": [{"case_type": ct, "title": types[ct]["title"] if ct in types else ct, "disposition": d, "count": n}
                     for (ct, d), n in sorted(dispositions.items(), key=lambda kv: (kv[0][0], -kv[1]))],
        "filings": filing_rows, "breaches": [{"case_type": ct, "title": types[ct]["title"] if ct in types else ct, "count": n} for ct, n in breaches.most_common()],
        "people": staff,
        "quality": {"reviewed": len(reviews), "passed": sum(r.disposition == "qa_passed" for r in reviews), "failed": sum(r.disposition == "qa_failed" for r in reviews),
                    "pass_rate": _pct(sum(r.disposition == "qa_passed" for r in reviews), len(reviews))},
        "agents": {"advised": len(advised), "agreed": agreed, "agreement": _pct(agreed, len(advised)),
                   "auto_closed": sum(a.status == "auto_closed" and _within(a.created, start, end) for a in alerts)},
        "fraud": {"disputes_closed": len(disputes), "claimed": round(sum(d.amount for d in disputes), 2), "recovered": round(sum(d.recovered for d in disputes), 2),
                  "loss": round(sum(d.loss for d in disputes), 2)},
        "sanctions": {"decided": len(holds), **{k: sum(h.status == k for h in holds) for k in ("released", "blocked", "rejected")}},
        "detection": {"changes_live": sum(_within(v.decided_at, start, end) and v.status in ("live", "superseded") for v in s.scalars(select(ScenarioVersion))),
                      "lookbacks_open": sum(lb.status in ("planned", "working") for lb in s.scalars(select(Lookback)))},
    }


def rows(r: dict) -> list[tuple[str, str, str, object]]:
    """The report as flat rows (section, measure, of what, value), for a spreadsheet or another system."""
    out: list[tuple[str, str, str, object]] = [("Headline", k.replace("_", " "), "", v) for k, v in r["headline"].items()]
    for t in r["trend"]:
        out += [("Trend", k, t["month"], t[k]) for k in ("alerts", "opened", "closed", "backlog", "filings")]
    for t in r["case_types"]:
        out += [("Case types", k.replace("_", " "), t["title"], t[k]) for k in ("opened", "closed", "backlog", "productive", "median_days", "in_time")]
    out += [("Open cases by age", "open", a["label"], a["count"]) for a in r["ageing"]] + [("Open cases by age", "past deadline", a["label"], a["overdue"]) for a in r["ageing"]]
    for sc in r["scenarios"]:
        out += [("Scenarios", k.replace("_", " "), sc["title"], sc[k]) for k in ("alerts", "auto_closed", "waiting", "decided_ever", "productive_ever", "productive_rate")]
    out += [("Outcomes", o["disposition"].replace("_", " "), o["title"], o["count"]) for o in r["outcomes"]]
    for f in r["filings"]:
        out += [("Reports to regulators", k, f["kind"], f[k]) for k in ("sent", "late", "accepted", "rejected")]
    out += [("Stage deadlines missed", "count", b["title"], b["count"]) for b in r["breaches"]]
    for p in r["people"]:
        out += [("People", k.replace("_", " "), p["name"], p[k]) for k in ("closed", "median_days", "productive", "open_now", "qa_reviewed", "qa_passed")]
    for section in ("quality", "agents", "fraud", "sanctions", "detection"):
        out += [(section.capitalize(), k.replace("_", " "), "", v) for k, v in r[section].items()]
    return out


def as_csv(r: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["period", "section", "measure", "of", "value"])
    for section, measure, of, value in rows(r):
        w.writerow([r["period"], section, measure, of, "" if value is None else value])
    return buf.getvalue()


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _share(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def as_pack(r: dict, client: str) -> str:
    """The period as a short paper for a committee: the figures that matter, in sentences, then the tables."""
    h = r["headline"]
    lines = [f"# Financial crime operations: {r['period']}", "", f"{client}. Prepared {r['generated'][:10]}" + (" (the month is not over)." if r["partial"] else "."), "",
             "## In brief", "",
             f"- {_n(h['alerts'], 'alert')} raised and {_n(h['opened'], 'case')} opened; {_n(h['closed'], 'case')} closed, leaving {h['backlog']} open.",
             f"- Half of the closed cases took {h['median_days']} days or less; {_share(h['in_time'])} closed inside their deadline." if h["closed"] else "- No case was closed in the period.",
             f"- {_n(h['productive'], 'closed case')} ended in a filing or a confirmed concern.",
             f"- {_n(h['filings_sent'], 'report')} sent to regulators, {h['filings_late']} after the due date.",
             f"- {_n(h['stage_breaches'], 'stage deadline')} missed.",
             f"- Quality assurance reviewed {_n(r['quality']['reviewed'], 'case')} and passed {_share(r['quality']['pass_rate'])}.",
             f"- People agreed with the agents' recommendation on {_share(r['agents']['agreement'])} of the {_n(r['agents']['advised'], 'closed case')} that had one.",
             f"- Fraud disputes closed: {r['fraud']['disputes_closed']}, with ${r['fraud']['loss']:,.0f} lost and ${r['fraud']['recovered']:,.0f} recovered.",
             f"- Sanctions holds decided: {r['sanctions']['decided']} ({r['sanctions']['blocked']} blocked, {r['sanctions']['rejected']} rejected, {r['sanctions']['released']} released).",
             "", "## Month by month", "", "| Month | Alerts | Opened | Closed | Open at month end | Reports sent |", "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| {t['month']} | {t['alerts']} | {t['opened']} | {t['closed']} | {t['backlog']} | {t['filings']} |" for t in r["trend"]]
    lines += ["", "## By kind of case", "", "| Kind | Opened | Closed | Open | Filing or confirmed concern | Median days | Inside deadline |", "|---|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {t['title']} | {t['opened']} | {t['closed']} | {t['backlog']} | {t['productive']} | {t['median_days'] if t['median_days'] is not None else 'n/a'} | {_share(t['in_time'])} |"
              for t in r["case_types"]]
    lines += ["", "## Open cases by age", "", "| Age | Open | Past deadline |", "|---|---:|---:|"] + [f"| {a['label']} | {a['count']} | {a['overdue']} |" for a in r["ageing"]]
    lines += ["", "## Detection", "", "| Scenario | Alerts in the period | Of all decided, led to a filing or confirmed concern |", "|---|---:|---:|"]
    lines += [f"| {sc['title']} | {sc['alerts']} | {_share(sc['productive_rate'])} of {sc['decided_ever']} |" for sc in r["scenarios"]]
    return "\n".join(lines) + "\n"
