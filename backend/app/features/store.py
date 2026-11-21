"""The feature sets in force: the shipped library. Each set is versioned; a model records the version it used."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .model import FeatureError, FeatureSet, parse

HERE = Path(__file__).parent / "library"


@lru_cache(maxsize=1)
def library() -> dict[str, FeatureSet]:
    sets = {}
    for f in sorted(HERE.glob("*.yaml")):
        fs = parse(f.read_text())
        sets[fs.id] = fs
    return sets


def get(set_id: str) -> FeatureSet:
    try:
        return library()[set_id]
    except KeyError:
        raise FeatureError(f"No feature set '{set_id}'") from None
