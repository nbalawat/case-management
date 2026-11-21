"""A form: sections of typed fields, written in YAML, compiled to JSON Schema (2020-12) for validation and exchange.

    id: sar_decision
    title: SAR decision record
    version: "1.0"
    case_types: [tm_alert_investigation]   # which case types offer it; empty means none (called by id only)
    stages: [sar_drafting, mlro_review]   # in which stages it can be filled; empty means any open stage
    sections:
      - title: Decision
        fields:
          - {key: decision, label: Decision, type: choice, options: [file, no_file], required: true}
          - {key: reason, label: Why not, type: long_text, required: true, min_length: 40, visible_when: "decision == no_file"}
          - {key: amount, label: Amount involved, type: money, prefill: alerted_amount}

A field that is not visible is not asked for, not required, and not kept. `prefill` names a case fact (see
decisions/facts.py) the form starts with. The compiled schema states the same rules with if/then, so another JSON
Schema form renderer (react-jsonschema-form, JSON Forms) enforces exactly what the platform does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

TYPES = {
    "text": "One line of text",
    "long_text": "A paragraph",
    "number": "A number",
    "money": "An amount of money",
    "date": "A date",
    "email": "An email address",
    "boolean": "Yes or no",
    "choice": "One of a list",
    "multi_choice": "Any of a list",
}
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_WHEN = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*(==|!=)\s*(.+?)\s*$")


class FormError(ValueError):
    """A form definition broke a rule. The message is safe to show its author."""


@dataclass(frozen=True)
class Condition:
    key: str
    op: str
    value: object

    def holds(self, data: dict) -> bool:
        have = data.get(self.key)
        same = have == self.value or (isinstance(have, list) and self.value in have)
        return same if self.op == "==" else not same

    def describe(self) -> str:
        v = self.value
        return f"{self.key} {self.op} {str(v).lower() if isinstance(v, bool) else v}"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str
    required: bool = False
    help: str = ""
    options: tuple[str, ...] = ()
    option_labels: dict = field(default_factory=dict, compare=False)
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    visible_when: Condition | None = None
    prefill: str | None = None

    def visible(self, data: dict) -> bool:
        return self.visible_when is None or self.visible_when.holds(data)


@dataclass(frozen=True)
class Section:
    title: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Form:
    id: str
    title: str
    version: str
    sections: tuple[Section, ...]
    description: str = ""
    case_types: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    source: str = field(default="", compare=False)

    @property
    def fields(self) -> list[Field]:
        return [f for s in self.sections for f in s.fields]

    def field(self, key: str) -> Field:
        return next(f for f in self.fields if f.key == key)

    def describe(self) -> dict:
        return {"id": self.id, "title": self.title, "version": self.version, "description": self.description,
                "case_types": list(self.case_types), "stages": list(self.stages),
                "sections": [{"title": s.title, "fields": [_field_dict(f) for f in s.fields]} for s in self.sections]}


def _field_dict(f: Field) -> dict:
    d = {"key": f.key, "label": f.label, "type": f.type, "required": f.required, "help": f.help, "options": list(f.options),
         "option_labels": dict(f.option_labels), "minimum": f.minimum, "maximum": f.maximum, "min_length": f.min_length,
         "max_length": f.max_length, "pattern": f.pattern, "visible_when": f.visible_when.describe() if f.visible_when else None, "prefill": f.prefill}
    return d


def version_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        raise FormError(f"version '{v}' must be numbers separated by dots, like 1.2") from None


def _value(raw: str):
    t = raw.strip().strip("'\"")
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    try:
        return float(t) if "." in t else int(t)
    except ValueError:
        return t


def _condition(raw, keys_before: dict[str, Field], where: str) -> Condition | None:
    if raw in (None, ""):
        return None
    m = _WHEN.match(str(raw))
    if not m:
        raise FormError(f"{where}: visible_when must read '<field> == <value>' or '<field> != <value>'")
    key, op, value = m.group(1), m.group(2), _value(m.group(3))
    if key not in keys_before:
        raise FormError(f"{where}: visible_when names '{key}', which is not a field above it")
    target = keys_before[key]
    if target.type in ("choice", "multi_choice") and value not in target.options:
        raise FormError(f"{where}: '{value}' is not an option of {key}")
    if target.type == "boolean" and not isinstance(value, bool):
        raise FormError(f"{where}: {key} is yes or no, so compare it with true or false")
    return Condition(key, op, value)


def _num(raw, name: str, where: str) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise FormError(f"{where}: {name} must be a number") from None


def _field(raw, seen: dict[str, Field], where: str) -> Field:
    if not isinstance(raw, dict) or not raw.get("key"):
        raise FormError(f"{where}: every field needs a key")
    key = str(raw["key"])
    where = f"field '{key}'"
    if not _KEY.match(key):
        raise FormError(f"{where}: keys are lower case letters, digits and underscores")
    if key in seen:
        raise FormError(f"{where}: the key is used twice")
    ftype = raw.get("type", "text")
    if ftype not in TYPES:
        raise FormError(f"{where}: type must be one of {', '.join(TYPES)}")
    options, labels = (), {}
    if ftype in ("choice", "multi_choice"):
        raw_opts = raw.get("options") or []
        for o in raw_opts:
            if isinstance(o, dict):
                labels[str(o["value"])] = str(o.get("label") or o["value"])
        options = tuple(str(o["value"]) if isinstance(o, dict) else str(o) for o in raw_opts)
        if len(options) < 2 or len(set(options)) != len(options):
            raise FormError(f"{where}: a choice needs at least two different options")
    elif raw.get("options"):
        raise FormError(f"{where}: only choice fields have options")
    lo, hi = _num(raw.get("minimum"), "minimum", where), _num(raw.get("maximum"), "maximum", where)
    if lo is not None and hi is not None and lo > hi:
        raise FormError(f"{where}: minimum is above maximum")
    if (lo is not None or hi is not None) and ftype not in ("number", "money"):
        raise FormError(f"{where}: minimum and maximum are for numbers")
    mn, mx = raw.get("min_length"), raw.get("max_length")
    if (mn is not None or mx is not None) and ftype not in ("text", "long_text"):
        raise FormError(f"{where}: lengths are for text")
    pattern = raw.get("pattern")
    if pattern is not None:
        try:
            re.compile(pattern)
        except re.error as e:
            raise FormError(f"{where}: pattern is not a valid regular expression ({e})") from None
    prefill = raw.get("prefill")
    if prefill is not None:
        from ..decisions.facts import CASE_FACTS  # noqa: PLC0415

        if prefill not in {f.name for f in CASE_FACTS}:
            raise FormError(f"{where}: prefill '{prefill}' is not a case fact")
    return Field(key=key, label=str(raw.get("label") or key.replace("_", " ").capitalize()), type=ftype, required=bool(raw.get("required")),
                 help=str(raw.get("help") or ""), options=options, option_labels=labels, minimum=lo, maximum=hi,
                 min_length=int(mn) if mn is not None else None, max_length=int(mx) if mx is not None else None, pattern=pattern,
                 visible_when=_condition(raw.get("visible_when"), seen, where), prefill=prefill)


def from_dict(doc, source: str = "") -> Form:
    if not isinstance(doc, dict):
        raise FormError("A form must be a mapping")
    for key in ("id", "title", "version", "sections"):
        if key not in doc:
            raise FormError(f"missing '{key}'")
    fid = str(doc["id"])
    if not _KEY.match(fid):
        raise FormError(f"id '{fid}' must be lower case letters, digits and underscores")
    version_key(doc["version"])
    if not isinstance(doc["sections"], list) or not doc["sections"]:
        raise FormError("a form needs at least one section")
    seen: dict[str, Field] = {}
    sections = []
    for n, raw in enumerate(doc["sections"], start=1):
        if not isinstance(raw, dict) or not isinstance(raw.get("fields"), list) or not raw["fields"]:
            raise FormError(f"section {n} needs fields")
        fields = []
        for f in raw["fields"]:
            parsed = _field(f, seen, f"section {n}")
            seen[parsed.key] = parsed
            fields.append(parsed)
        sections.append(Section(str(raw.get("title") or f"Section {n}"), tuple(fields)))
    return Form(id=fid, title=str(doc["title"]), version=str(doc["version"]), sections=tuple(sections), description=str(doc.get("description") or ""),
                case_types=tuple(doc.get("case_types") or ()), stages=tuple(doc.get("stages") or ()), source=source)


def parse(source: str) -> Form:
    try:
        doc = yaml.safe_load(source)
    except yaml.YAMLError as e:
        raise FormError(f"Not valid YAML: {e}") from None
    return from_dict(doc, source)


def to_yaml(form: Form) -> str:
    d = form.describe()
    doc = {k: d[k] for k in ("id", "title", "version", "description", "case_types", "stages")}
    doc["sections"] = []
    for s in d["sections"]:
        fields = []
        for f in s["fields"]:
            labels = f.pop("option_labels")
            if labels:
                f["options"] = [{"value": o, "label": labels.get(o, o)} for o in f["options"]]
            fields.append({k: v for k, v in f.items() if k == "key" or not (v is None or v is False or v == "" or v == [])})
        doc["sections"].append({"title": s["title"], "fields": fields})
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140)
