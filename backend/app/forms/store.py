"""Where forms come from: the shipped library and each client's published versions. The highest version is in force."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ROOT
from ..domain.models import FormVersion
from ..visibility import system_work
from .model import Form, FormError, parse, version_key

LIBRARY_DIR = ROOT / "backend" / "forms"


@lru_cache
def library(directory: str | None = None) -> dict[str, Form]:
    out = {}
    for f in sorted(Path(directory or LIBRARY_DIR).glob("*.yaml")):
        try:
            form = parse(f.read_text())
        except FormError as e:
            raise FormError(f"{f.name}: {e}") from None
        out[form.id] = form
    return out


@lru_cache(maxsize=512)
def _parsed(source: str) -> Form:
    return parse(source)


@system_work
def published(s: Session, form_id: str | None = None) -> list[FormVersion]:
    q = select(FormVersion).order_by(FormVersion.id)
    if form_id:
        q = q.where(FormVersion.form_id == form_id)
    return list(s.scalars(q))


@system_work
def active(s: Session) -> dict[str, Form]:
    forms = dict(library())
    for row in published(s):
        f = _parsed(row.source)
        if f.id not in forms or version_key(f.version) > version_key(forms[f.id].version):
            forms[f.id] = f
    return forms


@system_work
def get(s: Session, form_id: str, version: str | None = None) -> Form:
    if version is None:
        f = active(s).get(form_id)
    else:
        lib = library().get(form_id)
        f = lib if lib is not None and lib.version == version else None
        for row in published(s, form_id):
            if row.version == version:
                f = _parsed(row.source)
    if f is None:
        raise FormError(f"No form '{form_id}'" + (f" at version {version}" if version else ""))
    return f


@system_work
def versions(s: Session, form_id: str) -> list[dict]:
    out = []
    lib = library().get(form_id)
    if lib is not None:
        out.append({"version": lib.version, "origin": "Platform library", "published_by": None, "published": None, "note": ""})
    for row in published(s, form_id):
        out.append({"version": row.version, "origin": "Published by this client", "published_by": row.published_by,
                    "published": row.published.isoformat(), "note": row.note})
    out.sort(key=lambda v: version_key(v["version"]), reverse=True)
    if out:
        out[0]["active"] = True
    return out
