"""Grid settings and saved views, per person, on the server."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..domain.personas import Persona
from ..views import store
from .deps import get_session, persona

router = APIRouter(prefix="/api/grids")


class SettingIn(BaseModel):
    value: Any = None


class ViewIn(BaseModel):
    name: str
    query: str = ""
    shared: bool = False


class ShareIn(BaseModel):
    shared: bool


class ImportIn(BaseModel):
    views: list[dict] = []


def _run(fn, *args):
    try:
        return fn(*args)
    except store.NotYours as e:
        raise HTTPException(403, str(e)) from None
    except store.ViewError as e:
        raise HTTPException(404 if str(e) == "No such view" else 422, str(e)) from None


@router.get("/{grid}")
def get_grid(grid: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return {"settings": _run(store.settings_for, s, p, grid), "views": _run(store.views_for, s, p, grid)}


@router.put("/{grid}/settings/{name}")
def put_setting(grid: str, name: str, body: SettingIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    _run(store.put_setting, s, p, grid, name, body.value)
    return {"ok": True}


@router.post("/{grid}/views")
def post_view(grid: str, body: ViewIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return _run(store.save_view, s, p, grid, body.name, body.query, body.shared)


@router.post("/{grid}/views/import")
def import_views(grid: str, body: ImportIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return {"imported": _run(store.import_views, s, p, grid, body.views), "views": _run(store.views_for, s, p, grid)}


@router.post("/{grid}/views/{view_id}/share")
def share_view(grid: str, view_id: str, body: ShareIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    return _run(store.share_view, s, p, view_id, body.shared)


@router.delete("/{grid}/views/{view_id}")
def delete_view(grid: str, view_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    _run(store.delete_view, s, p, view_id)
    return {"ok": True}
