"""Case types are configuration: versioned YAML packages loaded at start-up."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from ..config import settings


class CaseTypeError(ValueError):
    pass


def _validate(ct: dict, path: Path) -> dict:
    for key in ("case_type", "version", "initial_state", "states", "transitions"):
        if key not in ct:
            raise CaseTypeError(f"{path.name}: missing '{key}'")
    states = ct["states"]
    if ct["initial_state"] not in states:
        raise CaseTypeError(f"{path.name}: initial_state not in states")
    for t in ct["transitions"]:
        if t["from"] not in states or t["to"] not in states:
            raise CaseTypeError(f"{path.name}: transition {t['from']}->{t['to']} references unknown state")
        if not t.get("roles"):
            raise CaseTypeError(f"{path.name}: transition {t['from']}->{t['to']} needs roles")
    return ct


@lru_cache
def load_case_types(directory: str | None = None) -> dict[str, dict]:
    d = Path(directory) if directory else settings.case_types_dir
    out = {}
    for p in sorted(d.glob("*.yaml")):
        ct = _validate(yaml.safe_load(p.read_text()), p)
        ct["source"] = p.read_text()
        out[ct["case_type"]] = ct
    return out


def case_type_for_scenario(scenario: str) -> dict:
    for ct in load_case_types().values():
        if scenario in ct.get("scenarios", []):
            return ct
    return load_case_types()["tm_alert_investigation"]
