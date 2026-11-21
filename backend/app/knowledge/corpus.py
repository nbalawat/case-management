"""Reading the library from disk into passages."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import ROOT

BASE = Path(os.getenv("KNOWLEDGE_DIR") or ROOT / "backend" / "knowledge")
ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Passage:
    id: str  # kb:<document>#<section>
    document: str
    title: str  # the document's title
    section: str
    kind: str  # typology, policy, procedure, regulation
    text: str
    source: str
    scenarios: tuple[str, ...] = field(default=())
    client: str | None = None  # None: the platform library

    def cite(self) -> dict:
        return {"id": self.id, "document": self.document, "title": self.title, "section": self.section, "kind": self.kind,
                "source": self.source, "client_document": self.client is not None}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse(name: str, raw: str, client: str | None = None) -> list[Passage]:
    """One document's passages. The header is YAML between --- lines; each `##` section is a passage."""
    if not ID.match(name):
        raise ValueError(f"Document name '{name}' must be lower-case letters, digits and hyphens")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{name}: a document starts with a --- header")
    head, body = yaml.safe_load(m.group(1)) or {}, m.group(2)
    for key in ("title", "kind"):
        if not head.get(key):
            raise ValueError(f"{name}: the header needs '{key}'")
    out = []
    for sec in re.split(r"^## ", body, flags=re.M)[1:]:
        heading, _, text = sec.partition("\n")
        heading, text = heading.strip(), text.strip()
        if text:
            out.append(Passage(f"kb:{name}#{_slug(heading)}", name, head["title"], heading, head["kind"], text,
                               head.get("source", ""), tuple(head.get("scenarios") or ()), client))
    if not out:
        raise ValueError(f"{name}: no '## ' sections")
    return out


@lru_cache(maxsize=32)
def _load(base: str, client: str | None) -> tuple[Passage, ...]:
    root = Path(base)
    docs: dict[str, list[Passage]] = {}
    for f in sorted((root / "library").glob("*.md")):
        docs[f.stem] = parse(f.stem, f.read_text())
    if client:
        for f in sorted((root / "clients" / client).glob("*.md")):
            docs[f.stem] = parse(f.stem, f.read_text(), client)  # the client's own version replaces the library's
    return tuple(p for ps in docs.values() for p in ps)


def documents(client: str | None) -> tuple[Passage, ...]:
    """Every passage one client's people may read: the platform library, with the client's own documents over it."""
    return _load(str(BASE), client)


def passage(client: str | None, pid: str) -> Passage | None:
    return next((p for p in documents(client) if p.id == pid), None)


def link(pid: str) -> str:
    """Where in the app a person reads a passage."""
    doc, _, section = pid.removeprefix("kb:").partition("#")
    return f"/knowledge/{doc}" + (f"#{section}" if section else "")


def reload() -> None:
    _load.cache_clear()
