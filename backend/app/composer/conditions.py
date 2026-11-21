"""Conditions as the designer builds them, and their translation into the case type's small condition language.

The designer edits a condition group as structured data:

    {"match": "all" | "any", "conditions": [{"fact": "alerted_amount", "op": ">=", "value": 100000}, ...]}

which compiles to a `when` list: `["alerted_amount >= 100000", ...]` for "all", `[{"any": [...]}]` for "any".
A decision's 'Otherwise' branch is the negation of every other branch, which the language can always express:
not(all of a, b) is any of (not a, not b), and not(any of a, b) is all of (not a, not b).
"""
from __future__ import annotations

import re

from ..cases import conditions as language

OPS = ("==", "!=", ">=", "<=", ">", "<")
NEGATE = {"==": "!=", "!=": "==", ">=": "<", "<=": ">", ">": "<=", "<": ">="}
_VALUE = re.compile(r"^[\w.'\"-]+$")


def _value_text(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def term(c: dict, negate: bool = False) -> str:
    op = NEGATE[c["op"]] if negate else c["op"]
    return f"{c['fact']} {op} {_value_text(c['value'])}"


def compile_group(group: dict | None) -> list:
    """A structured group as a `when` list. An empty group means 'always'."""
    if not group or not group.get("conditions"):
        return []
    terms = [term(c) for c in group["conditions"]]
    if group.get("match", "all") == "any" and len(terms) > 1:
        return [{"any": terms}]
    return terms


def negate_group(group: dict) -> list:
    terms = [term(c, negate=True) for c in group.get("conditions") or []]
    if not terms:
        return []
    if group.get("match", "all") == "any" or len(terms) == 1:
        return terms  # none of them holds
    return [{"any": terms}]  # at least one of them fails


def decompile(when) -> dict:
    """A `when` list as a structured group where it has that shape; otherwise kept as raw text the designer shows as is."""
    if not when:
        return {"match": "all", "conditions": []}
    if all(isinstance(w, str) for w in when):
        return {"match": "all", "conditions": [_parse(w) for w in when]}
    if len(when) == 1 and isinstance(when[0], dict):
        return {"match": "any", "conditions": [_parse(w) for w in when[0]["any"]]}
    return {"match": "all", "conditions": [], "raw": when}


def _parse(expr: str) -> dict:
    fact, op, value = language.parse(expr)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return {"fact": fact, "op": op, "value": value}


def condition_problem(c: dict) -> str | None:
    """What is wrong with one structured condition, in plain words, or None."""
    fact = c.get("fact")
    if not fact:
        return "A condition has no fact chosen."
    if fact not in language.FACTS:
        return f"'{fact}' is not a fact this platform knows."
    if c.get("op") not in OPS:
        return f"Choose how to compare {language.FACTS[fact].lower()}."
    kind, values = language.KINDS.get(fact, ("text", ()))
    v = c.get("value")
    if v is None or (isinstance(v, str) and not v.strip()):
        return f"Give a value to compare {language.FACTS[fact].lower()} with."
    if kind == "number":
        try:
            float(v)
        except (TypeError, ValueError):
            return f"'{v}' is not a number."
    elif kind == "bool":
        if _value_text(v).lower() not in ("true", "false"):
            return "This fact is yes or no."
        if c["op"] not in ("==", "!="):
            return "Yes-or-no facts can only be compared with 'is' or 'is not'."
    elif kind == "choice":
        if values and _value_text(v).lower() not in values:
            return f"'{v}' is not one of: {', '.join(values)}."
        if c["op"] not in ("==", "!="):
            return "Choices can only be compared with 'is' or 'is not'."
    if not _VALUE.match(_value_text(v)):
        return f"The value '{v}' can only use letters, digits, dots, dashes and underscores."
    return None


def group_problems(group: dict | None) -> list[str]:
    if not group:
        return []
    return [p for c in group.get("conditions") or [] if (p := condition_problem(c))]


def branch_issues(node_id: str, branch: dict, ctx) -> list[dict]:
    from .blocks import issue

    name = branch.get("label") or "unnamed"
    out = []
    if not branch.get("conditions"):
        out.append(issue("error", "node", node_id, f"The branch '{name}' has no condition, so it would always be taken.",
                         "Add a condition, or delete the branch and use 'Otherwise'."))
    for p in group_problems(branch):
        out.append(issue("error", "node", node_id, f"Branch '{name}': {p}"))
    return out


def facts_used(group: dict | None) -> set[str]:
    return {c.get("fact") for c in (group or {}).get("conditions") or [] if c.get("fact")}
