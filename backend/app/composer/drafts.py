"""Drafts: designs saved as people work on them, then published as a new version of the client's case type."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases import publishing
from ..cases.case_types import CaseTypeError
from ..domain.access import Persona
from ..domain.models import WorkflowDraft, new_id, now
from .compiler import Context, to_yaml
from .model import Design
from .validation import validate


class DraftError(ValueError):
    pass


class Conflict(DraftError):
    """Someone saved the draft since this editor loaded it."""


def summary(d: WorkflowDraft) -> dict:
    return {"id": d.id, "case_type": d.case_type, "title": d.title, "status": d.status, "revision": d.revision, "origin": d.origin,
            "created_by": d.created_by, "created": d.created.isoformat(), "updated_by": d.updated_by, "updated": d.updated.isoformat(),
            "published_version": d.published_version, "version": (d.design or {}).get("version"),
            "blocks": len((d.design or {}).get("nodes", []))}


def full(d: WorkflowDraft) -> dict:
    return summary(d) | {"design": d.design}


def listing(s: Session, include_archived: bool = False) -> list[dict]:
    q = select(WorkflowDraft).order_by(WorkflowDraft.updated.desc())
    if not include_archived:
        q = q.where(WorkflowDraft.status != "archived")
    return [summary(d) for d in s.scalars(q)]


def get(s: Session, draft_id: str) -> WorkflowDraft:
    d = s.get(WorkflowDraft, draft_id)
    if d is None:
        raise DraftError("No such draft")
    return d


def create(s: Session, design: Design, persona: Persona, origin: str = "") -> WorkflowDraft:
    d = WorkflowDraft(id=new_id("wfd"), case_type=design.key, title=design.title, design=design.model_dump(mode="json"), origin=origin[:80],
                      created_by=persona.id, updated_by=persona.id)
    s.add(d)
    s.flush()
    return d


def save(s: Session, draft_id: str, design: Design, persona: Persona, revision: int) -> WorkflowDraft:
    d = get(s, draft_id)
    if d.status == "archived":
        raise DraftError("This draft is archived. Restore it to edit it.")
    if revision != d.revision:
        raise Conflict(f"Saved by {d.updated_by} since you opened it. Reload to see their changes.")
    d.design, d.case_type, d.title = design.model_dump(mode="json"), design.key, design.title
    d.revision += 1
    d.updated_by, d.updated = persona.id, now()
    if d.status == "published":
        d.status = "draft"  # editing a published design starts its next version
    s.flush()
    return d


def archive(s: Session, draft_id: str, persona: Persona, restore: bool = False) -> WorkflowDraft:
    d = get(s, draft_id)
    d.status = "draft" if restore else "archived"
    d.updated_by, d.updated = persona.id, now()
    d.revision += 1
    s.flush()
    return d


def publish(s: Session, draft_id: str, persona: Persona, ctx: Context, note: str = "") -> dict:
    """Compile, check and publish a draft. The client's version history, change register and both engines take it from here."""
    d = get(s, draft_id)
    design = Design.model_validate(d.design)
    issues, definition = validate(design, ctx)
    errors = [i for i in issues if i["level"] == "error"]
    if errors or definition is None:
        raise DraftError(f"Fix {len(errors)} problem{'s' if len(errors) != 1 else ''} first: {errors[0]['message'] if errors else 'it does not compile'}")
    source = to_yaml(definition)
    try:
        result = publishing.publish(s, source, persona, note or f"Published from the workflow designer (draft {d.id})")
    except CaseTypeError as e:
        raise DraftError(str(e).removeprefix("draft: ")) from e
    d.status, d.published_version = "published", definition["version"]
    d.updated_by, d.updated = persona.id, now()
    d.revision += 1
    s.flush()
    return {"case_type": definition["case_type"], "version": definition["version"], "changes": result["changes"], "draft": summary(d)}
