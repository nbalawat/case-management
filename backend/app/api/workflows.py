"""Workflow configuration: read case types, check a draft, publish a new version."""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases import conditions, publishing
from ..cases.case_types import REQUIREMENTS, CaseTypeError, load_case_types, versions
from ..db import get_session
from ..domain.models import Case, WorkflowVersion
from ..domain.personas import PERMISSIONS, PERSONAS, POLICY, QUEUES, Persona
from .deps import require

router = APIRouter(prefix="/api")


def describe(ct: dict) -> dict:
    """A case type in the shape the UI draws: ordered stages, steps with readable conditions."""
    return {
        "case_type": ct["case_type"], "title": ct["title"], "domain": ct.get("domain", ""), "version": ct["version"],
        "initial_state": ct["initial_state"], "scenarios": ct.get("scenarios", []),
        "states": [{"id": k, "label": v["label"], "kind": v.get("kind", "open"), "queue": v.get("queue"),
                    "queue_label": QUEUES.get(v.get("queue") or "", ""), "sla_hours": v.get("sla_hours"),
                    "tasks": v.get("tasks", []), "disposition": v.get("disposition")} for k, v in ct["states"].items()],
        "transitions": [{"from": t["from"], "to": t["to"], "label": t["label"], "roles": t["roles"],
                         "requires": t.get("requires", []), "four_eyes": bool(t.get("four_eyes")),
                         "when": conditions.describe(t.get("when"))} for t in ct["transitions"]],
        "sla": ct.get("sla", {}), "qa": ct.get("qa"), "escalation": ct.get("escalation"), "agents": ct.get("agents", {}),
        "yaml": ct["source"],
    }


@router.get("/case-types")
def get_case_types(s: Session = Depends(get_session)):
    open_by = Counter((c.case_type, c.state) for c in s.scalars(select(Case).where(Case.closed.is_(None))))
    on_version = Counter((c.case_type, c.type_version) for c in s.scalars(select(Case).where(Case.closed.is_(None))))
    history = {}
    for row in s.scalars(select(WorkflowVersion).order_by(WorkflowVersion.id)):
        history[(row.case_type, row.version)] = {"published_by": row.published_by, "published": row.published.isoformat(), "note": row.note}
    out = []
    for ct in load_case_types().values():
        d = describe(ct)
        d["open_by_state"] = {st["id"]: open_by[(ct["case_type"], st["id"])] for st in d["states"]}
        d["versions"] = [v | history.get((ct["case_type"], v["version"]), {}) | {"open_cases": on_version[(ct["case_type"], v["version"])]}
                         for v in versions(ct["case_type"])]
        out.append(d)
    return sorted(out, key=lambda d: (d["domain"] == "Quality", d["domain"], d["title"]))


@router.get("/workflow-reference")
def reference():
    """Everything a workflow author can refer to, so the editor can explain itself."""
    return {"roles": [{"id": r, "people": [p.name for p in PERSONAS.values() if p.role == r]} for r in PERMISSIONS],
            "queues": [{"id": q, "label": label} for q, label in QUEUES.items()],
            "requirements": [{"id": k, "label": v} for k, v in REQUIREMENTS.items()],
            "facts": [{"id": k, "label": v} for k, v in conditions.FACTS.items()]}


class DraftIn(BaseModel):
    source: str
    note: str = ""


@router.post("/case-types/check")
def check(body: DraftIn):
    try:
        result = publishing.check(body.source)
    except CaseTypeError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "changes": result["changes"], "is_new": result["is_new"], "preview": describe(result["definition"])}


@router.post("/case-types/publish")
def publish(body: DraftIn, s: Session = Depends(get_session), p: Persona = Depends(require("publish_workflow"))):
    try:
        result = publishing.publish(s, body.source, p, body.note)
    except CaseTypeError as e:
        raise HTTPException(422, str(e)) from e
    ct = result["definition"]
    return {"ok": True, "case_type": ct["case_type"], "version": ct["version"], "changes": result["changes"]}


@router.get("/access")
def access():
    """The access model in full: the policy as configured, and what it means for every workflow step."""
    steps = []
    for ct in sorted(load_case_types().values(), key=lambda c: c["title"]):
        for t in ct["transitions"]:
            steps.append({"case_type": ct["case_type"], "title": ct["title"], "from": ct["states"][t["from"]]["label"],
                          "label": t["label"], "roles": t["roles"], "four_eyes": bool(t.get("four_eyes")),
                          "when": conditions.describe(t.get("when"))})
    return {
        "policy": {"name": POLICY.name, "version": POLICY.version},
        "permissions": [{"id": k, "label": v} for k, v in POLICY.permissions.items()],
        "roles": [{"id": r.id, "title": r.title, "permissions": sorted(r.permissions), "read_only": r.read_only,
                   "people": [p.name for p in POLICY.holders(r.id)]} for r in POLICY.roles.values()],
        "queues": [{"id": q, "label": label, "members": [p.name for p in POLICY.queue_members(q)]} for q, label in QUEUES.items()],
        "steps": steps,
    }
