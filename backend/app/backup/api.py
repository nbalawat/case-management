"""Backups for platform staff. Making, checking and drilling are here; restoring is a command run on purpose
(`python -m app.backup restore`), never a button, because it writes a whole database."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..api.deps import operator
from . import service
from .manifest import BackupError

router = APIRouter(prefix="/api/platform/backups")


class CreateIn(BaseModel):
    client: str | None = None
    documents: bool = True


def _store():
    try:
        return service.target()
    except BackupError as e:
        raise HTTPException(409, str(e)) from None


@router.get("")
def list_all(_: dict = Depends(operator)):
    store = _store()
    return {"location": store.url.split("://", 1)[0], "backups": [m.summary() for m in service.list_backups(store)][:50],
            "drills": service.drills(store)}


@router.post("", status_code=201)
def make(body: CreateIn, _: dict = Depends(operator)):
    try:
        return service.create(scope=body.client or "platform", store=_store(), with_documents=body.documents).summary()
    except BackupError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/{backup_id}/verify")
def check(backup_id: str, _: dict = Depends(operator)):
    try:
        return service.verify(backup_id, _store())
    except BackupError as e:
        return {"id": backup_id, "ok": False, "error": str(e)}


@router.post("/drill")
def run_drill(_: dict = Depends(operator)):
    try:
        return service.drill(store=_store())
    except BackupError as e:
        raise HTTPException(422, str(e)) from None
