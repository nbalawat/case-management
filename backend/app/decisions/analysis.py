"""Checking a table before it goes live: rules that overlap where the hit policy forbids it, rules that can never
decide anything, and inputs no rule covers.

Every cell is a union of intervals (numbers) or a set of literals (text, true/false), so the input space splits into
finitely many regions inside which every cell gives the same answer. One representative point per region is enough
to decide every question exactly: each boundary, a point between each pair of boundaries and one beyond each end for
numbers; each value named anywhere plus "anything else" for text. The product of the representatives is walked once.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from .feel import BOOL, NUMBER
from .model import DecisionTable

OTHER = "␀other"  # a text value no cell names
LIMIT = 200_000  # combinations walked at most; above it the analysis says it did not run


@dataclass
class Report:
    checked: bool = True
    combinations: int = 0
    overlaps: list[dict] = field(default_factory=list)  # forbidden by UNIQUE, or ANY with different answers
    never_match: list[str] = field(default_factory=list)  # rules no input can satisfy
    never_decide: list[str] = field(default_factory=list)  # FIRST/PRIORITY: always beaten by another rule
    gaps: list[dict] = field(default_factory=list)  # examples of inputs no rule matches
    gap_count: int = 0
    note: str = ""
    undefaulted: bool = True  # some output has no default, so an uncovered input gets no answer at all

    @property
    def errors(self) -> list[str]:
        out = [f"Rules {' and '.join(o['rules'])} both match, for example when {_say(o['example'])}" for o in self.overlaps]
        out += [f"Rule {r} can never match: its cells contradict each other" for r in self.never_match]
        return out

    @property
    def warnings(self) -> list[str]:
        out = [f"Rule {r} never decides anything: an earlier or higher-priority rule always wins" for r in self.never_decide]
        if self.gap_count and self.undefaulted:
            out.append(f"{self.gap_count} combination(s) of inputs match no rule and get the defaults, for example when {_say(self.gaps[0])}")
        return out

    def as_dict(self) -> dict:
        return {"checked": self.checked, "combinations": self.combinations, "errors": self.errors, "warnings": self.warnings,
                "overlaps": self.overlaps, "never_match": self.never_match, "never_decide": self.never_decide,
                "gaps": self.gaps, "gap_count": self.gap_count, "note": self.note}


def _say(example: dict) -> str:
    parts = []
    for k, v in example.items():
        parts.append(f"{k} is anything else" if v == OTHER else f"{k} = {v}")
    return ", ".join(parts) or "any input"


def _points(t: DecisionTable, col) -> list:
    tests = [r.tests[col.name] for r in t.rules]
    if col.kind == BOOL:
        return [True, False]
    if col.kind == NUMBER:
        bounds = sorted(set().union(*(x.boundaries() for x in tests)) | {float(v) for v in col.values if isinstance(v, float)})
        if not bounds:
            return [0.0]
        pts = [bounds[0] - 1]
        for a, b in zip(bounds, bounds[1:]):
            pts += [a, (a + b) / 2]
        pts += [bounds[-1], bounds[-1] + 1]
        return [round(p, 6) if math.isfinite(p) else p for p in pts]
    named = set(col.values) | set().union(*(x.literals() for x in tests))
    return sorted(v for v in named if isinstance(v, str)) + ([] if col.values else [OTHER])


def analyse(t: DecisionTable) -> Report:
    axes = [_points(t, c) for c in t.inputs]
    total = math.prod(len(a) for a in axes) if axes else 1
    rep = Report(combinations=total, undefaulted=any(c.default is None for c in t.outputs))
    if total > LIMIT:
        rep.checked, rep.note = False, f"Too many input combinations to check exhaustively ({total:,}); the table was validated but not analysed."
        return rep
    names = [c.name for c in t.inputs]
    matched_ever: set[str] = set()
    decided_ever: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for combo in itertools.product(*axes) if axes else [()]:
        inputs = dict(zip(names, combo))
        hits = [r for r in t.rules if r.matches(inputs)]
        if not hits:
            rep.gap_count += 1
            if len(rep.gaps) < 5:
                rep.gaps.append(inputs)
            continue
        matched_ever |= {r.id for r in hits}
        if t.hit_policy == "FIRST":
            decided_ever.add(hits[0].id)
        elif t.hit_policy == "PRIORITY":
            decided_ever.add(min(hits, key=lambda r: tuple(c.values.index(r.outputs[c.name]) for c in t.outputs)).id)
        else:
            decided_ever |= {r.id for r in hits}
        if len(hits) > 1 and t.hit_policy in ("UNIQUE", "ANY"):
            for a, b in itertools.combinations(hits, 2):
                if t.hit_policy == "ANY" and a.outputs == b.outputs:
                    continue
                if (a.id, b.id) not in seen_pairs:
                    seen_pairs.add((a.id, b.id))
                    rep.overlaps.append({"rules": [a.id, b.id], "example": inputs})
    rep.never_match = [r.id for r in t.rules if r.id not in matched_ever]
    rep.never_decide = [r.id for r in t.rules if r.id in matched_ever and r.id not in decided_ever]
    return rep
