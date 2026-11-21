"""What the platform does with a table's answer. A table bound to one of these and enabled takes part in live work; any
other table is still callable by id (`decide`, `evaluate`) from workflows, agents and the workflow designer.

  case_priority   when a case opens, its priority is the table's `priority`
  case_queue      when a case opens, it starts in the table's `queue` instead of its first stage's queue
  risk_band       when a customer is scored, their band is the table's `band` instead of the score band

Every application is written to the case's audit trail with the table, version and rule, so a reviewer can see why.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..domain.models import Case, Party
from ..visibility import system_work
from .engine import HitPolicyViolation, evaluate
from .facts import CONTEXTS, case_facts, customer_facts
from .model import DecisionError, DecisionTable
from .store import active

log = logging.getLogger("caseforge.decisions")
PRIORITIES = ("low", "medium", "high", "critical")
BANDS = ("low", "medium", "high")


@dataclass(frozen=True)
class Binding:
    id: str
    label: str
    context: str
    output: str
    allowed: tuple | None  # None: checked against the client's configuration instead (queues)


BINDINGS = {b.id: b for b in (
    Binding("none", "Called by id only", "", "", None),
    Binding("case_priority", "Sets a new case's priority", "case", "priority", PRIORITIES),
    Binding("case_queue", "Sends a new case to a queue", "case", "queue", None),
    Binding("risk_band", "Sets a customer's risk band", "customer", "band", BANDS),
)}


def check(t: DecisionTable, queues: dict[str, str]) -> None:
    """Rules that depend on what a table is for. Raises DecisionError."""
    if t.context not in CONTEXTS:
        raise DecisionError(f"context must be one of {', '.join(CONTEXTS)}")
    vocab = {f.name: f for f in CONTEXTS[t.context]}
    if t.context != "inputs":
        for c in t.inputs:
            if c.name not in vocab:
                raise DecisionError(f"input '{c.name}' is not a {t.context} fact. Known: {', '.join(vocab)}")
            if vocab[c.name].kind != c.kind:
                raise DecisionError(f"input '{c.name}' is a {vocab[c.name].kind}, not a {c.kind}")
    b = BINDINGS.get(t.binding)
    if b is None:
        raise DecisionError(f"binding must be one of {', '.join(BINDINGS)}")
    if b.id == "none":
        return
    if t.context != b.context:
        raise DecisionError(f"a table bound to {b.id} reads {b.context} facts, so its context must be '{b.context}'")
    if b.output not in {c.name for c in t.outputs}:
        raise DecisionError(f"a table bound to {b.id} must have an output named '{b.output}'")
    if t.hit_policy in ("COLLECT", "RULE ORDER"):
        raise DecisionError(f"a table bound to {b.id} must give one answer: use UNIQUE, FIRST, PRIORITY or ANY")
    allowed = b.allowed if b.allowed is not None else tuple(queues)
    col = t.output(b.output)
    used = {r.outputs[b.output] for r in t.rules} | ({col.default} if col.default is not None else set())
    bad = sorted(str(v) for v in used if v not in allowed)
    if bad:
        raise DecisionError(f"'{b.output}' can only be {', '.join(allowed)}; the table uses {', '.join(bad)}")


def bound(s: Session, binding: str, case_type: str | None = None, tables: dict[str, DecisionTable] | None = None) -> list[DecisionTable]:
    out = []
    for t in (tables if tables is not None else active(s)).values():
        if t.enabled and t.binding == binding and (not case_type or not t.case_types or case_type in t.case_types):
            out.append(t)
    return sorted(out, key=lambda t: t.id)


@system_work
def on_cases_opened(s: Session, cases: list[Case]) -> None:
    """Apply the enabled case_priority and case_queue tables to cases that have just opened."""
    if not cases:
        return
    from ..cases.service import definition, record
    from ..domain.personas import policy_of

    in_force = active(s)
    for case in cases:
        tables = [*bound(s, "case_priority", case.case_type, in_force), *bound(s, "case_queue", case.case_type, in_force)]
        if not tables:
            continue
        inputs = case_facts(s, case)
        for t in tables:
            try:
                r = evaluate(t, inputs)
            except HitPolicyViolation as e:  # a broken table must not stop a case opening; it is reported instead
                log.warning("decision %s v%s on %s: %s", t.id, t.version, case.id, e)
                record(s, case.id, "decisions", "system", "decision.failed", {"table": t.id, "version": t.version, "error": str(e)})
                continue
            b = BINDINGS[t.binding]
            value = r.outputs.get(b.output)
            if value is None:
                continue
            before = case.priority if b.id == "case_priority" else case.queue
            if b.id == "case_queue":
                if value not in policy_of(case.tenant_id).queues or case.state != definition(case)["initial_state"]:
                    continue
                case.queue = value
            else:
                case.priority = value
            record(s, case.id, "decisions", "system", "decision.applied",
                   {"table": t.id, "title": t.title, "version": t.version, "rules": list(r.matched), "defaulted": r.defaulted,
                    "output": b.output, "value": value, "before": before})
    s.flush()


def risk_band(s: Session, party: Party, result: dict) -> dict | None:
    """The band an enabled risk_band table gives this customer, with how it got there; None when no table applies."""
    tables = bound(s, "risk_band")
    if not tables:
        return None
    t = tables[0]
    try:
        r = evaluate(t, customer_facts(result, party))
    except HitPolicyViolation as e:
        log.warning("decision %s v%s on %s: %s", t.id, t.version, party.id, e)
        return None
    band = r.outputs.get("band")
    if band is None:
        return None
    return {"band": band, "table": t.id, "title": t.title, "version": t.version, "rules": list(r.matched), "defaulted": r.defaulted}
