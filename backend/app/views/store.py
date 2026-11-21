"""Each person's grid settings (columns, density) and saved views, per client, with views shareable across the team.

A setting is one JSON value per (person, grid, name). A saved view is a named filter query on one grid: private to
whoever saved it, or shared with everyone at the client who can open that grid. Only the person who saved a view
can change or delete it. Nothing here can widen what a person sees, because a view is only a query the grid's own
API answers with that person's access.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..db import Base
from ..domain.personas import Persona, policy_of
from ..tenancy import TenantScoped, tenant_of

GRID = re.compile(r"^[a-z][a-z0-9_.-]{0,59}$")
SETTINGS = {"columns", "density"}  # what a grid may keep per person; views are rows of their own
MAX_VALUE = 8_000  # bytes of JSON per setting
MAX_QUERY = 2_000
MAX_VIEWS = 50  # per person and grid


def _now() -> datetime:
    from ..domain.models import now

    return now()


class GridSetting(TenantScoped, Base):
    __tablename__ = "grid_setting"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    person: Mapped[str] = mapped_column(String(60))
    grid: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(20))
    value: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    updated: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (UniqueConstraint("tenant_id", "person", "grid", "name", name="uq_grid_setting"),)


class SavedView(TenantScoped, Base):
    __tablename__ = "saved_view"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(60), index=True)
    grid: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str] = mapped_column(String(80))
    query: Mapped[str] = mapped_column(Text, default="")
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (UniqueConstraint("tenant_id", "owner", "grid", "name", name="uq_saved_view"),)


class ViewError(ValueError):
    pass


class NotYours(ViewError):
    pass


def _grid(grid: str) -> str:
    if not GRID.match(grid or ""):
        raise ViewError("A grid is named in lower-case letters, digits, dots, dashes or underscores")
    return grid


def _query(query: str) -> str:
    q = (query or "").lstrip("?")
    if len(q) > MAX_QUERY:
        raise ViewError(f"A view's filters must fit in {MAX_QUERY:,} characters")
    return re.sub(r"(^|&)(page|size)=[^&]*", "", q).lstrip("&")  # a view remembers the filters, not the page you were on


# --- settings ----------------------------------------------------------------------------------------------------------
def settings_for(s: Session, persona: Persona, grid: str) -> dict:
    rows = s.scalars(select(GridSetting).where(GridSetting.person == persona.id, GridSetting.grid == _grid(grid)))
    return {r.name: r.value for r in rows}


def put_setting(s: Session, persona: Persona, grid: str, name: str, value) -> None:
    from ..domain.models import new_id

    if name not in SETTINGS:
        raise ViewError(f"A grid keeps only {', '.join(sorted(SETTINGS))}")
    if len(json.dumps(value)) > MAX_VALUE:
        raise ViewError("That setting is too large to keep")
    row = s.scalars(select(GridSetting).where(GridSetting.person == persona.id, GridSetting.grid == _grid(grid), GridSetting.name == name)).first()
    if row is None:
        s.add(GridSetting(id=new_id("gs"), person=persona.id, grid=grid, name=name, value=value))
    else:
        row.value = value
    s.flush()


# --- views -------------------------------------------------------------------------------------------------------------
def row(v: SavedView, persona: Persona, people: dict | None = None) -> dict:
    who = (people or {}).get(v.owner)
    return {"id": v.id, "name": v.name, "query": v.query, "shared": v.shared, "owner": v.owner, "owner_name": who.name if who else v.owner,
            "mine": v.owner == persona.id, "updated": v.updated.isoformat() if v.updated else None}


def views_for(s: Session, persona: Persona, grid: str) -> list[dict]:
    """My views first, then those my colleagues shared, each group by name."""
    q = select(SavedView).where(SavedView.grid == _grid(grid), (SavedView.owner == persona.id) | SavedView.shared)
    found = sorted(s.scalars(q), key=lambda v: (v.owner != persona.id, v.name.lower(), v.owner))
    people = policy_of(tenant_of(s)).people
    return [row(v, persona, people) for v in found]


def save_view(s: Session, persona: Persona, grid: str, name: str, query: str, shared: bool = False) -> dict:
    """Save, or replace my own view of the same name."""
    from ..domain.models import new_id

    name = " ".join((name or "").split())
    if not name or len(name) > 80:
        raise ViewError("A view needs a name of at most 80 characters")
    if persona.read_only and shared:
        raise ViewError(f"{persona.title} cannot share views")
    mine = list(s.scalars(select(SavedView).where(SavedView.owner == persona.id, SavedView.grid == _grid(grid))))
    v = next((x for x in mine if x.name == name), None)
    if v is None:
        if len(mine) >= MAX_VIEWS:
            raise ViewError(f"You can keep at most {MAX_VIEWS} views on one list; delete one first")
        v = SavedView(id=new_id("view"), owner=persona.id, grid=grid, name=name)
        s.add(v)
    v.query, v.shared = _query(query), bool(shared)
    s.flush()
    return row(v, persona)


def _mine(s: Session, persona: Persona, view_id: str) -> SavedView:
    v = s.scalars(select(SavedView).where(SavedView.id == view_id)).first()
    if v is None or (v.owner != persona.id and not v.shared):
        raise ViewError("No such view")
    if v.owner != persona.id:
        raise NotYours("Only the person who saved a view can change or delete it")
    return v


def share_view(s: Session, persona: Persona, view_id: str, shared: bool) -> dict:
    v = _mine(s, persona, view_id)
    if persona.read_only and shared:
        raise ViewError(f"{persona.title} cannot share views")
    v.shared = bool(shared)
    s.flush()
    return row(v, persona)


def delete_view(s: Session, persona: Persona, view_id: str) -> None:
    s.delete(_mine(s, persona, view_id))
    s.flush()


def import_views(s: Session, persona: Persona, grid: str, views: list[dict]) -> int:
    """Bring over views a browser kept before views lived on the server. Names already saved are left alone."""
    have = {v["name"] for v in views_for(s, persona, grid) if v["mine"]}
    n = 0
    for v in views[:MAX_VIEWS]:
        name = " ".join(str(v.get("name") or "").split())
        if name and name not in have:
            try:
                save_view(s, persona, grid, name, str(v.get("query") or ""))
            except ViewError:
                continue
            have.add(name)
            n += 1
    return n
