"""The part of FEEL (the DMN expression language) that decision table cells use: unary tests and literals. Never eval().

An input cell is a unary test against the column's value:

    -                     anything (an empty cell means the same)
    "high"  or  high      equal to that text
    "high", "critical"    any one of them
    >= 100000             a comparison: <, <=, >, >=, = and != are allowed
    [1..10]  (1..10]      a range; a square bracket includes the end, a round one (or a reversed square one) does not
    true  false  null     those values
    not("low", "medium")  none of them

An output cell is a literal: a quoted or bare text, a number, true, false or null.

A test is parsed once into a `Test`, which both checks a value and describes the values it accepts as `Region`s. The
regions are what the analysis in `analysis.py` reasons about to find overlapping rules and inputs no rule covers.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

NUMBER, TEXT, BOOL = "number", "text", "bool"
KINDS = (NUMBER, TEXT, BOOL)


class FeelError(ValueError):
    """A cell could not be read. The message names the cell's text and is safe to show its author."""


@dataclass(frozen=True)
class Interval:
    low: float = -math.inf
    high: float = math.inf
    low_closed: bool = False
    high_closed: bool = False

    def holds(self, x: float) -> bool:
        above = x > self.low or (self.low_closed and x == self.low)
        below = x < self.high or (self.high_closed and x == self.high)
        return above and below


@dataclass(frozen=True)
class Test:
    """One cell's test. `source` is how it was written, kept for display and export."""

    source: str = field(compare=False)
    any: bool = False
    negate: bool = False
    values: tuple = ()  # literal equality (text, bool, number or None)
    intervals: tuple[Interval, ...] = ()
    excluded: tuple = ()  # != literals

    def holds(self, value) -> bool:
        if self.any:
            return True
        hit = self._positive(value)
        return not hit if self.negate else hit

    def _positive(self, value) -> bool:
        v = _norm(value)
        if any(_equal(v, w) for w in self.values):
            return True
        if self.excluded and v is not None and not any(_equal(v, w) for w in self.excluded):
            return True
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return any(i.holds(float(v)) for i in self.intervals)
        return False

    def boundaries(self) -> set[float]:
        out: set[float] = set()
        for i in self.intervals:
            out |= {b for b in (i.low, i.high) if math.isfinite(b)}
        out |= {float(v) for v in self.values + self.excluded if isinstance(v, (int, float)) and not isinstance(v, bool)}
        return out

    def literals(self) -> set:
        return {v for v in self.values + self.excluded if isinstance(v, str)}


ANY = Test("-", any=True)

_NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_RANGE = re.compile(rf"^([\[\(\]])\s*({_NUM})\s*\.\.\s*({_NUM})\s*([\]\)\[])$")
_CMP = re.compile(r"^(<=|>=|!=|<|>|=)\s*(.+)$")
_BARE = re.compile(r"^[A-Za-z_][\w .\-/]*$")


def _norm(value):
    if isinstance(value, str):
        return value.strip().lower()
    return value


def _equal(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def _split(text: str) -> list[str]:
    """Split on commas that are not inside quotes or brackets."""
    parts, depth, quoted, cur = [], 0, False, []
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in "[(":
            depth += 1
        elif not quoted and ch in "])":
            depth = max(depth - 1, 0)
        if ch == "," and not quoted and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if quoted:
        raise FeelError(f"Unclosed quote in '{text}'")
    parts.append("".join(cur).strip())
    return parts


def literal(text, kind: str | None = None):
    """A literal value from a cell or a YAML value. Bare words are text, so `high` and `"high"` are the same."""
    if text is None or isinstance(text, bool):
        return text
    if isinstance(text, (int, float)):
        return float(text) if kind != TEXT else str(text)
    raw = str(text).strip()
    if raw == "":
        raise FeelError("An empty value")
    if raw.startswith('"'):
        if len(raw) < 2 or not raw.endswith('"') or '"' in raw[1:-1]:
            raise FeelError(f"Cannot read the text {raw}")
        return raw[1:-1].strip().lower()
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    if low == "null":
        return None
    if re.fullmatch(_NUM, raw):
        return float(raw) if kind != TEXT else raw
    if _BARE.match(raw):
        return low
    raise FeelError(f"Cannot read '{raw}'. Write text in quotes, a number, true, false or null")


def parse_test(cell, kind: str = TEXT) -> Test:
    """Parse one input cell. `cell` may be FEEL text or a YAML value (a list means any one of the items)."""
    if cell is None:
        return ANY
    if isinstance(cell, list):
        return _compose([_one(item if not isinstance(item, str) else item, kind) for item in cell], ", ".join(map(_show, cell)))
    if isinstance(cell, (bool, int, float)):
        return _one(cell, kind)
    text = str(cell).strip()
    if text in ("", "-"):
        return ANY
    m = re.fullmatch(r"not\s*\((.*)\)", text, flags=re.S)
    if m:
        inner = parse_test(m.group(1), kind)
        if inner.any:
            raise FeelError("not(-) matches nothing")
        return Test(text, negate=True, values=inner.values, intervals=inner.intervals, excluded=inner.excluded)
    parts = _split(text)
    return _compose([_one(p, kind) for p in parts], text)


def _show(v) -> str:
    return f'"{v}"' if isinstance(v, str) else str(v).lower() if isinstance(v, bool) or v is None else str(v)


def _compose(tests: list[Test], source: str) -> Test:
    if any(t.negate or t.any for t in tests):
        raise FeelError(f"'{source}': not() and - cannot be part of a list")
    return Test(source, values=tuple(v for t in tests for v in t.values), intervals=tuple(i for t in tests for i in t.intervals),
                excluded=tuple(v for t in tests for v in t.excluded))


def _one(item, kind: str) -> Test:
    if not isinstance(item, str):
        return Test(_show(item), values=(literal(item, kind),))
    text = item.strip()
    if not text:
        raise FeelError("An empty entry in a list")
    m = _RANGE.match(text)
    if m:
        if kind != NUMBER:
            raise FeelError(f"'{text}': a range needs a number column")
        lo_b, lo, hi, hi_b = m.groups()
        low, high = float(lo), float(hi)
        if low > high:
            raise FeelError(f"'{text}': the range starts above where it ends")
        return Test(text, intervals=(Interval(low, high, lo_b == "[", hi_b == "]"),))
    m = _CMP.match(text)
    if m:
        op, rest = m.groups()
        value = literal(rest, kind)
        if op in ("=", "!="):
            return Test(text, values=(value,)) if op == "=" else Test(text, excluded=(value,))
        if kind != NUMBER or not isinstance(value, float):
            raise FeelError(f"'{text}': {op} compares numbers")
        interval = {"<": Interval(high=value), "<=": Interval(high=value, high_closed=True),
                    ">": Interval(low=value), ">=": Interval(low=value, low_closed=True)}[op]
        return Test(text, intervals=(interval,))
    value = literal(text, kind)
    _check_kind(value, kind, text)
    return Test(text, values=(value,))


def _check_kind(value, kind: str, text: str) -> None:
    if value is None:
        return
    ok = {NUMBER: isinstance(value, float), BOOL: isinstance(value, bool), TEXT: isinstance(value, str)}[kind]
    if not ok:
        raise FeelError(f"'{text}' is not a {kind} value")


def parse_output(cell, kind: str):
    """Parse one output cell into its value."""
    value = literal(cell, kind)
    _check_kind(value, kind, str(cell))
    return value


def to_feel(value) -> str:
    """A value as FEEL text, for DMN export."""
    return _show(value)
