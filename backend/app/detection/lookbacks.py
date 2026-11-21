"""Lookback projects: a past period run again through one scenario, usually on different settings, and worked to a conclusion.

A project is planned with a count of what it would raise before it raises anything. Running it raises only what was
never alerted at the time, as alerts marked with the project, and every one goes to a person: no agent closes a
lookback alert and no rule lets it wait. The project closes when its cases have, with the figures frozen as they stood.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.service import record
from ..domain.models import Alert, Case, Filing, Lookback, Party, new_id, now
from ..domain.personas import Persona
from ..visibility import system_work
from . import tuning
from .scenarios import REGISTRY, Hit, describe, tuned, Tuning

MANAGE = tuning.TUNE
SIGN_OFF = tuning.APPROVE
LIMIT = 500  # the most one run may raise: a larger project is split by period or segment
RATINGS = ("low", "medium", "high")


class LookbackError(ValueError):
    pass


def _get(s: Session, lookback_id: str) -> Lookback:
    lb = s.get(Lookback, lookback_id)
    if lb is None:
        raise LookbackError("Lookback not found")
    return lb


def _note(lb: Lookback, by: str, what: str, note: str = "", at: datetime | None = None) -> None:
    lb.history = [*lb.history, {"at": (at or now()).isoformat(), "by": by, "what": what, "note": note}]


def _source(lb: Lookback) -> str:
    return f"lookback:{lb.id}"


@system_work
def _population(s: Session, lb: Lookback) -> set[str] | None:
    ratings = lb.segment.get("risk_ratings") or []
    return set(s.scalars(select(Party.id).where(Party.risk_rating.in_(ratings)))) if ratings else None


def _found(s: Session, lb: Lookback) -> tuple[dict[str, tuple[str, Hit]], dict[str, dict]]:
    """Everything the period holds on the project's settings, and what is already known about each."""
    found = tuning.hits(s, lb.scenario, lb.params, lb.period_from, lb.period_to, _population(s, lb))
    return found, tuning.outcomes(s, set(found))


@system_work
def preview(s: Session, lb: Lookback) -> dict:
    """What running the project would raise, counted before anything is raised."""
    found, known = _found(s, lb)
    fresh = {k: v for k, v in found.items() if k not in known}
    names = {p.id: p.name for p in s.scalars(select(Party).where(Party.id.in_({pid for pid, _ in fresh.values()})))} if fresh else {}
    then = [x["outcome"] for x in known.values()]
    sample = sorted(fresh.items(), key=lambda kv: -kv[1][1].score)[:tuning.SAMPLE]
    return {"at": now().isoformat(), "found": len(found), "already_alerted": len(known),
            "already": {x: then.count(x) for x in ("productive", "unproductive", "undecided")},
            "to_raise": len(fresh), "customers": len({pid for pid, _ in fresh.values()}), "over_limit": len(fresh) > LIMIT,
            "sample": [{"party_id": pid, "party_name": names.get(pid, pid), "score": round(h.score, 2), "explanation": h.explanation} for _, (pid, h) in sample]}


def _day(value: str, what: str, end: bool = False) -> datetime:
    try:
        d = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise LookbackError(f"{what} is not a date") from None
    return d.replace(hour=23, minute=59, second=59) if end and len(value) <= 10 else d


def create(s: Session, persona: Persona, name: str, reason: str, scenario: str, params: dict, period_from: str, period_to: str,
           risk_ratings: list[str] | None = None, due: str | None = None, at: datetime | None = None) -> Lookback:
    if not persona.can(MANAGE):
        raise LookbackError(f"Planning a lookback needs the '{MANAGE}' permission")
    if len(name.strip()) < 5:
        raise LookbackError("Give the lookback a name people will recognise")
    if len(reason.strip()) < 20:
        raise LookbackError("Say why it is being done: a regulator's finding, a defect, a new typology")
    try:
        wanted = tuning.validate(scenario, params)
    except tuning.TuningError as e:
        raise LookbackError(str(e)) from None
    start, end = _day(period_from, "The start of the period"), _day(period_to, "The end of the period", end=True)
    if start >= end:
        raise LookbackError("The period must start before it ends")
    if end > now():
        raise LookbackError("The period must be in the past: what happens from now on is ordinary detection")
    ratings = sorted(set(risk_ratings or []))
    if any(r not in RATINGS for r in ratings):
        raise LookbackError(f"Risk ratings are {', '.join(RATINGS)}")
    lb = Lookback(id=new_id("lbk"), name=name.strip()[:160], reason=reason.strip()[:2000], scenario=scenario, params=wanted, period_from=start,
                  period_to=end, segment={"risk_ratings": ratings} if ratings else {}, due=_day(due, "The date it is due", end=True) if due else None,
                  created_by=persona.id, created=at or now(), history=[])
    s.add(lb)
    _note(lb, persona.id, "planned", at=at)
    lb.preview = preview(s, lb)
    s.flush()
    return lb


@system_work
def run(s: Session, lookback_id: str, persona: Persona, at: datetime | None = None) -> Lookback:
    """Raise what the period holds and was never alerted, and send every alert to a person."""
    from ..alerts.hub import route_alerts

    lb = _get(s, lookback_id)
    if not persona.can(MANAGE):
        raise LookbackError(f"Running a lookback needs the '{MANAGE}' permission")
    if lb.status != "planned":
        raise LookbackError(f"This lookback is already {lb.status}")
    at = at or now()
    found, known = _found(s, lb)
    fresh = {k: v for k, v in found.items() if k not in known}
    if len(fresh) > LIMIT:
        raise LookbackError(f"This would raise {len(fresh)} alerts; one run may raise {LIMIT}. Split it by period or by customer risk rating")
    version = f"{REGISTRY[lb.scenario]['version']}/lb"
    alerts = []
    for key, (pid, h) in sorted(fresh.items()):
        a = Alert(id=new_id("alr"), dedupe_key=key, source=_source(lb), scenario=lb.scenario, scenario_version=version, party_id=pid, score=round(h.score, 2),
                  explanation=f"Lookback '{lb.name}': {h.explanation}", txn_ids=h.txn_ids, created=at,
                  policy_note="Raised by a lookback, so it goes to a person: no agent closes it and no rule lets it wait.")
        s.add(a)
        alerts.append(a)
    s.flush()
    cases = route_alerts(s, alerts, screened=True, woken=True)
    for c in cases:
        c.data = dict(c.data or {}) | {"lookbacks": sorted({*(c.data or {}).get("lookbacks", []), lb.id})}
        record(s, c.id, persona.id, "human", "lookback.alerts", {"lookback": lb.id, "name": lb.name}, ts=at)
    lb.status, lb.run_by, lb.run_at = "working", persona.id, at
    lb.preview = lb.preview | {"raised": len(alerts), "cases": len(cases)}
    _note(lb, persona.id, "run", f"{len(alerts)} alert(s) raised into {len(cases)} case(s); {len(known)} had been alerted at the time", at)
    s.flush()
    return lb


@system_work
def progress(s: Session, lb: Lookback) -> dict:
    """Where the project's work stands. Counts cover every case, whoever is asking."""
    alerts = list(s.scalars(select(Alert).where(Alert.source == _source(lb))))
    cases = {c.id: c for c in s.scalars(select(Case).where(Case.id.in_({a.case_id for a in alerts if a.case_id})))} if alerts else {}
    closed = [c for c in cases.values() if c.closed is not None]
    productive = [c for c in closed if c.disposition in tuning.POSITIVE]
    filings = s.scalars(select(Filing.id).where(Filing.case_id.in_(cases), Filing.kind.in_(("SAR", "STR")), Filing.status != "superseded")).all() if cases else []
    by_outcome: dict[str, int] = {}
    for c in closed:
        by_outcome[c.disposition or "closed"] = by_outcome.get(c.disposition or "closed", 0) + 1
    return {"alerts": len(alerts), "customers": len({a.party_id for a in alerts}), "cases": len(cases), "open": len(cases) - len(closed), "closed": len(closed),
            "productive": len(productive), "filings": len(filings), "by_outcome": by_outcome, "case_ids": sorted(cases),
            "complete": round(len(closed) / len(cases), 3) if cases else (1.0 if lb.status != "planned" else 0.0)}


def close(s: Session, lookback_id: str, conclusion: str, persona: Persona, at: datetime | None = None) -> Lookback:
    lb = _get(s, lookback_id)
    if not persona.can(SIGN_OFF):
        raise LookbackError(f"Signing off a lookback needs the '{SIGN_OFF}' permission")
    if lb.status != "working":
        raise LookbackError("Only a lookback that has been run can be signed off" if lb.status == "planned" else f"This lookback is already {lb.status}")
    if persona.id == lb.run_by:
        raise LookbackError("A second person must sign it off: you ran this lookback")
    figures = progress(s, lb)
    if figures["open"]:
        raise LookbackError(f"{figures['open']} of its cases are still open. It can be signed off when they are decided")
    if len(conclusion.strip()) < 40:
        raise LookbackError("Write the conclusion: what was found, what was filed and what changes as a result")
    at = at or now()
    lb.status, lb.closed_by, lb.closed_at, lb.conclusion = "closed", persona.id, at, conclusion.strip()[:4000]
    lb.report = {k: v for k, v in figures.items() if k != "case_ids"} | {"already_alerted": lb.preview.get("already_alerted", 0), "found": lb.preview.get("found", 0)}
    _note(lb, persona.id, "signed off", at=at)
    s.flush()
    return lb


def cancel(s: Session, lookback_id: str, note: str, persona: Persona) -> Lookback:
    lb = _get(s, lookback_id)
    if not persona.can(MANAGE):
        raise LookbackError(f"Cancelling a lookback needs the '{MANAGE}' permission")
    if lb.status != "planned":
        raise LookbackError("Only a lookback that has not been run can be cancelled: once alerts are raised they are worked")
    if len(note.strip()) < 10:
        raise LookbackError("Say why it is cancelled")
    lb.status = "cancelled"
    _note(lb, persona.id, "cancelled", note.strip()[:500])
    s.flush()
    return lb


def refresh(s: Session, lookback_id: str, persona: Persona) -> Lookback:
    """Count again what a planned project would raise: data may have arrived since it was planned."""
    lb = _get(s, lookback_id)
    if not persona.can(MANAGE):
        raise LookbackError(f"This needs the '{MANAGE}' permission")
    if lb.status != "planned":
        raise LookbackError("The count is fixed once a lookback has been run")
    lb.preview = preview(s, lb)
    s.flush()
    return lb


def row(s: Session, lb: Lookback, full: bool = False) -> dict:
    at = now()
    out = {"id": lb.id, "name": lb.name, "reason": lb.reason, "scenario": lb.scenario, "scenario_title": describe(lb.scenario)["title"], "params": lb.params,
           "defaults": REGISTRY[lb.scenario]["params"], "period_from": lb.period_from.isoformat(), "period_to": lb.period_to.isoformat(),
           "risk_ratings": lb.segment.get("risk_ratings", []), "status": lb.status, "due": lb.due.isoformat() if lb.due else None,
           "overdue": lb.status in ("planned", "working") and lb.due is not None and lb.due < at, "created_by": lb.created_by, "created": lb.created.isoformat(),
           "run_by": lb.run_by, "run_at": lb.run_at.isoformat() if lb.run_at else None, "closed_by": lb.closed_by,
           "closed_at": lb.closed_at.isoformat() if lb.closed_at else None, "conclusion": lb.conclusion, "preview": lb.preview,
           "progress": (lb.report | {"case_ids": progress(s, lb)["case_ids"]}) if lb.report else progress(s, lb)}
    if full:
        out["history"] = lb.history
    return out


def rows(s: Session) -> list[dict]:
    order = {"working": 0, "planned": 1, "closed": 2, "cancelled": 3}
    return sorted((row(s, lb, full=True) for lb in s.scalars(select(Lookback))), key=lambda r: (order.get(r["status"], 9), r["created"]), reverse=False)


def report_text(s: Session, lookback_id: str) -> str:
    """The project as a document someone outside the platform can read: scope, figures, conclusion."""
    lb = _get(s, lookback_id)
    r = row(s, lb, full=True)
    p, pv = r["progress"], lb.preview
    changed = {k: v for k, v in lb.params.items() if REGISTRY[lb.scenario]["params"].get(k) != v}
    limits = REGISTRY[lb.scenario]["limits"]
    lines = [f"# Lookback: {lb.name}", "", f"Status: {lb.status}" + (f", signed off by {lb.closed_by} on {lb.closed_at.date().isoformat()}" if lb.closed_at else ""),
             "", "## Why", "", lb.reason, "", "## Scope", "",
             f"- Scenario: {r['scenario_title']}",
             f"- Period: {lb.period_from.date().isoformat()} to {lb.period_to.date().isoformat()}",
             f"- Customers: {'risk rating ' + ', '.join(r['risk_ratings']) if r['risk_ratings'] else 'all'}",
             "- Settings: " + ("; ".join(f"{limits[k][0] if k in limits else k} {v:g} (ordinarily {REGISTRY[lb.scenario]['params'][k]:g})" for k, v in changed.items())
                               or "the ordinary settings"),
             "", "## What the period held", "",
             f"- Found on these settings: {pv.get('found', 0)}",
             f"- Already alerted at the time: {pv.get('already_alerted', 0)}",
             f"- Raised by this lookback: {p['alerts']} alert(s) on {p['customers']} customer(s), in {p['cases']} case(s)",
             "", "## Outcome", "",
             f"- Cases decided: {p['closed']} of {p['cases']}",
             f"- Ended in a filing or a confirmed concern: {p['productive']}",
             f"- Suspicious activity reports filed from these cases: {p['filings']}"]
    lines += [f"- {k.replace('_', ' ').capitalize()}: {n}" for k, n in sorted(p["by_outcome"].items())]
    lines += ["", "## Conclusion", "", lb.conclusion or "Not yet signed off.", "", "## Record", ""]
    lines += [f"- {h['at'][:16].replace('T', ' ')} {h['by']}: {h['what']}{' - ' + h['note'] if h['note'] else ''}" for h in lb.history]
    return "\n".join(lines) + "\n"
