"""Where tables come from: the shipped library (YAML files every client starts from) and each client's published
versions, stored per client and never seen by another. The highest version of a table is the one in force.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ROOT
from ..domain.models import DecisionVersion
from ..visibility import system_work
from .model import DecisionError, DecisionTable, parse, version_key

LIBRARY_DIR = ROOT / "backend" / "decisions"


@lru_cache
def library(directory: str | None = None) -> dict[str, DecisionTable]:
    out = {}
    for f in sorted(Path(directory or LIBRARY_DIR).glob("*.yaml")):
        try:
            t = parse(f.read_text())
        except DecisionError as e:
            raise DecisionError(f"{f.name}: {e}") from None
        out[t.id] = t
    return out


@lru_cache(maxsize=512)
def _parsed(source: str) -> DecisionTable:
    return parse(source)


@system_work
def published(s: Session, table_id: str | None = None) -> list[DecisionVersion]:
    q = select(DecisionVersion).order_by(DecisionVersion.id)
    if table_id:
        q = q.where(DecisionVersion.table_id == table_id)
    return list(s.scalars(q))


@system_work
def versions(s: Session, table_id: str) -> list[dict]:
    """Every version of one table at this client, newest first, with where it came from."""
    out = []
    lib = library().get(table_id)
    if lib is not None:
        out.append({"version": lib.version, "origin": "Platform library", "published_by": None, "published": None, "note": "", "enabled": lib.enabled})
    for row in published(s, table_id):
        out.append({"version": row.version, "origin": "Published by this client", "published_by": row.published_by,
                    "published": row.published.isoformat(), "note": row.note, "enabled": _parsed(row.source).enabled})
    out.sort(key=lambda v: version_key(v["version"]), reverse=True)
    if out:
        out[0]["active"] = True
    return out


@system_work
def active(s: Session) -> dict[str, DecisionTable]:
    """Every table in force at this client: the highest version of each, library or published."""
    tables = dict(library())
    for row in published(s):
        t = _parsed(row.source)
        if t.id not in tables or version_key(t.version) > version_key(tables[t.id].version):
            tables[t.id] = t
    return tables


@system_work
def get(s: Session, table_id: str, version: str | None = None) -> DecisionTable:
    if version is None:
        t = active(s).get(table_id)
    else:
        lib = library().get(table_id)
        t = lib if lib is not None and lib.version == version else None
        for row in published(s, table_id):
            if row.version == version:
                t = _parsed(row.source)
    if t is None:
        raise DecisionError(f"No decision table '{table_id}'" + (f" at version {version}" if version else ""))
    return t
