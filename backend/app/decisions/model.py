"""A decision table: what it reads, what it answers, its rules and how matches combine (the DMN hit policy).

Tables are written in YAML, which reads well in a diff and in Git review:

    id: case_priority
    title: Case priority
    version: "1.1"
    context: case                # which facts the inputs may name (see facts.py)
    binding: case_priority       # what the platform does with the answer, or none
    enabled: true                # a binding takes effect only while the table is enabled
    hit_policy: FIRST
    inputs:
      - {name: max_score, label: Highest alert score, kind: number}
    outputs:
      - {name: priority, kind: text, values: [critical, high, medium, low], default: medium}
    rules:
      - {id: r1, when: {max_score: ">= 0.9"}, then: {priority: critical}, note: Very strong alert}

The same table can be exchanged as DMN 1.3 XML (dmn.py) with Camunda, Kogito, Trisotech and other DMN tools.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from .feel import KINDS, TEXT, FeelError, Test, parse_output, parse_test

# How matches combine. UNIQUE: at most one rule may match. FIRST: the first match in rule order. PRIORITY: the match
# whose output comes first in the output's list of values. ANY: several may match but must agree. COLLECT: every match,
# optionally aggregated. RULE ORDER: every match, in rule order.
HIT_POLICIES = ("UNIQUE", "FIRST", "PRIORITY", "ANY", "COLLECT", "RULE ORDER")
AGGREGATIONS = ("SUM", "MIN", "MAX", "COUNT")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


class DecisionError(ValueError):
    """A table broke a rule. The message is safe to show its author."""


@dataclass(frozen=True)
class Column:
    name: str
    label: str
    kind: str = TEXT
    values: tuple = ()  # allowed values, in priority order for the PRIORITY hit policy
    default: object = None  # outputs only: the answer when no rule matches


@dataclass(frozen=True)
class Rule:
    id: str
    tests: dict[str, Test]
    outputs: dict[str, object]
    note: str = ""

    def matches(self, inputs: dict) -> bool:
        return all(t.holds(inputs.get(name)) for name, t in self.tests.items())


@dataclass(frozen=True)
class DecisionTable:
    id: str
    title: str
    version: str
    hit_policy: str
    inputs: tuple[Column, ...]
    outputs: tuple[Column, ...]
    rules: tuple[Rule, ...]
    description: str = ""
    context: str = "case"
    binding: str = "none"
    enabled: bool = False
    aggregation: str | None = None
    case_types: tuple[str, ...] = ()  # when bound to cases: which case types; empty means all
    source: str = field(default="", compare=False)

    def output(self, name: str) -> Column:
        return next(c for c in self.outputs if c.name == name)

    def describe(self) -> dict:
        """The table in the shape the editor and the API use."""
        return {"id": self.id, "title": self.title, "version": self.version, "description": self.description, "context": self.context,
                "binding": self.binding, "enabled": self.enabled, "hit_policy": self.hit_policy, "aggregation": self.aggregation,
                "case_types": list(self.case_types),
                "inputs": [{"name": c.name, "label": c.label, "kind": c.kind, "values": list(c.values)} for c in self.inputs],
                "outputs": [{"name": c.name, "label": c.label, "kind": c.kind, "values": list(c.values), "default": c.default} for c in self.outputs],
                "rules": [{"id": r.id, "when": {k: t.source for k, t in r.tests.items()}, "then": r.outputs, "note": r.note} for r in self.rules]}


def version_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        raise DecisionError(f"version '{v}' must be numbers separated by dots, like 1.2") from None


def _column(raw, where: str, output: bool) -> Column:
    if not isinstance(raw, dict) or not raw.get("name"):
        raise DecisionError(f"{where} needs a name")
    name = str(raw["name"])
    if not _ID.match(name):
        raise DecisionError(f"{where}: '{name}' must be lower case letters, digits and underscores")
    kind = raw.get("kind", TEXT)
    if kind not in KINDS:
        raise DecisionError(f"{where} '{name}': kind must be one of {', '.join(KINDS)}")
    try:
        values = tuple(parse_output(v, kind) for v in raw.get("values") or [])
        default = parse_output(raw["default"], kind) if output and raw.get("default") is not None else None
    except FeelError as e:
        raise DecisionError(f"{where} '{name}': {e}") from None
    if output and values and default is not None and default not in values:
        raise DecisionError(f"{where} '{name}': the default '{default}' is not one of its values")
    return Column(name, str(raw.get("label") or name.replace("_", " ").capitalize()), kind, values, default)


def from_dict(doc, source: str = "") -> DecisionTable:
    """Read and validate a table. Every structural rule is checked here, so a bad table never reaches a live case."""
    if not isinstance(doc, dict):
        raise DecisionError("A decision table must be a mapping")
    for key in ("id", "title", "version", "inputs", "outputs", "rules"):
        if key not in doc:
            raise DecisionError(f"missing '{key}'")
    tid = str(doc["id"])
    if not _ID.match(tid):
        raise DecisionError(f"id '{tid}' must be lower case letters, digits and underscores")
    version_key(doc["version"])
    hit = str(doc.get("hit_policy", "UNIQUE")).upper()
    if hit not in HIT_POLICIES:
        raise DecisionError(f"hit_policy must be one of {', '.join(HIT_POLICIES)}")
    agg = doc.get("aggregation")
    if agg is not None:
        agg = str(agg).upper()
        if hit != "COLLECT" or agg not in AGGREGATIONS:
            raise DecisionError(f"aggregation is for COLLECT only and must be one of {', '.join(AGGREGATIONS)}")
    if not isinstance(doc["inputs"], list) or not isinstance(doc["outputs"], list) or not doc["outputs"]:
        raise DecisionError("inputs must be a list and outputs a non-empty list")
    inputs = tuple(_column(c, "input", False) for c in doc["inputs"])
    outputs = tuple(_column(c, "output", True) for c in doc["outputs"])
    names = [c.name for c in inputs + outputs]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise DecisionError(f"column names must be unique: {', '.join(dupes)}")
    if hit == "PRIORITY" and any(not c.values for c in outputs):
        raise DecisionError("PRIORITY needs every output to list its values, highest priority first")
    if agg and agg != "COUNT" and any(c.kind != "number" for c in outputs):
        raise DecisionError(f"{agg} adds up numbers, so every output must be a number")
    kinds = {c.name: c.kind for c in inputs}
    rules, seen = [], set()
    if not isinstance(doc["rules"], list):
        raise DecisionError("rules must be a list")
    for n, raw in enumerate(doc["rules"], start=1):
        if not isinstance(raw, dict):
            raise DecisionError(f"rule {n} must be a mapping")
        rid = str(raw.get("id") or f"r{n}")
        if rid in seen:
            raise DecisionError(f"rule id '{rid}' is used twice")
        seen.add(rid)
        when = raw.get("when") or {}
        unknown = [k for k in when if k not in kinds]
        if unknown:
            raise DecisionError(f"rule {rid} tests unknown input(s): {', '.join(unknown)}")
        try:
            tests = {c.name: parse_test(when.get(c.name), c.kind) for c in inputs}
        except FeelError as e:
            raise DecisionError(f"rule {rid}: {e}") from None
        then = raw.get("then") or {}
        missing = [c.name for c in outputs if c.name not in then]
        if missing:
            raise DecisionError(f"rule {rid} gives no value for {', '.join(missing)}")
        out = {}
        for c in outputs:
            try:
                out[c.name] = parse_output(then[c.name], c.kind)
            except FeelError as e:
                raise DecisionError(f"rule {rid}, {c.name}: {e}") from None
            if c.values and out[c.name] not in c.values:
                raise DecisionError(f"rule {rid}: '{out[c.name]}' is not one of the values of {c.name} ({', '.join(map(str, c.values))})")
        rules.append(Rule(rid, tests, out, str(raw.get("note") or "")))
    return DecisionTable(id=tid, title=str(doc["title"]), version=str(doc["version"]), hit_policy=hit, inputs=inputs, outputs=outputs,
                         rules=tuple(rules), description=str(doc.get("description") or ""), context=str(doc.get("context") or "case"),
                         binding=str(doc.get("binding") or "none"), enabled=bool(doc.get("enabled", False)), aggregation=agg,
                         case_types=tuple(doc.get("case_types") or ()), source=source)


def parse(source: str) -> DecisionTable:
    try:
        doc = yaml.safe_load(source)
    except yaml.YAMLError as e:
        raise DecisionError(f"Not valid YAML: {e}") from None
    return from_dict(doc, source)


def to_yaml(t: DecisionTable) -> str:
    """The table as YAML, for a table that arrived another way (DMN import, the grid editor)."""
    d = t.describe()
    doc = {k: d[k] for k in ("id", "title", "version", "description", "context", "binding", "enabled", "hit_policy")}
    if t.aggregation:
        doc["aggregation"] = t.aggregation
    if t.case_types:
        doc["case_types"] = list(t.case_types)
    doc["inputs"] = [{k: v for k, v in c.items() if v not in ([], None)} for c in d["inputs"]]
    doc["outputs"] = [{k: v for k, v in c.items() if v not in ([], None)} for c in d["outputs"]]
    doc["rules"] = [{"id": r["id"], "when": {k: v for k, v in r["when"].items() if v != "-"}, "then": r["then"], **({"note": r["note"]} if r["note"] else {})}
                    for r in d["rules"]]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140)
