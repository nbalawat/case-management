"""Feature sets: named, versioned groups of features about one kind of entity, defined in YAML.

A feature is an aggregate over a source in a window ending at a moment (`as_of`), or an attribute of the entity.
The spec is deliberately small so that it compiles to portable SQL on any database the platform runs on, and so a
reviewer can read exactly what a model was given.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import yaml

SOURCES = {"transactions", "alerts", "cases", "party"}
AGGS = {"count", "sum", "max", "count_distinct"}
TXN_FIELDS = {"amount", "counterparty", "channel", "counterparty_country"}
TXN_WHERE = {"direction", "channel", "counterparty_country", "amount_from", "amount_below"}
PARTY_FIELDS = {"risk_rating", "pep", "onboarded"}


class FeatureError(ValueError):
    pass


@dataclass(frozen=True)
class Feature:
    name: str
    label: str
    source: str
    agg: str | None = None
    field: str | None = None
    window_days: int | None = None
    where: dict = dc_field(default_factory=dict, hash=False, compare=False)


@dataclass(frozen=True)
class FeatureSet:
    id: str
    title: str
    entity: str
    version: str
    description: str
    features: tuple[Feature, ...]

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.features]

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "entity": self.entity, "version": self.version, "description": self.description,
                "features": [{"name": f.name, "label": f.label, "source": f.source, "agg": f.agg, "field": f.field,
                              "window_days": f.window_days, "where": f.where} for f in self.features]}


KEYS = {"name", "label", "source", "agg", "field", "window_days", "where"}


def _feature(raw: dict) -> Feature:
    name = str(raw.get("name") or "")
    if not name.isidentifier() or name != name.lower():
        raise FeatureError(f"Feature names are lower_snake_case: '{name}'")
    if unknown := set(raw) - KEYS:
        # Usually an unquoted comma in a one-line YAML mapping: "label: 8,000 and 10,000" splits into stray keys.
        raise FeatureError(f"{name}: unknown key(s) {', '.join(sorted(map(str, unknown)))}. Quote a label that has a comma in it")
    src = raw.get("source")
    if src not in SOURCES:
        raise FeatureError(f"{name}: source must be one of {', '.join(sorted(SOURCES))}")
    where = dict(raw.get("where") or {})
    f = Feature(name, str(raw.get("label") or name), src, raw.get("agg"), raw.get("field"), raw.get("window_days"), where)
    if src == "party":
        if f.field not in PARTY_FIELDS:
            raise FeatureError(f"{name}: a party feature reads one of {', '.join(sorted(PARTY_FIELDS))}")
        return f
    if f.agg not in AGGS:
        raise FeatureError(f"{name}: agg must be one of {', '.join(sorted(AGGS))}")
    if f.agg in ("sum", "max", "count_distinct") and not f.field:
        raise FeatureError(f"{name}: {f.agg} needs a field")
    if src == "transactions":
        if f.field and f.field not in TXN_FIELDS:
            raise FeatureError(f"{name}: transactions have no field '{f.field}'")
        if unknown := set(where) - TXN_WHERE:
            raise FeatureError(f"{name}: unknown condition(s) {', '.join(sorted(unknown))}")
    if src == "cases" and set(where) - {"outcome"}:
        raise FeatureError(f"{name}: cases can be filtered by outcome only")
    if src != "cases" and not (isinstance(f.window_days, int) and 0 < f.window_days <= 730):
        raise FeatureError(f"{name}: window_days must be between 1 and 730")
    return f


def from_dict(doc: dict) -> FeatureSet:
    if not isinstance(doc, dict) or not doc.get("id") or not doc.get("features"):
        raise FeatureError("A feature set needs an id and features")
    if doc.get("entity") != "party":
        raise FeatureError("Feature sets are about customers (entity: party)")
    feats = tuple(_feature(f) for f in doc["features"])
    names = [f.name for f in feats]
    if len(set(names)) != len(names):
        raise FeatureError("A feature name is repeated")
    return FeatureSet(doc["id"], str(doc.get("title") or doc["id"]), "party", str(doc.get("version") or "1.0"),
                      str(doc.get("description") or ""), feats)


def parse(source: str) -> FeatureSet:
    return from_dict(yaml.safe_load(source))
