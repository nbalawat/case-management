"""Forms: the builder's API (list, read, check, publish, JSON Schema) and the forms on a case (fill, complete, reopen)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..cases.case_types import library as case_type_library, load_case_types
from ..decisions.facts import CASE_FACTS
from ..domain.personas import Persona
from ..forms import service, store
from ..forms.model import TYPES, FormError, from_dict, to_yaml
from ..forms.schema import to_json_schema
from ..forms.service import FormInvalid
from ..tenants.registry import TenantRuntime
from .deps import actor, get_case_or_404, get_session, require, tenant

router = APIRouter(prefix="/api")


@router.get("/forms")
def list_forms(s: Session = Depends(get_session)):
    return [{"id": f.id, "title": f.title, "version": f.version, "description": f.description, "case_types": list(f.case_types),
             "stages": list(f.stages), "fields": len(f.fields), "sections": len(f.sections)}
            for f in sorted(store.active(s).values(), key=lambda f: f.title)]


@router.get("/forms/reference")
def reference(rt: TenantRuntime = Depends(tenant)):
    types = case_type_library() | load_case_types(rt.id)
    return {"types": [{"id": k, "label": v} for k, v in TYPES.items()],
            "prefill": [{"name": f.name, "label": f.label, "kind": f.kind} for f in CASE_FACTS],
            "case_types": [{"id": k, "title": ct["title"], "stages": [{"id": sid, "label": st["label"]} for sid, st in ct["states"].items()
                                                                        if st.get("kind", "open") != "closed"]} for k, ct in types.items()]}


@router.get("/forms/{form_id}")
def get_form(form_id: str, version: str | None = None, s: Session = Depends(get_session)):
    try:
        f = store.get(s, form_id, version)
    except FormError as e:
        raise HTTPException(404, str(e)) from e
    return f.describe() | {"yaml": f.source or to_yaml(f), "versions": store.versions(s, form_id), "json_schema": to_json_schema(f)}


@router.get("/forms/{form_id}/schema")
def json_schema(form_id: str, version: str | None = None, s: Session = Depends(get_session)):
    try:
        f = store.get(s, form_id, version)
    except FormError as e:
        raise HTTPException(404, str(e)) from e
    return JSONResponse(to_json_schema(f), media_type="application/schema+json",
                        headers={"Content-Disposition": f'attachment; filename="{f.id}-{f.version}.schema.json"'})


class Draft(BaseModel):
    form: dict
    note: str = ""


def _read(body: Draft):
    f = from_dict(body.form)
    return from_dict(body.form, to_yaml(f))


@router.post("/forms/check")
def check(body: Draft, s: Session = Depends(get_session)):
    try:
        return {"ok": True} | service.check(s, _read(body))
    except FormError as e:
        return {"ok": False, "error": str(e)}


@router.post("/forms/publish")
def publish(body: Draft, s: Session = Depends(get_session), p: Persona = Depends(require("publish_workflow"))):
    try:
        f = _read(body)
        result = service.publish(s, f, p, body.note)
    except FormError as e:
        raise HTTPException(422, str(e)) from e
    return {"ok": True, "id": f.id, "version": f.version, "changes": result["changes"]}


# --- forms on a case ---------------------------------------------------------------------------------------------
@router.get("/cases/{case_id}/forms")
def case_forms(case_id: str, s: Session = Depends(get_session)):
    return service.for_case(s, get_case_or_404(s, case_id))


class Answers(BaseModel):
    data: dict
    complete: bool = False


@router.put("/cases/{case_id}/forms/{form_id}")
def save(case_id: str, form_id: str, body: Answers, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    case = get_case_or_404(s, case_id)
    try:
        sub = service.save(s, case, form_id, body.data, p, body.complete)
    except FormInvalid as e:
        return JSONResponse({"detail": str(e), "fields": e.fields}, status_code=422)
    except FormError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "status": sub.status, "data": sub.data}


class Reopen(BaseModel):
    reason: str


@router.post("/cases/{case_id}/forms/{form_id}/reopen")
def reopen(case_id: str, form_id: str, body: Reopen, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    try:
        sub = service.reopen(s, get_case_or_404(s, case_id), form_id, p, body.reason)
    except FormError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "status": sub.status}
