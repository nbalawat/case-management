"""Decision-table answers as facts the workflow designer's conditions can test: `dt_<table>_<output>`.

For each table that decides about a case, every output becomes a fact, so a step can say
`dt_case_priority_priority == critical` or `dt_red_flag_points_points >= 40`. The value is the table in force at the
case's client, evaluated on the case when the condition is checked.

A table's inputs are themselves case facts, so while a table is being evaluated these facts read as unknown: a table
never decides on another table's answer, and evaluation cannot loop.
"""
from __future__ import annotations

import contextvars
import logging

from sqlalchemy.orm import Session

from ..cases import conditions
from ..domain.models import Case
from .model import DecisionError, DecisionTable

log = logging.getLogger("caseforge.decisions")
_deciding = contextvars.ContextVar("deciding", default=False)


def fact_name(table_id: str, output: str) -> str:
    return f"dt_{table_id}_{output}"


def _kind(kind: str, values: tuple) -> tuple[str, tuple]:
    if kind == "number":
        return "number", ()
    if kind == "bool":
        return "bool", ()
    return ("choice", tuple(str(v) for v in values)) if values else ("text", ())


def _reader(table_id: str, output: str):
    def read(s: Session, case: Case):
        if _deciding.get():
            return None
        from . import decide

        token = _deciding.set(True)
        try:
            return decide(s, table_id, case).get(output)
        except DecisionError as e:  # not in force at this client, disabled, or no rule decided: unknown, never a match
            log.debug("dt fact %s.%s unknown for %s: %s", table_id, output, case.id, e)
            return None
        finally:
            _deciding.reset(token)
    return read


def register(t: DecisionTable) -> list[str]:
    """Offer a case table's outputs as facts. Returns their names; a table about something else offers none."""
    if t.context != "case":
        return []
    names = []
    for c in t.outputs:
        kind, values = _kind(c.kind, c.values)
        name = fact_name(t.id, c.name)
        conditions.register_fact(name, f"{t.title}: {c.label}", _reader(t.id, c.name), kind, values)
        names.append(name)
    return names


def register_library() -> list[str]:
    from .store import library

    return [n for t in library().values() for n in register(t)]
