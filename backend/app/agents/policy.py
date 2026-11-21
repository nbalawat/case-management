"""Each client's agent policy: which agents run, the autonomy each has per case type, and the limits they act inside.

The policy is versioned like the access policy. Turning an agent off or lowering its autonomy takes effect at once.
Raising an agent to "act within policy" needs a passing evaluation of exactly the settings being published.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import load_case_types
from ..domain.models import AgentPolicyVersion
from ..tenancy import tenant_of
from .catalog import LEVELS, AgentSpec, catalog

DEFAULT_LIMITS = {
    "close_alert_below": 0.62,   # triage may close an alert scoring under this, if nothing else about the customer is a concern
    "discount_hit_below": 0.80,  # the adjudicator may discount a screening hit whose name similarity is under this
    "qa_sample": 0.25,           # share of agent-closed alerts a person must check
}
DEFAULT_DECISION = {"escalate_at": 0.25}  # weight of evidence at which the recommendation becomes "escalate"
LIMIT_RANGES = {"close_alert_below": (0.0, 0.7), "discount_hit_below": (0.0, 0.99), "qa_sample": (0.05, 1.0)}
DECISION_RANGE = (0.05, 0.6)


class PolicyError(ValueError):
    pass


def default_body() -> dict:
    return {"agents": {a: {"enabled": True, "levels": {}} for a in catalog()}, "limits": dict(DEFAULT_LIMITS),
            "decision": dict(DEFAULT_DECISION), "challenger": None}


def _declared(case_type: dict | None) -> str:
    level = (case_type or {}).get("agents", {}).get("autonomy", "L2")
    return level if level in LEVELS else "L2"


def default_level(spec: AgentSpec, case_type: dict | None) -> str:
    """Where an agent starts before a client touches the dial: never above L2, never above what the case type declares."""
    if spec.kind == "on_request":
        return "L0" if _declared(case_type) == "L0" else "L1"
    cap = min(LEVELS.index(_declared(case_type)), LEVELS.index("L2"), LEVELS.index(spec.max_level))
    return LEVELS[cap]


class Policy:
    """The policy in force, with the questions the runtime asks of it."""

    def __init__(self, version: int, body: dict, tenant_id: str):
        self.version, self.body, self.tenant_id = version, body, tenant_id

    def enabled(self, agent: str) -> bool:
        return bool(self.body["agents"].get(agent, {}).get("enabled", True))

    def level(self, agent: str, case_type: str | None) -> str:
        spec = catalog()[agent]
        if not self.enabled(agent):
            return "L0"
        chosen = self.body["agents"].get(agent, {}).get("levels", {}).get(case_type or "")
        return chosen or default_level(spec, load_case_types(self.tenant_id).get(case_type or ""))

    @property
    def limits(self) -> dict:
        return DEFAULT_LIMITS | self.body.get("limits", {})

    @property
    def decision(self) -> dict:
        return DEFAULT_DECISION | self.body.get("decision", {})

    @property
    def challenger(self) -> dict | None:
        return self.body.get("challenger")


def fingerprint(body: dict) -> str:
    """Identifies what an evaluation covered: the agents' versions and every setting that changes what they decide."""
    basis = {"versions": {a: s.version for a, s in sorted(catalog().items())}, "limits": DEFAULT_LIMITS | body.get("limits", {}),
             "decision": DEFAULT_DECISION | body.get("decision", {})}
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:16]


def _row(s: Session) -> AgentPolicyVersion | None:
    return s.scalars(select(AgentPolicyVersion).order_by(AgentPolicyVersion.version.desc()).limit(1)).first()


def current(s: Session) -> Policy:
    row = _row(s)
    return Policy(row.version, default_body() | (row.body or {}), tenant_of(s)) if row else Policy(0, default_body(), tenant_of(s))


def history(s: Session) -> list[dict]:
    return [{"version": r.version, "published_by": r.published_by, "published": r.published.isoformat(), "note": r.note}
            for r in s.scalars(select(AgentPolicyVersion).order_by(AgentPolicyVersion.version.desc()))]


def clean(s: Session, body: dict) -> dict:
    """Validate a proposed policy and return it in canonical form. Raises PolicyError with a reason a person can act on."""
    if not isinstance(body, dict):
        raise PolicyError("The policy must be an object")
    types = load_case_types(tenant_of(s))
    out = default_body()
    for aid, entry in (body.get("agents") or {}).items():
        if aid not in catalog():
            raise PolicyError(f"Unknown agent '{aid}'")
        spec = catalog()[aid]
        entry = entry or {}
        levels = {}
        for ct, level in (entry.get("levels") or {}).items():
            if ct not in types:
                raise PolicyError(f"{spec.title}: this client does not run case type '{ct}'")
            if level not in LEVELS:
                raise PolicyError(f"{spec.title}: '{level}' is not an autonomy level; use L0, L1, L2 or L3")
            if not spec.allows(level):
                raise PolicyError(f"{spec.title} cannot be set above {spec.max_level}")
            if level != default_level(spec, types[ct]):
                levels[ct] = level
        out["agents"][aid] = {"enabled": bool(entry.get("enabled", True)), "levels": levels}
    for key, (lo, hi) in LIMIT_RANGES.items():
        value = (body.get("limits") or {}).get(key, DEFAULT_LIMITS[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise PolicyError(f"Limit '{key}' must be a number between {lo} and {hi}")
        out["limits"][key] = round(float(value), 3)
    out["decision"] = _decision(body.get("decision"), "decision")
    ch = body.get("challenger")
    if ch:
        if not isinstance(ch, dict) or not str(ch.get("name", "")).strip():
            raise PolicyError("A challenger needs a name")
        out["challenger"] = {"name": str(ch["name"]).strip()[:60], "decision": _decision(ch.get("decision"), "challenger decision")}
        if out["challenger"]["decision"] == out["decision"]:
            raise PolicyError("The challenger is the same as the settings in force; change its threshold")
    return out


def _decision(raw: dict | None, label: str) -> dict:
    value = (raw or {}).get("escalate_at", DEFAULT_DECISION["escalate_at"])
    lo, hi = DECISION_RANGE
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise PolicyError(f"The {label} threshold must be a number between {lo} and {hi}")
    return {"escalate_at": round(float(value), 3)}


def raised_to_act(old: Policy, new_body: dict, tenant_id: str) -> list[tuple[str, str]]:
    """(agent, case type) pairs that the new policy lets act on their own and the old one did not."""
    new = Policy(old.version + 1, new_body, tenant_id)
    return [(a, ct) for a in catalog() for ct in load_case_types(tenant_id)
            if new.level(a, ct) == "L3" and (old.level(a, ct) != "L3" or fingerprint(old.body) != fingerprint(new_body))]


def diff(old: Policy, new_body: dict, tenant_id: str) -> list[str]:
    new = Policy(old.version + 1, new_body, tenant_id)
    types = load_case_types(tenant_id)
    out = []
    for a, spec in catalog().items():
        if old.enabled(a) != new.enabled(a):
            out.append(f"{spec.title} is switched {'on' if new.enabled(a) else 'off'}")
            continue
        for ct, d in types.items():
            before, after = old.level(a, ct), new.level(a, ct)
            if before != after:
                out.append(f"{spec.title} on {d.get('title', ct)}: {before} to {after}")
    for key in DEFAULT_LIMITS:
        if old.limits[key] != new.limits[key]:
            out.append(f"Limit {key}: {old.limits[key]} to {new.limits[key]}")
    if old.decision != new.decision:
        out.append(f"Escalation threshold: {old.decision['escalate_at']} to {new.decision['escalate_at']}")
    if (old.challenger or None) != (new.challenger or None):
        out.append(f"Challenger: {new.challenger['name']} at {new.challenger['decision']['escalate_at']}" if new.challenger else "Challenger removed")
    return out


def check(s: Session, body: dict) -> dict:
    """What publishing this policy would change, and whether anything stops it."""
    from . import evals

    old = current(s)
    new_body = clean(s, body)
    tenant = tenant_of(s)
    needs = raised_to_act(old, new_body, tenant)
    blockers = []
    if needs:
        verdict = evals.latest(s, fingerprint(new_body))
        for a, ct in needs:
            spec = catalog()[a]
            ev = (verdict or {}).get("sets", {}).get(spec.evaluation)
            if ev is None:
                blockers.append(f"{spec.title} cannot act on its own until these settings are evaluated. Run the evaluation on this draft first.")
            elif not ev["passed"]:
                blockers.append(f"{spec.title} cannot act on its own: the evaluation of these settings did not pass ({ev['why']}).")
    changes = diff(old, new_body, tenant)
    return {"body": new_body, "changes": changes, "blockers": sorted(set(blockers)), "fingerprint": fingerprint(new_body),
            "publishable": bool(changes) and not blockers}


def publish(s: Session, body: dict, by: str, note: str = "") -> Policy:
    result = check(s, body)
    if result["blockers"]:
        raise PolicyError(result["blockers"][0])
    if not result["changes"]:
        raise PolicyError("Nothing would change")
    return _save(s, result["body"], by, note or "; ".join(result["changes"]))


def _save(s: Session, body: dict, by: str, note: str) -> Policy:
    old = current(s)
    row = AgentPolicyVersion(version=old.version + 1, body=body, published_by=by, note=note[:2000])
    s.add(row)
    s.flush()
    return Policy(row.version, body, tenant_of(s))


def switch(s: Session, agent: str, on: bool, by: str, reason: str) -> Policy:
    """The kill switch. Off is immediate and needs no evaluation; on restores the agent at no more than L2."""
    if agent not in catalog():
        raise PolicyError(f"Unknown agent '{agent}'")
    if not reason.strip():
        raise PolicyError("Say why, for the record")
    old = current(s)
    if old.enabled(agent) == on:
        raise PolicyError(f"{catalog()[agent].title} is already switched {'on' if on else 'off'}")
    body = json.loads(json.dumps(old.body))
    entry = body["agents"].setdefault(agent, {"enabled": True, "levels": {}})
    entry["enabled"] = on
    if on:  # coming back never restores the right to act alone; that has to be published and evaluated again
        entry["levels"] = {ct: lv for ct, lv in entry.get("levels", {}).items() if lv != "L3"}
    return _save(s, body, by, f"{catalog()[agent].title} switched {'on' if on else 'off'}: {reason.strip()}")
