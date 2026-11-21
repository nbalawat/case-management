"""Reference data: the code lists a client's people choose from. Every client starts with the library's lists and may replace any."""
from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain.models import CodeList, now
from .domain.personas import Persona
from .visibility import system_work

LIBRARY = Path(__file__).resolve().parents[1] / "reference" / "code_lists.yaml"
MANAGE = "publish_workflow"  # the people who configure workflows also keep the lists those workflows use
_code = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


class ReferenceError(Exception):
    pass


@functools.cache
def library() -> dict:
    return yaml.safe_load(LIBRARY.read_text())


def codes(s: Session, list_id: str, include_retired: bool = False) -> list[dict]:
    """The codes in force for this client: its own list if it has one, otherwise the library's."""
    if list_id not in library():
        raise ReferenceError(f"Unknown code list '{list_id}'")
    row = s.scalars(select(CodeList).where(CodeList.id == list_id)).first()
    items = row.codes if row is not None else library()[list_id]["codes"]
    return [dict(c) for c in items if include_retired or not c.get("retired")]


def label(s: Session, list_id: str, code: str) -> str:
    return next((c["label"] for c in codes(s, list_id, include_retired=True) if c["code"] == code), code.replace("_", " "))


def describe(s: Session) -> list[dict]:
    own = {r.id: r for r in s.scalars(select(CodeList))}
    return [{"id": k, "title": v["title"], "about": v["about"], "fields": v.get("fields") or {}, "closed": bool(v.get("closed")), "own": k in own, "codes": codes(s, k, include_retired=True),
             "updated_by": own[k].updated_by if k in own else None, "updated_at": own[k].updated_at.isoformat() if k in own else None}
            for k, v in library().items()]


def check(list_id: str, items: list) -> list[dict]:
    if list_id not in library():
        raise ReferenceError(f"Unknown code list '{list_id}'")
    optional = bool(library()[list_id].get("optional"))
    if not isinstance(items, list) or (not items and not optional):
        raise ReferenceError("A code list needs at least one code")
    seen, out = set(), []
    spec = library()[list_id]
    if spec.get("closed"):  # the platform tests for exactly these codes, so a client tunes them but cannot invent one
        extra = sorted({str(c.get("code")) for c in items if isinstance(c, dict)} - {c["code"] for c in spec["codes"]})
        if extra:
            raise ReferenceError(f"This list is fixed by the platform; unknown code(s): {', '.join(extra)}")
    for c in items:
        if not isinstance(c, dict) or not _code.match(str(c.get("code") or "")):
            raise ReferenceError("Each code needs a short id: lower-case letters, digits and underscores, starting with a letter")
        if c["code"] in seen:
            raise ReferenceError(f"Code '{c['code']}' appears twice")
        if not str(c.get("label") or "").strip():
            raise ReferenceError(f"Code '{c['code']}' needs a label")
        seen.add(c["code"])
        clean = {"code": c["code"], "label": str(c["label"]).strip()[:200]}
        if c.get("retired"):
            clean["retired"] = True
        for field, kind in (library()[list_id].get("fields") or {}).items():
            if field not in c:
                continue
            v = c[field]
            number = isinstance(v, (int, float)) and not isinstance(v, bool)
            ok = (isinstance(v, list) and all(isinstance(x, str) for x in v)) if kind == "list" else (number and v > 0) if kind == "number" else \
                (number and v >= 0) if kind == "count" else isinstance(v, str)
            if not ok:
                must = {"list": "a list of names", "number": "a positive number", "count": "zero or a positive number"}.get(kind, "text")
                raise ReferenceError(f"Code '{c['code']}': {field.replace('_', ' ')} must be {must}")
            clean[field] = v
        out.append(clean)
    if out and all(c.get("retired") for c in out) and not optional:
        raise ReferenceError("At least one code must stay in use")
    return out


@system_work
def replace(s: Session, list_id: str, items: list, persona: Persona) -> list[dict]:
    """Give this client its own version of a list. Codes already used on cases should be retired, not removed, so history still reads."""
    clean = check(list_id, items)
    row = s.scalars(select(CodeList).where(CodeList.id == list_id)).first()
    used = {c["code"] for c in (row.codes if row is not None else library()[list_id]["codes"])}
    gone = sorted(used - {c["code"] for c in clean})
    if gone and not library()[list_id].get("optional"):
        raise ReferenceError(f"Retire codes instead of removing them, so past records still read: {', '.join(gone)}")
    if row is None:
        row = CodeList(id=list_id, codes=clean, updated_by=persona.id, updated_at=now())
        s.add(row)
    else:
        row.codes, row.updated_by, row.updated_at = clean, persona.id, now()
    s.flush()
    return clean


def setting(s: Session, list_id: str, code: str, field: str, default=None):
    """One field of one code in force, for lists that hold settings rather than choices."""
    return next((c.get(field, default) for c in codes(s, list_id) if c["code"] == code), default)
