"""Changing a table: check a draft, see what it would have done to recent cases, publish it as a new version."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Case, DecisionVersion
from ..domain.personas import Persona, policy_of
from ..tenancy import tenant_of
from ..visibility import system_work
from . import bindings, dmn
from .analysis import analyse
from .engine import HitPolicyViolation, evaluate
from .facts import case_facts
from .model import DecisionError, DecisionTable, parse, to_yaml, version_key
from .store import active, get

SIMULATE_CASES = 500


def read(source: str | None = None, dmn_xml: str | None = None) -> DecisionTable:
    """A draft from YAML or from DMN XML."""
    if dmn_xml:
        return dmn.parse(dmn_xml)
    if not source:
        raise DecisionError("Give the table as YAML or DMN")
    return parse(source)


def diff(old: DecisionTable | None, new: DecisionTable) -> list[str]:
    if old is None:
        return [f"New decision table '{new.title}'"]
    out = []
    if old.hit_policy != new.hit_policy:
        out.append(f"Hit policy {old.hit_policy} becomes {new.hit_policy}")
    if old.enabled != new.enabled:
        out.append("Puts the table in use" if new.enabled else "Takes the table out of use")
    if old.binding != new.binding:
        out.append(f"Binding {old.binding} becomes {new.binding}")
    for kind, a, b in (("input", old.inputs, new.inputs), ("output", old.outputs, new.outputs)):
        an, bn = {c.name: c for c in a}, {c.name: c for c in b}
        out += [f"Adds {kind} '{n}'" for n in bn if n not in an]
        out += [f"Removes {kind} '{n}'" for n in an if n not in bn]
        out += [f"Changes {kind} '{n}'" for n in bn if n in an and an[n] != bn[n]]
    olds, news = {r.id: r for r in old.rules}, {r.id: r for r in new.rules}
    out += [f"Adds rule {i}" for i in news if i not in olds]
    out += [f"Removes rule {i}" for i in olds if i not in news]
    out += [f"Changes rule {i}" for i in news if i in olds and (olds[i].tests != news[i].tests or olds[i].outputs != news[i].outputs)]
    if [r.id for r in old.rules if r.id in news] != [r.id for r in new.rules if r.id in olds]:
        out.append("Reorders rules")
    return out or ["No change to the rules"]


@system_work
def simulate(s: Session, t: DecisionTable, limit: int = SIMULATE_CASES) -> dict | None:
    """Replay a case table against the client's most recent cases: how often each rule decides, and for a bound table
    how many cases would have come out differently from what they actually got."""
    if t.context != "case":
        return None
    cases = list(s.scalars(select(Case).order_by(Case.created.desc()).limit(limit)))
    if t.case_types:
        cases = [c for c in cases if c.case_type in t.case_types]
    rules, outcomes, changed, errors = Counter(), Counter(), [], 0
    b = bindings.BINDINGS.get(t.binding)
    for c in cases:
        try:
            r = evaluate(t, case_facts(s, c))
        except HitPolicyViolation:
            errors += 1
            continue
        for rid in r.matched or ("(default)",):
            rules[rid] += 1
        if b and b.output:
            value = r.outputs.get(b.output)
            outcomes[str(value)] += 1
            actual = c.priority if b.id == "case_priority" else c.queue
            if value is not None and value != actual and len(changed) < 2000:
                changed.append({"case_id": c.id, "case_type": c.case_type, "actual": actual, "would_be": value, "rules": list(r.matched)})
        else:
            outcomes[str(next(iter(r.outputs.values()), None))] += 1
    return {"cases": len(cases), "by_rule": dict(rules), "outcomes": dict(outcomes), "errors": errors,
            "changed": len(changed), "examples": changed[:25], "compared_with": b.output if b and b.output else None}


@system_work
def check(s: Session, t: DecisionTable) -> dict:
    """Everything a reviewer needs before publishing: validity, analysis, what changes, and a replay on recent cases."""
    bindings.check(t, policy_of(tenant_of(s)).queues)
    current = active(s).get(t.id)
    if current is not None and version_key(t.version) <= version_key(current.version):
        raise DecisionError(f"version must be higher than the active {current.version}")
    report = analyse(t)
    return {"table": t.describe(), "yaml": t.source or to_yaml(t), "is_new": current is None, "changes": diff(current, t),
            "analysis": report.as_dict(), "simulation": simulate(s, t), "publishable": not report.errors}


@system_work
def publish(s: Session, t: DecisionTable, persona: Persona, note: str = "") -> dict:
    if not persona.can("publish_workflow"):
        raise DecisionError("Publishing a decision table needs the 'publish workflow' permission")
    result = check(s, t)
    if not result["publishable"]:
        raise DecisionError("The table has errors: " + "; ".join(result["analysis"]["errors"]))
    s.add(DecisionVersion(table_id=t.id, version=t.version, source=result["yaml"], published_by=persona.id, note=note))
    s.flush()
    return result


@system_work
def export_dmn(s: Session, table_id: str, version: str | None = None) -> str:
    return dmn.export(get(s, table_id, version))
