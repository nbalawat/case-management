"""Evaluating a table against one set of inputs, by its hit policy."""
from __future__ import annotations

from dataclasses import dataclass

from .model import DecisionError, DecisionTable


class HitPolicyViolation(DecisionError):
    """The inputs matched rules the hit policy does not allow together (UNIQUE or ANY). The table needs fixing."""


@dataclass(frozen=True)
class Result:
    outputs: dict  # output name -> value (a list per output for COLLECT and RULE ORDER without aggregation)
    matched: tuple[str, ...]  # ids of the rules that decided the outputs
    defaulted: bool  # no rule matched and the output defaults were used

    def as_dict(self) -> dict:
        return {"outputs": self.outputs, "matched": list(self.matched), "defaulted": self.defaulted}


def evaluate(t: DecisionTable, inputs: dict) -> Result:
    """The table's answer for these inputs. Inputs it does not know are ignored; inputs it needs but lacks count as null."""
    hits = [r for r in t.rules if r.matches(inputs)]
    if not hits:
        return Result({c.name: c.default for c in t.outputs}, (), True)
    hp = t.hit_policy
    if hp == "UNIQUE":
        if len(hits) > 1:
            raise HitPolicyViolation(f"'{t.title}' is UNIQUE but rules {', '.join(r.id for r in hits)} all match")
        return Result(dict(hits[0].outputs), (hits[0].id,), False)
    if hp == "FIRST":
        return Result(dict(hits[0].outputs), (hits[0].id,), False)
    if hp == "ANY":
        first = hits[0].outputs
        if any(r.outputs != first for r in hits[1:]):
            raise HitPolicyViolation(f"'{t.title}' is ANY but rules {', '.join(r.id for r in hits)} match with different answers")
        return Result(dict(first), tuple(r.id for r in hits), False)
    if hp == "PRIORITY":
        def rank(r):
            return tuple(c.values.index(r.outputs[c.name]) for c in t.outputs)
        best = min(hits, key=rank)
        return Result(dict(best.outputs), (best.id,), False)
    # COLLECT and RULE ORDER: every match
    if t.aggregation:
        out = {}
        for c in t.outputs:
            vals = [r.outputs[c.name] for r in hits if r.outputs[c.name] is not None]
            out[c.name] = {"SUM": lambda v: sum(v), "MIN": lambda v: min(v) if v else None, "MAX": lambda v: max(v) if v else None,
                           "COUNT": lambda v: len(v)}[t.aggregation](vals)
        return Result(out, tuple(r.id for r in hits), False)
    return Result({c.name: [r.outputs[c.name] for r in hits] for c in t.outputs}, tuple(r.id for r in hits), False)

