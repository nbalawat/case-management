"""Decision tables for one client: list, read, try, check a draft against recent cases, publish, exchange as DMN."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..decisions import DecisionError, HitPolicyViolation, service, store
from ..decisions.analysis import analyse
from ..decisions.bindings import BINDINGS
from ..decisions.engine import evaluate
from ..decisions.facts import CONTEXTS, vocabulary
from ..decisions.feel import KINDS
from ..decisions.model import AGGREGATIONS, HIT_POLICIES, from_dict, to_yaml
from ..domain.personas import Persona
from .deps import get_session, require

router = APIRouter(prefix="/api/decisions")


def _summary(t) -> dict:
    return {"id": t.id, "title": t.title, "version": t.version, "description": t.description, "context": t.context,
            "binding": t.binding, "binding_label": BINDINGS.get(t.binding, BINDINGS["none"]).label, "enabled": t.enabled,
            "hit_policy": t.hit_policy, "rules": len(t.rules), "inputs": len(t.inputs), "outputs": [c.name for c in t.outputs]}


@router.get("")
def list_tables(s: Session = Depends(get_session)):
    return sorted((_summary(t) for t in store.active(s).values()), key=lambda d: (not d["enabled"], d["title"]))


@router.get("/reference")
def reference():
    """What a table can be made of, so the editor can offer it."""
    return {"hit_policies": list(HIT_POLICIES), "aggregations": list(AGGREGATIONS), "kinds": list(KINDS),
            "contexts": {name: vocabulary(name) for name in CONTEXTS},
            "bindings": [{"id": b.id, "label": b.label, "context": b.context, "output": b.output, "values": list(b.allowed or [])} for b in BINDINGS.values()]}


@router.get("/{table_id}")
def get_table(table_id: str, version: str | None = None, s: Session = Depends(get_session)):
    try:
        t = store.get(s, table_id, version)
    except DecisionError as e:
        raise HTTPException(404, str(e)) from e
    return t.describe() | {"yaml": t.source or to_yaml(t), "versions": store.versions(s, table_id), "analysis": analyse(t).as_dict()}


class Draft(BaseModel):
    source: str | None = None  # YAML
    table: dict | None = None  # the grid editor's shape (as GET returns it)
    dmn: str | None = None  # DMN 1.3 XML
    note: str = ""


def _read(body: Draft):
    if body.table is not None:
        t = from_dict(body.table)
        return from_dict(body.table, to_yaml(t))
    return service.read(body.source, body.dmn)


@router.post("/check")
def check(body: Draft, s: Session = Depends(get_session)):
    try:
        return {"ok": True} | service.check(s, _read(body))
    except DecisionError as e:
        return {"ok": False, "error": str(e)}


@router.post("/publish")
def publish(body: Draft, s: Session = Depends(get_session), p: Persona = Depends(require("publish_workflow"))):
    try:
        t = _read(body)
        result = service.publish(s, t, p, body.note)
    except DecisionError as e:
        raise HTTPException(422, str(e)) from e
    return {"ok": True, "id": t.id, "version": t.version, "changes": result["changes"]}


class TryIn(BaseModel):
    inputs: dict
    table: dict | None = None  # try an unpublished draft


@router.post("/{table_id}/evaluate")
def try_table(table_id: str, body: TryIn, s: Session = Depends(get_session)):
    try:
        t = from_dict(body.table) if body.table is not None else store.get(s, table_id)
        return {"ok": True} | evaluate(t, body.inputs).as_dict()
    except HitPolicyViolation as e:
        return {"ok": False, "error": str(e)}
    except DecisionError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/{table_id}/dmn")
def export(table_id: str, version: str | None = None, s: Session = Depends(get_session)):
    try:
        xml = service.export_dmn(s, table_id, version)
    except DecisionError as e:
        raise HTTPException(404, str(e)) from e
    return PlainTextResponse(xml, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{table_id}.dmn"'})
