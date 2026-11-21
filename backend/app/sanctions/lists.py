"""The lists a client screens against, kept as versions.

A list is loaded whole, the way a publisher issues it: the load is compared with what is held, and entries are added,
changed or marked removed. Nothing is deleted, so it is always possible to say what the list said on a given day.
Every client starts with one small fictional list; a deployment loads the real ones from their publishers.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..detection.scenarios import DEMO_WATCHLIST, Watch
from ..domain.models import ScreeningExemption, Watchlist, WatchlistEntry, new_id, now, side_ids
from ..tenancy import tenant_of
from ..domain.personas import Persona
from ..visibility import system_work

MANAGE = "manage_lists"
KINDS = ("sanctions", "internal")
ENTRY_KINDS = ("entity", "person", "vessel")
MAX_ENTRIES = 50_000
STARTER = {"code": "demo_sanctions", "name": "Demonstration sanctions list", "kind": "sanctions",
           "source": "Fictional names for the demo. Not a real sanctions list.",
           "entries": [
               {"ref": "DEMO-001", "name": DEMO_WATCHLIST[0], "aliases": [], "kind": "entity", "countries": ["RU"], "programs": ["DEMO-MARITIME"]},
               {"ref": "DEMO-002", "name": DEMO_WATCHLIST[1], "aliases": [], "kind": "entity", "countries": ["AE"], "programs": ["DEMO-ENERGY"]},
               {"ref": "DEMO-003", "name": DEMO_WATCHLIST[2], "aliases": [], "kind": "entity", "countries": ["IR"], "programs": ["DEMO-MARITIME"]},
               {"ref": "DEMO-004", "name": DEMO_WATCHLIST[3], "aliases": [], "kind": "entity", "countries": ["AF"], "programs": ["DEMO-FINANCE"]},
           ]}
_code = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


class ListError(ValueError):
    pass


def fingerprint(e: WatchlistEntry | dict) -> str:
    """What identifies an entry's content. A decision made about an entry holds only while this is unchanged."""
    get = (lambda k: e.get(k)) if isinstance(e, dict) else (lambda k: getattr(e, k))
    body = {"name": get("name"), "aliases": sorted(get("aliases") or []), "kind": get("kind"), "countries": sorted(get("countries") or []),
            "identifiers": sorted(f"{i.get('type')}:{i.get('value')}" for i in (get("identifiers") or []))}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _clean(rows: list) -> list[dict]:
    if not isinstance(rows, list):
        raise ListError("A list is a set of entries")
    if len(rows) > MAX_ENTRIES:
        raise ListError(f"A single load can hold at most {MAX_ENTRIES:,} entries")
    out, refs = [], set()
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise ListError(f"Entry {i} is not a record")
        name, ref = str(r.get("name") or "").strip(), str(r.get("ref") or "").strip()
        if not name:
            raise ListError(f"Entry {i} has no name")
        if not ref:
            raise ListError(f"Entry {i} ('{name}') has no reference. The publisher's id is what ties one version of a list to the next.")
        if ref in refs:
            raise ListError(f"Reference '{ref}' appears twice")
        refs.add(ref)
        kind = str(r.get("kind") or "entity").strip().lower()
        if kind not in ENTRY_KINDS:
            raise ListError(f"Entry {i} ('{name}'): kind must be one of {', '.join(ENTRY_KINDS)}")
        several = lambda v: [str(x).strip() for x in (v if isinstance(v, list) else str(v or "").split(";")) if str(x).strip()]  # noqa: E731
        ids = r.get("identifiers") or []
        if isinstance(ids, str):
            ids = [{"type": a.strip(), "value": b.strip()} for a, _, b in (x.partition(":") for x in ids.split(";")) if a.strip() and b.strip()]
        countries = [c.upper() for c in several(r.get("countries"))]
        if any(not re.fullmatch(r"[A-Z]{2}", c) for c in countries):
            raise ListError(f"Entry {i} ('{name}'): countries are two-letter codes")
        out.append({"ref": ref[:60], "name": name[:200], "aliases": [a[:200] for a in several(r.get("aliases")) if a.lower() != name.lower()], "kind": kind,
                    "countries": countries, "programs": several(r.get("programs")),
                    "identifiers": [{"type": str(x.get("type", ""))[:30], "value": str(x.get("value", ""))[:60]} for x in ids if isinstance(x, dict)]})
    return out


def parse(text: str) -> list[dict]:
    """Read a list from JSON (an array of entries) or CSV with a header row. Several values in one CSV cell are separated by ';'."""
    text = text.lstrip("﻿").strip()
    if not text:
        raise ListError("The file is empty")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ListError(f"The file is not valid JSON: {e}") from None
        return data["entries"] if isinstance(data, dict) and "entries" in data else data
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "name" not in rows[0] or "ref" not in rows[0]:
        raise ListError("A CSV list needs a header row with at least 'ref' and 'name'")
    return rows


def _apply(s: Session, wl: Watchlist, rows: list[dict], by: str, note: str, at: datetime) -> dict:
    """Bring a list to exactly these entries, as a new version. Returns what changed."""
    held = {e.ref: e for e in s.scalars(select(WatchlistEntry).where(WatchlistEntry.list_id == wl.id, WatchlistEntry.removed_version.is_(None)))}
    version = wl.version + 1
    added, changed, removed, touched = [], [], [], []
    for r in rows:
        e = held.pop(r["ref"], None)
        if e is None:
            e = WatchlistEntry(id=new_id("wle"), list_id=wl.id, added_version=version, **r)
            s.add(e)
            added.append(r["name"])
        elif fingerprint(e) != fingerprint(r) or e.programs != r["programs"]:
            if fingerprint(e) != fingerprint(r):
                touched.append(e.id)
            e.name, e.aliases, e.kind, e.countries, e.programs, e.identifiers = r["name"], r["aliases"], r["kind"], r["countries"], r["programs"], r["identifiers"]
            changed.append(r["name"])
    for e in held.values():
        e.removed_version = version
        removed.append(e.name)
        touched.append(e.id)
    if not (added or changed or removed) and wl.version:
        return {"version": wl.version, "added": [], "changed": [], "removed": [], "exemptions_ended": 0, "unchanged": True}
    s.flush()
    ended = 0
    if touched:  # a decision about an entry does not carry over to a different entry
        for x in s.scalars(select(ScreeningExemption).where(ScreeningExemption.entry_id.in_(touched), ScreeningExemption.status.in_(("active", "pending")))):
            x.status, x.decided_at = "revoked", at
            x.decision_note = (x.decision_note + " " if x.decision_note else "") + f"Ended automatically: the list entry changed or was removed in version {version} of {wl.name}."
            ended += 1
    wl.version, wl.updated_by, wl.updated_at = version, by, at
    wl.history = [*wl.history, {"version": version, "at": at.isoformat(), "by": by, "added": len(added), "changed": len(changed), "removed": len(removed),
                                "note": note[:300], "names": {"added": added[:20], "changed": changed[:20], "removed": removed[:20]}}]
    s.flush()
    return {"version": version, "added": added, "changed": changed, "removed": removed, "exemptions_ended": ended, "unchanged": False}


@system_work
def ensure(s: Session) -> None:
    """Give a client its starting list the first time lists are needed."""
    if s.scalars(select(Watchlist.id)).first() is None:
        with side_ids(f"lists:{tenant_of(s)}"):
            wl = Watchlist(id=new_id("wl"), code=STARTER["code"], name=STARTER["name"], kind=STARTER["kind"], source=STARTER["source"], history=[])
            s.add(wl)
            s.flush()
            _apply(s, wl, _clean(STARTER["entries"]), "platform", "The list every client starts with", now())
            s.flush()


@system_work
def entries(s: Session) -> list[WatchlistEntry]:
    """Every entry in force: on an active list, and not removed."""
    ensure(s)
    active = set(s.scalars(select(Watchlist.id).where(Watchlist.active.is_(True))))
    return [e for e in s.scalars(select(WatchlistEntry).where(WatchlistEntry.removed_version.is_(None)).order_by(WatchlistEntry.name, WatchlistEntry.id))
            if e.list_id in active]


def names(s: Session) -> list[str]:
    """Primary names of the entries in force, for the places that only need to compare against them."""
    return [e.name for e in entries(s)]


@system_work
def watch(s: Session, at: datetime | None = None) -> Watch:
    """What a screening run compares against: names, aliases, the pairs ruled out, and the client's threshold."""
    at = at or now()
    pairs = tuple((n, e.id) for e in entries(s) for n in [e.name, *e.aliases])
    exempt = frozenset((x.subject_key, x.entry_id) for x in s.scalars(select(ScreeningExemption).where(ScreeningExemption.status == "active"))
                       if x.expires is None or x.expires > at)
    return Watch(pairs, exempt, float(reference.setting(s, "screening_rules", "match_at", "value", 0.86)))


def _may(persona: Persona) -> None:
    if persona.read_only or not persona.can(MANAGE):
        raise ListError("Changing a screening list needs the permission to manage lists")


@system_work
def load(s: Session, code: str, rows: list, persona: Persona, name: str = "", kind: str = "sanctions", source: str = "", note: str = "",
         at: datetime | None = None) -> dict:
    """Load a list as its publisher issued it. A new code makes a new list; an existing one gets a new version."""
    _may(persona)
    ensure(s)
    if not _code.match(code or ""):
        raise ListError("A list needs a short code: lower-case letters, digits and underscores")
    clean = _clean(rows)
    wl = s.scalars(select(Watchlist).where(Watchlist.code == code)).first()
    if wl is None:
        if not clean:
            raise ListError("A new list needs at least one entry")
        if kind not in KINDS:
            raise ListError(f"A list is one of: {', '.join(KINDS)}")
        if not name.strip():
            raise ListError("A new list needs a name")
        wl = Watchlist(id=new_id("wl"), code=code, name=name.strip()[:120], kind=kind, source=source.strip()[:200], history=[])
        s.add(wl)
        s.flush()
    elif source.strip():
        wl.source = source.strip()[:200]
    return {"list": wl.code} | _apply(s, wl, clean, persona.id, note, at or now())


def _current(s: Session, wl: Watchlist) -> list[dict]:
    return [{"ref": e.ref, "name": e.name, "aliases": e.aliases, "kind": e.kind, "countries": e.countries, "programs": e.programs, "identifiers": e.identifiers}
            for e in s.scalars(select(WatchlistEntry).where(WatchlistEntry.list_id == wl.id, WatchlistEntry.removed_version.is_(None)))]


def _list(s: Session, code: str) -> Watchlist:
    wl = s.scalars(select(Watchlist).where(Watchlist.code == code)).first()
    if wl is None:
        raise ListError("List not found")
    return wl


@system_work
def put_entry(s: Session, code: str, entry: dict, persona: Persona, note: str = "") -> dict:
    """Add one entry to a list, or change the one with the same reference. For the client's own lists."""
    _may(persona)
    wl = _list(s, code)
    if wl.kind != "internal":
        raise ListError("A published list is changed by loading its next version, not entry by entry")
    new = _clean([entry])[0]
    return {"list": code} | _apply(s, wl, [r for r in _current(s, wl) if r["ref"] != new["ref"]] + [new], persona.id, note, now())


@system_work
def remove_entry(s: Session, code: str, ref: str, persona: Persona, note: str = "") -> dict:
    _may(persona)
    wl = _list(s, code)
    if wl.kind != "internal":
        raise ListError("A published list is changed by loading its next version, not entry by entry")
    rows = _current(s, wl)
    if not any(r["ref"] == ref for r in rows):
        raise ListError("Entry not found")
    if not note.strip():
        raise ListError("Say why the entry is being removed")
    return {"list": code} | _apply(s, wl, [r for r in rows if r["ref"] != ref], persona.id, note, now())


@system_work
def set_active(s: Session, code: str, active: bool, persona: Persona, note: str) -> Watchlist:
    """Stop or resume screening against a list. Its entries and history stay."""
    _may(persona)
    wl = _list(s, code)
    if not note.strip():
        raise ListError("Say why screening against this list is being switched")
    if not active and wl.kind == "sanctions" and not any(x.active and x.kind == "sanctions" and x.id != wl.id for x in s.scalars(select(Watchlist))):
        raise ListError("This is the only sanctions list in force. Load its replacement before switching it off.")
    wl.active, wl.updated_by, wl.updated_at = active, persona.id, now()
    wl.history = [*wl.history, {"version": wl.version, "at": wl.updated_at.isoformat(), "by": persona.id, "added": 0, "changed": 0, "removed": 0,
                                "note": f"{'Switched on' if active else 'Switched off'}: {note.strip()[:250]}"}]
    s.flush()
    return wl


def entry_row(e: WatchlistEntry) -> dict:
    return {"id": e.id, "ref": e.ref, "name": e.name, "aliases": e.aliases, "kind": e.kind, "countries": e.countries, "programs": e.programs,
            "identifiers": e.identifiers, "added_version": e.added_version, "removed_version": e.removed_version}


@system_work
def view(s: Session, persona: Persona, limit: int = 200) -> dict:
    ensure(s)
    out = []
    for wl in s.scalars(select(Watchlist).order_by(Watchlist.kind.desc(), Watchlist.name)):
        rows = list(s.scalars(select(WatchlistEntry).where(WatchlistEntry.list_id == wl.id).order_by(WatchlistEntry.removed_version.is_not(None), WatchlistEntry.name)))
        live = [e for e in rows if e.removed_version is None]
        out.append({"code": wl.code, "name": wl.name, "kind": wl.kind, "source": wl.source, "version": wl.version, "active": wl.active,
                    "updated_by": wl.updated_by, "updated_at": wl.updated_at.isoformat(), "entries": len(live), "removed": len(rows) - len(live),
                    "history": list(reversed(wl.history))[:12], "rows": [entry_row(e) for e in rows[:limit]], "more": max(0, len(rows) - limit)})
    return {"lists": out, "can_manage": persona.can(MANAGE) and not persona.read_only, "kinds": list(KINDS), "entry_kinds": list(ENTRY_KINDS)}
