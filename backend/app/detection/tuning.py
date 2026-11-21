"""A client's own detection settings: proposed with a backtest by one person, put live by another.

Scenarios declare their defaults and the range each setting may move within. A client's version holds the complete
settings, so what ran on any day can be read from one row. Nothing here raises or closes an alert: a change applies to
the next detection run, and looking backwards is a lookback project's job.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Case, Party, ScenarioVersion, new_id, now
from ..domain.personas import Persona
from ..tenancy import tenant_of
from ..visibility import system_work
from .scenarios import REGISTRY, Hit, Tuning, tuned

TUNE = "tune_detection"
APPROVE = "approve_tuning"
POSITIVE = {"sar_filed", "fraud_confirmed", "true_match", "exit", "declined"}  # outcomes that mean the concern was real
SAMPLE = 25


class TuningError(ValueError):
    pass


def tunable(name: str) -> bool:
    return bool(REGISTRY.get(name, {}).get("limits"))


def _latest_live(s: Session) -> dict[str, ScenarioVersion]:
    out: dict[str, ScenarioVersion] = {}
    for v in s.scalars(select(ScenarioVersion).where(ScenarioVersion.status == "live").order_by(ScenarioVersion.number)):
        out[v.scenario] = v
    return out


@system_work
def live(s: Session) -> Tuning:
    """What the next run detects with."""
    versions = _latest_live(s)
    return Tuning(params={k: dict(v.params) for k, v in versions.items()}, revisions={k: v.number for k, v in versions.items()},
                  off=frozenset(k for k, v in versions.items() if not v.enabled))


def current(s: Session, name: str) -> tuple[dict, bool, int]:
    """The settings in force for one scenario, whether it is switched on, and which revision that is (0 for the defaults)."""
    v = _latest_live(s).get(name)
    return (REGISTRY[name]["params"] | (v.params if v else {}), v.enabled if v else True, v.number if v else 0)


def validate(name: str, params: dict) -> dict:
    """The complete settings a proposal asks for, or the reason it cannot be made."""
    if name not in REGISTRY:
        raise TuningError("Scenario not found")
    sc = REGISTRY[name]
    if not sc["limits"]:
        raise TuningError(f"'{sc['title']}' is not set here")
    out = dict(sc["params"])
    for key, value in (params or {}).items():
        if key not in sc["limits"]:
            raise TuningError(f"'{key}' is not a setting of this scenario that a client can change")
        label, lo, hi = sc["limits"][key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TuningError(f"{label}: a number is needed")
        if isinstance(sc["params"][key], int) and float(value) != int(value):
            raise TuningError(f"{label}: a whole number is needed")
        if not lo <= value <= hi:
            raise TuningError(f"{label}: must be between {lo:g} and {hi:g}")
        out[key] = int(value) if isinstance(sc["params"][key], int) else float(value)
    if "min" in out and "max" in out and out["min"] >= out["max"]:
        raise TuningError("The smallest amount counted must be below the largest")
    return out


@system_work
def hits(s: Session, name: str, params: dict, since: datetime | None = None, until: datetime | None = None,
         party_ids: set[str] | None = None) -> dict[str, tuple[str, Hit]]:
    """What one scenario finds with the given settings, keyed as alerts are, so two runs can be compared. Writes nothing."""
    from .history import by_party  # the operational store, or the lake when DETECTION_HISTORY=lake

    out: dict[str, tuple[str, Hit]] = {}
    with tuned(Tuning(params={name: params})):
        for pid, txns in by_party(s, since, until, party_ids):
            for h in REGISTRY[name]["fn"](txns):
                out.setdefault(f"{h.scenario}:{pid}:{h.window_key}", (pid, h))
    return out


@system_work
def outcomes(s: Session, keys: set[str]) -> dict[str, dict]:
    """What became of the alerts with these keys: the case each joined and how it ended."""
    out: dict[str, dict] = {}
    for a in s.scalars(select(Alert).where(Alert.dedupe_key.in_(keys))) if keys else []:
        c = s.get(Case, a.case_id) if a.case_id else None
        if c is not None and c.closed is not None and c.disposition:
            result = "productive" if c.disposition in POSITIVE else "unproductive"
        elif a.status in ("auto_closed", "expired"):
            result = "unproductive"
        else:
            result = "undecided"
        out[a.dedupe_key] = {"alert_id": a.id, "case_id": a.case_id, "outcome": result, "disposition": c.disposition if c else None}
    return out


def _item(s: Session, key: str, pid: str, h: Hit, known: dict[str, dict]) -> dict:
    party = s.get(Party, {"tenant_id": tenant_of(s), "id": pid})
    return {"key": key, "party_id": pid, "party_name": party.name if party else pid, "score": round(h.score, 2), "explanation": h.explanation,
            **(known.get(key) or {"alert_id": None, "case_id": None, "outcome": "never_raised", "disposition": None})}


@system_work
def backtest(s: Session, name: str, params: dict, enabled: bool = True, days: int = 365) -> dict:
    """What past alerts would have looked like on these settings, against the settings in force."""
    since = now() - timedelta(days=days)
    in_force, on_now, _ = current(s, name)
    before = hits(s, name, in_force, since) if on_now else {}
    after = hits(s, name, params, since) if enabled else {}
    known = outcomes(s, set(before) | set(after))
    removed = [_item(s, k, *before[k], known) for k in sorted(set(before) - set(after))]
    added = [_item(s, k, *after[k], known) for k in sorted(set(after) - set(before))]
    kept = [known.get(k, {}).get("outcome", "never_raised") for k in set(before) & set(after)]

    def split(rows: list[dict]) -> dict:
        return {x: sum(r["outcome"] == x for r in rows) for x in ("productive", "unproductive", "undecided", "never_raised")}

    removed.sort(key=lambda r: (r["outcome"] != "productive", -r["score"]))
    added.sort(key=lambda r: -r["score"])
    lost = split(removed)["productive"]
    productive_now = lost + sum(k == "productive" for k in kept)
    return {
        "days": days, "ran_at": now().isoformat(), "from": in_force, "to": params, "enabled": enabled,
        "alerts_now": len(before), "alerts_then": len(after), "kept": len(kept),
        "removed": {"count": len(removed), **split(removed), "items": removed[:SAMPLE]},
        "added": {"count": len(added), "customers": len({r["party_id"] for r in added}), "items": added[:SAMPLE]},
        "productive_now": productive_now, "productive_lost": lost,
        "summary": (f"Over the last {days} days: {len(before)} alert(s) on the settings in force, {len(after)} on these. "
                    f"{len(removed)} would not have been raised, {lost} of which led to a filing or a confirmed concern. "
                    f"{len(added)} more would have been raised."),
        "caution": "Alerts are compared by customer and window. A change to how a window is counted can move an alert between windows.",
    }


def _get(s: Session, version_id: str) -> ScenarioVersion:
    v = s.get(ScenarioVersion, version_id)
    if v is None:
        raise TuningError("Proposal not found")
    return v


def propose(s: Session, name: str, params: dict, enabled: bool, reason: str, persona: Persona, at: datetime | None = None, days: int = 365) -> ScenarioVersion:
    if not persona.can(TUNE):
        raise TuningError(f"Proposing a change to detection needs the '{TUNE}' permission")
    wanted = validate(name, params)
    if len(reason.strip()) < 20:
        raise TuningError("Say why, in a sentence: the approver and audit will read it")
    if s.scalars(select(ScenarioVersion.id).where(ScenarioVersion.scenario == name, ScenarioVersion.status == "pending")).first():
        raise TuningError("A change to this scenario is already waiting for a decision")
    in_force, on_now, _ = current(s, name)
    if wanted == in_force and enabled == on_now:
        raise TuningError("These are the settings already in force")
    number = (s.scalar(select(func.max(ScenarioVersion.number)).where(ScenarioVersion.scenario == name)) or 0) + 1
    v = ScenarioVersion(id=new_id("scv"), scenario=name, number=number, params=wanted, enabled=enabled, reason=reason.strip()[:2000],
                        backtest=backtest(s, name, wanted, enabled, days), proposed_by=persona.id, proposed_at=at or now())
    s.add(v)
    s.flush()
    return v


def decide(s: Session, version_id: str, approve: bool, note: str, persona: Persona, at: datetime | None = None) -> ScenarioVersion:
    v = _get(s, version_id)
    if not persona.can(APPROVE):
        raise TuningError(f"Deciding a change to detection needs the '{APPROVE}' permission")
    if v.status != "pending":
        raise TuningError(f"This proposal is already {v.status}")
    if persona.id == v.proposed_by:
        raise TuningError("A second person must decide: you proposed this change")
    note = note.strip()
    if not approve and len(note) < 10:
        raise TuningError("Say why it is refused")
    if approve and len(note) < 20 and (v.backtest.get("productive_lost", 0) > 0 or not v.enabled):
        raise TuningError("This change would have lost alerts that mattered, or switches the scenario off. Say why that is acceptable"
                          if v.enabled else "This switches the scenario off. Say why that is acceptable")
    at = at or now()
    if approve:
        for old in s.scalars(select(ScenarioVersion).where(ScenarioVersion.scenario == v.scenario, ScenarioVersion.status == "live")):
            old.status = "superseded"
    v.status, v.decided_by, v.decided_at, v.decision_note = "live" if approve else "refused", persona.id, at, note[:2000]
    s.flush()
    return v


def withdraw(s: Session, version_id: str, persona: Persona) -> ScenarioVersion:
    v = _get(s, version_id)
    if v.status != "pending":
        raise TuningError(f"This proposal is already {v.status}")
    if persona.id != v.proposed_by:
        raise TuningError("Only the person who proposed it can withdraw it")
    v.status, v.decided_by, v.decided_at = "withdrawn", persona.id, now()
    s.flush()
    return v


def row(v: ScenarioVersion, full: bool = False) -> dict:
    bt = v.backtest or {}
    return {"id": v.id, "scenario": v.scenario, "number": v.number, "params": v.params, "enabled": v.enabled, "status": v.status, "reason": v.reason,
            "proposed_by": v.proposed_by, "proposed_at": v.proposed_at.isoformat(), "decided_by": v.decided_by,
            "decided_at": v.decided_at.isoformat() if v.decided_at else None, "decision_note": v.decision_note,
            "backtest": bt if full else {k: bt.get(k) for k in ("summary", "alerts_now", "alerts_then", "productive_lost")}}


@system_work
def _record(s: Session) -> dict[str, dict]:
    """Per scenario: how many alerts it has raised and how those that are decided ended."""
    out: dict[str, dict] = defaultdict(lambda: {"alerts": 0, "productive": 0, "unproductive": 0, "undecided": 0})
    cases = {c.id: c for c in s.scalars(select(Case))}
    for a in s.scalars(select(Alert)):
        r, c = out[a.scenario], cases.get(a.case_id)
        r["alerts"] += 1
        if c is not None and c.closed is not None and c.disposition:
            r["productive" if c.disposition in POSITIVE else "unproductive"] += 1
        elif a.status in ("auto_closed", "expired"):
            r["unproductive"] += 1
        else:
            r["undecided"] += 1
    return out


def view(s: Session, persona: Persona) -> dict:
    record = _record(s)
    versions: dict[str, list[ScenarioVersion]] = defaultdict(list)
    for v in s.scalars(select(ScenarioVersion).order_by(ScenarioVersion.number.desc())):
        versions[v.scenario].append(v)
    scenarios = []
    for name, sc in REGISTRY.items():
        params, enabled, revision = current(s, name)
        mine = versions.get(name, [])
        pending = next((v for v in mine if v.status == "pending"), None)
        r = record.get(name, {"alerts": 0, "productive": 0, "unproductive": 0, "undecided": 0})
        decided = r["productive"] + r["unproductive"]
        scenarios.append({
            "scenario": name, "title": sc["title"], "typology": sc["typology"], "version": sc["version"], "revision": revision, "enabled": enabled,
            "tunable": tunable(name), "defaults": sc["params"], "params": params,
            "limits": {k: {"label": label, "min": lo, "max": hi, "whole": isinstance(sc["params"][k], int)} for k, (label, lo, hi) in sc["limits"].items()},
            "record": r | {"productive_rate": round(r["productive"] / decided, 3) if decided else None},
            "pending": row(pending, full=True) if pending else None, "history": [row(v) for v in mine if v.status != "pending"]})
    return {"scenarios": scenarios, "can_propose": persona.can(TUNE) and not persona.read_only, "can_decide": persona.can(APPROVE) and not persona.read_only,
            "me": persona.id}
