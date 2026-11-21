"""The workflow designer's API: compose a workflow from blocks, check it, try it on a sample case, import or export it,
save it as a draft and publish it as a new version of the client's case type.

Reading needs nothing beyond being signed in at the client; anything that saves or publishes needs 'publish workflow'.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from ..cases.case_types import CaseTypeError, load_case_types
from ..cases.service import TransitionError, create_case
from ..composer import catalog as palette
from ..composer import drafts, interchange, templates, trial
from ..composer.compiler import CompileError, Context, to_yaml
from ..composer.model import Design
from ..composer.validation import validate
from ..domain.models import Party
from ..domain.personas import Persona
from ..cases import publishing
from ..tenants.registry import TenantRuntime
from .deps import get_session, persona, require, tenant

router = APIRouter(prefix="/api/composer")
editor = require("publish_workflow")


def context(s: Session, rt: TenantRuntime) -> Context:
    forms = {f["id"]: f.get("title") or f["id"] for f in palette.optional_list("forms", "list_forms", s)}
    return Context.for_tenant(rt.id, rt.policy, palette.reason_counts(s), forms)


def design_of(raw: dict) -> Design:
    try:
        return Design.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise HTTPException(422, f"The design is not valid ({where}): {first['msg'].removeprefix('Value error, ')}") from e


@router.get("/catalog")
def get_catalog(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    return palette.build(s, context(s, rt))


@router.get("/templates")
def get_templates(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    return templates.catalog(context(s, rt))


class DesignIn(BaseModel):
    design: dict[str, Any]


@router.post("/validate")
def post_validate(body: DesignIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    """Every problem with a design in plain words, the case type it compiles to, and what publishing it would change."""
    design = design_of(body.design)
    ctx = context(s, rt)
    issues, definition = validate(design, ctx)
    out: dict = {"ok": definition is not None and not any(i["level"] == "error" for i in issues), "issues": issues,
                 "errors": sum(i["level"] == "error" for i in issues), "warnings": sum(i["level"] == "warning" for i in issues)}
    if definition is not None:
        source = to_yaml(definition)
        out["yaml"] = source
        try:
            check = publishing.check(rt.id, source)
            out["changes"], out["is_new"] = check["changes"], check["is_new"]
        except CaseTypeError as e:
            out["ok"] = False
            out["issues"].append({"level": "error", "target": "workflow", "id": None, "message": str(e).removeprefix("draft: "), "hint": ""})
        out["summary"] = {"stages": sum(1 for st in definition["states"].values() if st.get("kind") != "closed"),
                          "outcomes": sum(1 for st in definition["states"].values() if st.get("kind") == "closed"),
                          "steps": len(definition["transitions"]),
                          "automatic_actions": sum(len(st.get("on_enter") or []) for st in definition["states"].values())}
    return out


class TrialIn(BaseModel):
    design: dict[str, Any]
    sample: dict[str, Any] = {}
    steps: list[str] = Field(default_factory=list, max_length=trial.MAX_STEPS)


@router.post("/trial")
def post_trial(body: TrialIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    """Run a sample case through the draft for real, then throw it all away."""
    design = design_of(body.design)
    ctx = context(s, rt)
    issues, definition = validate(design, ctx)
    if definition is None:
        errors = [i for i in issues if i["level"] == "error"]
        return {"ok": False, "ready": False, "issues": errors,
                "why": f"Fix {len(errors)} problem{'s' if len(errors) != 1 else ''} before trying it: {errors[0]['message'] if errors else ''}"}
    sample = trial.Sample.from_dict(body.sample)
    result = trial.run(s, rt.id, rt.policy, definition, sample, body.steps)
    return {"ready": True, **result, "routes": trial.routes(definition, sample.facts()),
            "state_nodes": _state_nodes(design)}


def _state_nodes(design: Design) -> dict[str, str]:
    from ..composer.compiler import state_ids

    return {sid: nid for nid, sid in state_ids(design).items()}


class ImportIn(BaseModel):
    content: str = Field(max_length=2_000_000)
    format: str | None = None
    filename: str = ""


@router.post("/import")
def post_import(body: ImportIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    """Read a workflow file. Returns the design and notes on everything that had to be assumed; nothing is saved."""
    fmt = body.format or ("bpmn" if body.filename.lower().endswith((".bpmn", ".xml")) else
                          "case_type" if body.filename.lower().endswith((".yaml", ".yml")) else
                          "caseforge" if body.filename.lower().endswith(".json") else None)
    ctx = context(s, rt)
    try:
        design, notes = interchange.read(body.content, ctx, fmt)
    except interchange.InterchangeError as e:
        raise HTTPException(422, str(e)) from e
    issues, _ = validate(design, ctx)
    return {"design": design.model_dump(mode="json"), "notes": notes, "format": fmt or interchange.detect(body.content),
            "errors": sum(i["level"] == "error" for i in issues)}


class ExportIn(BaseModel):
    design: dict[str, Any]
    format: str = "caseforge"


@router.post("/export")
def post_export(body: ExportIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    design = design_of(body.design)
    fmt = interchange.FORMATS.get(body.format)
    if fmt is None:
        raise HTTPException(422, f"Unknown format '{body.format}'")
    try:
        content = interchange.write(design, context(s, rt), body.format)
    except (CompileError, interchange.InterchangeError, KeyError) as e:
        raise HTTPException(422, f"Cannot export yet: {e}. Fix the problems the designer lists first.") from e
    return {"filename": f"{design.key}-{design.version}{fmt.extension}", "media_type": fmt.media_type, "content": content}


# --- drafts ----------------------------------------------------------------------------------------------------------------
@router.get("/drafts")
def get_drafts(archived: bool = False, s: Session = Depends(get_session)):
    return drafts.listing(s, archived)


class NewDraftIn(BaseModel):
    template: str | None = None  # a template id, or case_type:<name> to edit a live workflow
    design: dict[str, Any] | None = None  # an imported design
    origin: str = ""


@router.post("/drafts", status_code=201)
def post_draft(body: NewDraftIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(editor)):
    ctx = context(s, rt)
    if body.design is not None:
        design, origin = design_of(body.design), body.origin or "imported file"
    else:
        tid = body.template or "blank"
        try:
            design = templates.build(tid, ctx)
        except KeyError as e:
            raise HTTPException(404, f"No template '{tid}'") from e
        origin = f"template {tid}" if not tid.startswith("case_type:") else f"live {tid.split(':', 1)[1]}"
    return drafts.full(drafts.create(s, design, p, origin))


def _draft(s: Session, draft_id: str):
    try:
        return drafts.get(s, draft_id)
    except drafts.DraftError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, s: Session = Depends(get_session)):
    return drafts.full(_draft(s, draft_id))


class SaveIn(BaseModel):
    design: dict[str, Any]
    revision: int


@router.put("/drafts/{draft_id}")
def put_draft(draft_id: str, body: SaveIn, s: Session = Depends(get_session), p: Persona = Depends(editor)):
    _draft(s, draft_id)
    try:
        return drafts.summary(drafts.save(s, draft_id, design_of(body.design), p, body.revision))
    except drafts.Conflict as e:
        raise HTTPException(409, str(e)) from e
    except drafts.DraftError as e:
        raise HTTPException(422, str(e)) from e


class ArchiveIn(BaseModel):
    restore: bool = False


@router.post("/drafts/{draft_id}/archive")
def post_archive(draft_id: str, body: ArchiveIn, s: Session = Depends(get_session), p: Persona = Depends(editor)):
    _draft(s, draft_id)
    return drafts.summary(drafts.archive(s, draft_id, p, body.restore))


class PublishIn(BaseModel):
    note: str = Field("", max_length=2000)


@router.post("/drafts/{draft_id}/publish")
def post_publish(draft_id: str, body: PublishIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant),
                 p: Persona = Depends(editor)):
    _draft(s, draft_id)
    try:
        return drafts.publish(s, draft_id, p, context(s, rt), body.note)
    except drafts.DraftError as e:
        raise HTTPException(422, str(e)) from e


# --- opening a case on a published workflow by hand ---------------------------------------------------------------------
class OpenIn(BaseModel):
    case_type: str
    party_id: str = Field(min_length=1, max_length=40)
    priority: str = "medium"


@router.post("/cases", status_code=201)
def open_case(body: OpenIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(persona)):
    """Open a case by hand on any of the client's live workflows, for work that does not start from an alert."""
    if p.read_only or not p.can("work"):
        raise HTTPException(403, f"{p.title} cannot open cases. It needs the 'work' permission.")
    types = load_case_types(rt.id)
    if body.case_type not in types:
        raise HTTPException(404, f"No live workflow '{body.case_type}'")
    if body.priority not in ("low", "medium", "high", "critical"):
        raise HTTPException(422, "Priority is low, medium, high or critical")
    if s.get(Party, {"tenant_id": rt.id, "id": body.party_id}) is None:
        raise HTTPException(404, f"No customer '{body.party_id}'")
    try:
        c = create_case(s, types[body.case_type], body.party_id, body.priority, actor=p.id)
    except TransitionError as e:
        raise HTTPException(422, str(e)) from e
    s.flush()
    return {"case_id": c.id, "state": c.state, "queue": c.queue}



