"""Decision tables (DMN): routing, priority, risk and any other business decision written as a table of rules rather
than as code or nested conditions.

  feel       the cell language: unary tests and literals, parsed safely
  model      a table, read from YAML and validated
  engine     evaluation by hit policy
  analysis   overlaps, rules that never decide, inputs no rule covers
  dmn        DMN 1.3 XML import and export
  store      the shipped library and each client's published versions
  facts      what inputs may name, per context, and how they are read
  bindings   what the platform does with an answer (case priority, queue, customer risk band)
  service    check, simulate, publish

The interface other components build on:

    list_tables(s)                 every table in force at this client
    decide(s, table_id, case)      the table's answer for a case: outputs, plus "_rule" (rule ids) and "_version"
    evaluate(s, table_id, inputs)  the same for inputs the caller supplies
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.models import Case
from ..visibility import system_work
from .engine import HitPolicyViolation, Result, evaluate as _evaluate
from .facts import READERS
from .model import DecisionError, DecisionTable
from .store import active, get

__all__ = ["DecisionError", "DecisionTable", "HitPolicyViolation", "Result", "decide", "evaluate", "list_tables"]


@system_work
def list_tables(s: Session) -> list[dict]:
    return [{"id": t.id, "title": t.title, "version": t.version, "context": t.context, "binding": t.binding, "enabled": t.enabled,
             "outputs": [{"name": c.name, "kind": c.kind, "values": list(c.values)} for c in t.outputs]}
            for t in sorted(active(s).values(), key=lambda t: t.title)]


def _answer(t: DecisionTable, r: Result) -> dict:
    return dict(r.outputs) | {"_rule": list(r.matched), "_version": t.version}


@system_work
def evaluate(s: Session, table_id: str, inputs: dict) -> dict:
    t = get(s, table_id)
    return _answer(t, _evaluate(t, inputs))


@system_work
def decide(s: Session, table_id: str, case: Case) -> dict:
    t = get(s, table_id)
    reader = READERS.get(t.context)
    if reader is None:
        raise DecisionError(f"'{t.title}' reads {t.context} facts, not a case: call evaluate() with its inputs")
    return _answer(t, _evaluate(t, reader(s, case)))
