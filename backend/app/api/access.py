"""One client's access model: read it, check a draft, publish a new version."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..cases import conditions
from ..cases.case_types import load_case_types
from ..domain.access import AccessPolicy, AccessPolicyError
from ..domain.personas import Persona
from ..tenants import access_admin
from ..tenants.registry import TenantRuntime
from .deps import get_session, persona, require, tenant

router = APIRouter(prefix="/api")


def describe_policy(policy: AccessPolicy, titles: dict[str, str] | None = None) -> dict:
    def sees(views) -> list[str]:
        return ["Every case"] if views is None else [v.describe(titles) for v in views]

    return {
        "policy": {"name": policy.name, "version": policy.version},
        "permissions": [{"id": k, "label": v} for k, v in policy.permissions.items()],
        "roles": [{"id": r.id, "title": r.title, "permissions": sorted(r.permissions), "read_only": r.read_only,
                   "sees": sees(None if r.sees is None else (r.sees,))[0], "limited": r.sees is not None,
                   "restricted": "view_restricted" in r.permissions,
                   "people": [p.name for p in policy.holders(r.id)]} for r in policy.roles.values()],
        "queues": [{"id": q, "label": label, "members": [p.name for p in policy.queue_members(q)]} for q, label in policy.queues.items()],
        "people": [{"id": p.id, "name": p.name, "title": p.title, "roles": list(p.roles), "queues": list(p.queues),
                    "read_only": p.read_only, "sees": sees(p.views), "restricted": p.can("view_restricted"), "org": p.org} for p in policy.people.values()],
    }


def _titles(tenant_id: str) -> dict[str, str]:
    return {k: v["title"] for k, v in load_case_types(tenant_id).items()}


@router.get("/access")
def access(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(persona)):
    """The client's access model in full: the policy in force, its history, and what it means for every workflow step."""
    steps = []
    for ct in sorted(load_case_types(rt.id).values(), key=lambda c: c["title"]):
        for t in ct["transitions"]:
            steps.append({"case_type": ct["case_type"], "title": ct["title"], "from": ct["states"][t["from"]]["label"],
                          "label": t["label"], "roles": t["roles"], "four_eyes": bool(t.get("four_eyes")),
                          "when": conditions.describe(t.get("when"))})
    return describe_policy(rt.policy, _titles(rt.id)) | {"tenant": {"id": rt.id, "name": rt.name}, "steps": steps,
                                         "yaml": access_admin.active(s).source, "history": access_admin.history(s),
                                         "can_edit": p.can("manage_access")}


class PolicyIn(BaseModel):
    source: str
    note: str = ""


@router.post("/access/check")
def check(body: PolicyIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    try:
        result = access_admin.check(s, body.source)
    except AccessPolicyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "changes": result["changes"], "warnings": result["warnings"], "preview": describe_policy(result["policy"], _titles(rt.id))}


@router.post("/access/publish")
def publish(body: PolicyIn, s: Session = Depends(get_session), p: Persona = Depends(require("manage_access"))):
    try:
        result = access_admin.publish(s, body.source, p, body.note)
    except AccessPolicyError as e:
        raise HTTPException(422, str(e)) from e
    return {"ok": True, "version": result["policy"].version, "changes": result["changes"], "warnings": result["warnings"]}
