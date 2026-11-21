"""Client templates: a starting access policy and set of case types for a kind of client.

Onboarding copies a template into the new client's own configuration. From then on the client's copy is
independent: changing a template never changes a client that already exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import settings


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    summary: str
    case_types: tuple[str, ...]
    access_source: str
    settings: dict = field(default_factory=dict)


@lru_cache
def templates(directory: str | None = None) -> dict[str, Template]:
    d = Path(directory) if directory else settings.templates_dir
    out = {}
    for path in sorted(d.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        access = (settings.access_dir / raw["access"]).read_text()
        out[raw["template"]] = Template(raw["template"], raw["title"], raw.get("summary", ""), tuple(raw["case_types"]),
                                        access, dict(raw.get("settings", {})))
    return out
