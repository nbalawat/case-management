"""Guard conditions for transitions: a small, safe expression form, never eval().

A condition is `<fact> <op> <value>`, for example `alerted_amount >= 100000` or `pep == true`.
A transition's `when` is a list; every condition must hold. `any:` gives an either/or group.
"""
from __future__ import annotations

import operator
import re

OPS = {"==": operator.eq, "!=": operator.ne, ">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
_PATTERN = re.compile(r"^\s*([a-z_]+)\s*(==|!=|>=|<=|>|<)\s*([\w.'\"-]+)\s*$")
FACTS = {
    "alerted_amount": "Total value of alerted transactions",
    "alerts": "Number of alerts on the case",
    "risk_rating": "Customer risk rating: low, medium or high",
    "pep": "Customer is a politically exposed person",
    "priority": "Case priority: low, medium, high or critical",
    "agent_recommendation": "Agent recommendation: escalate or close",
}


class ConditionError(ValueError):
    pass


def _value(raw: str):
    low = raw.strip().strip("'\"").lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return float(low)
    except ValueError:
        return low


def parse(expr: str) -> tuple[str, str, object]:
    m = _PATTERN.match(expr)
    if not m:
        raise ConditionError(f"Cannot read condition '{expr}'. Use: <fact> <op> <value>")
    fact, op, raw = m.groups()
    if fact not in FACTS:
        raise ConditionError(f"Unknown fact '{fact}'. Known facts: {', '.join(FACTS)}")
    return fact, op, _value(raw)


def check(when) -> None:
    """Validate a `when` block at publish time, so a bad rule never reaches a live case."""
    for item in when or []:
        if isinstance(item, dict):
            if set(item) != {"any"} or not isinstance(item["any"], list):
                raise ConditionError("A condition group must be {any: [..]}")
            for sub in item["any"]:
                parse(sub)
        else:
            parse(item)


def _holds(expr: str, facts: dict) -> bool:
    fact, op, want = parse(expr)
    have = facts.get(fact)
    if isinstance(have, str):
        have = have.lower()
    if have is None:
        return False
    try:
        return bool(OPS[op](have, want))
    except TypeError:
        return False


def evaluate(when, facts: dict) -> bool:
    for item in when or []:
        if isinstance(item, dict):
            if not any(_holds(sub, facts) for sub in item["any"]):
                return False
        elif not _holds(item, facts):
            return False
    return True


def describe(when) -> str:
    parts = []
    for item in when or []:
        parts.append("(" + " or ".join(item["any"]) + ")" if isinstance(item, dict) else item)
    return " and ".join(parts)
