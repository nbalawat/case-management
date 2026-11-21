"""Operator console: the provider's own view across clients.

Platform staff see how each client is running - volumes, backlog, breaches - and bring new clients on.
They get counts only. Nothing here returns a customer, a case or a narrative: reading client data means
signing in to that client, under that client's access policy.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cases.case_types import library
from ..db import session_scope
from ..domain.models import AccessPolicyVersion, Alert, Case, CaseEvent, Tenant, Transaction, WorkflowVersion, now
from ..tenants import onboarding
from ..tenants.onboarding import DEPLOYMENTS, OnboardingError
from ..tenants.registry import runtime
from ..tenants.templates import templates
from .access import describe_policy
from .deps import operator, platform_session

router = APIRouter(prefix="/api/platform")
REGIONS = {"us-east": "US East", "us-west": "US West", "eu-west": "EU West", "uk-south": "UK South", "ap-southeast": "Asia Pacific"}


def _counts(s: Session, model, *where) -> dict[str, int]:
    return dict(s.execute(select(model.tenant_id, func.count()).where(*where).group_by(model.tenant_id)).all())


def _tenant_out(row: Tenant) -> dict:
    rt = runtime(row.id)
    return {"id": row.id, "name": row.name, "template": row.template, "template_title": templates()[row.template].title,
            "region": row.region, "region_label": REGIONS.get(row.region, row.region), "deployment": row.deployment,
            "deployment_label": DEPLOYMENTS.get(row.deployment, row.deployment), "status": row.status,
            "created": row.created.isoformat(), "case_types": list(rt.case_types), "people": len(rt.policy.people),
            "roles": len(rt.policy.roles), "queues": len(rt.policy.queues), "policy_version": rt.policy.version,
            "identity": (rt.settings or {}).get("identity"), "identity_declared": _declared(row.id)}


def _declared(tenant_id: str) -> str | None:
    """The issuer OIDC_CLIENTS declares for this client, used when none is registered here."""
    from ..auth.identity import configured

    p = configured().get(tenant_id)
    return p.issuer if p else None


@router.get("/overview")
def overview(s: Session = Depends(platform_session), op: dict = Depends(operator)):
    today = now()
    open_cases = _counts(s, Case, Case.closed.is_(None))
    all_cases = _counts(s, Case)
    breached = _counts(s, Case, Case.closed.is_(None), Case.sla_due < today)
    alerts, txns = _counts(s, Alert), _counts(s, Transaction)
    workflow_versions, policy_versions = _counts(s, WorkflowVersion), _counts(s, AccessPolicyVersion)
    last = dict(s.execute(select(CaseEvent.tenant_id, func.max(CaseEvent.ts)).group_by(CaseEvent.tenant_id)).all())
    rows = []
    for row in s.scalars(select(Tenant).order_by(Tenant.created, Tenant.id)):
        rows.append(_tenant_out(row) | {
            "open_cases": open_cases.get(row.id, 0), "cases": all_cases.get(row.id, 0), "sla_breached": breached.get(row.id, 0),
            "alerts": alerts.get(row.id, 0), "transactions": txns.get(row.id, 0),
            "own_workflow_versions": workflow_versions.get(row.id, 0), "policy_versions": policy_versions.get(row.id, 0),
            "last_activity": last[row.id].isoformat() if last.get(row.id) else None})
    return {"operator": op, "tenants": rows,
            "totals": {"clients": len(rows), "live": sum(1 for r in rows if r["status"] == "live"),
                       "open_cases": sum(r["open_cases"] for r in rows), "transactions": sum(r["transactions"] for r in rows),
                       "alerts": sum(r["alerts"] for r in rows), "sla_breached": sum(r["sla_breached"] for r in rows)},
            "isolation": {"model": "Shared database, every row keyed by client",
                          "enforced": "In the data layer: every query is filtered and every write is stamped, with no action by application code",
                          "next": "Postgres row-level security as a second layer; dedicated stamps for clients that need them"}}


@router.get("/templates")
def get_templates(_: dict = Depends(operator)):
    out = []
    for tpl in templates().values():
        plan = onboarding.preview(tpl.id)
        out.append({"id": tpl.id, "title": tpl.title, "summary": tpl.summary,
                    "case_types": [{"id": d["case_type"], "title": d["title"], "domain": d.get("domain", "")} for d in plan["definitions"]],
                    "access": describe_policy(plan["policy"])})
    return {"templates": out, "regions": REGIONS, "deployments": DEPLOYMENTS,
            "library": [{"id": d["case_type"], "title": d["title"], "domain": d.get("domain", "")} for d in library().values()]}


class OnboardIn(BaseModel):
    id: str = Field(description="Short id used in URLs, for example 'northwind'")
    name: str
    template: str
    region: str = "us-east"
    deployment: str = "pooled"
    case_types: list[str] | None = None
    sample_data: bool = False


@router.post("/tenants/check")
def check_tenant(body: OnboardIn, s: Session = Depends(platform_session)):
    """Everything onboarding would validate, without creating anything."""
    try:
        if s.get(Tenant, body.id) is not None:
            raise OnboardingError(f"A client with id '{body.id}' already exists")
        if not onboarding.SLUG.match(body.id) or body.id in onboarding.RESERVED:
            raise OnboardingError("Client id must be 3 to 31 characters: lower-case letters, digits and hyphens, starting with a letter")
        plan = onboarding.preview(body.template, body.case_types)
    except OnboardingError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "case_types": plan["case_types"], "people": len(plan["policy"].people), "roles": len(plan["policy"].roles),
            "queues": len(plan["policy"].queues)}


@router.post("/tenants", status_code=201)
def create_tenant(body: OnboardIn, op: dict = Depends(operator)):
    try:
        with session_scope(platform=True) as s:
            onboarding.onboard(s, tenant_id=body.id, name=body.name, template=body.template, region=body.region,
                               deployment=body.deployment, case_types=body.case_types, by=op["name"])
    except OnboardingError as e:
        raise HTTPException(422, str(e)) from e
    seeded = None
    if body.sample_data:
        from ..seed import PROFILES, seed_tenant

        with session_scope(body.id) as s:
            seeded = seed_tenant(s, PROFILES["starter"])
    with session_scope(platform=True) as s:
        return _tenant_out(s.get(Tenant, body.id)) | {"sample_data": seeded}


class StatusIn(BaseModel):
    status: str


@router.post("/tenants/{tenant_id}/status")
def post_status(tenant_id: str, body: StatusIn, s: Session = Depends(platform_session)):
    try:
        onboarding.set_status(s, tenant_id, body.status)
    except OnboardingError as e:
        raise HTTPException(422, str(e)) from e
    return _tenant_out(s.get(Tenant, tenant_id))


class CaseTypesIn(BaseModel):
    case_types: list[str]


@router.put("/tenants/{tenant_id}/case-types")
def put_case_types(tenant_id: str, body: CaseTypesIn, s: Session = Depends(platform_session)):
    in_use = set(s.scalars(select(Case.case_type).where(Case.tenant_id == tenant_id, Case.closed.is_(None)).distinct()))
    try:
        onboarding.set_case_types(s, tenant_id, body.case_types, in_use)
    except OnboardingError as e:
        raise HTTPException(422, str(e)) from e
    return _tenant_out(s.get(Tenant, tenant_id))


class StorageIn(BaseModel):
    locations: list[dict]


@router.put("/tenants/{tenant_id}/storage")
def put_storage(tenant_id: str, body: StorageIn, s: Session = Depends(platform_session)):
    """Register the storage locations a client's pipelines may read. Only platform staff can do this."""
    try:
        rt = onboarding.set_storage(s, tenant_id, body.locations)
    except OnboardingError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": tenant_id, "locations": rt.settings["storage"]}


class IdentityIn(BaseModel):
    provider: dict | None


class FilingProfileIn(BaseModel):
    regime: str = "fincen"
    channel: str = "outbox"
    institution: dict = {}


@router.put("/tenants/{tenant_id}/filing")
def put_filing(tenant_id: str, body: FilingProfileIn, s: Session = Depends(platform_session)):
    """Record how a client reports to its regulator. Only platform staff can do this."""
    try:
        rt = onboarding.set_filing(s, tenant_id, body.model_dump())
    except onboarding.OnboardingError as e:
        raise HTTPException(400, str(e)) from None
    return {"id": tenant_id, "filing": rt.settings.get("filing")}


@router.put("/tenants/{tenant_id}/identity")
def put_identity(tenant_id: str, body: IdentityIn, s: Session = Depends(platform_session)):
    """Register the identity provider a client's people sign in through. Only platform staff can do this."""
    try:
        rt = onboarding.set_identity(s, tenant_id, body.provider)
    except OnboardingError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": tenant_id, "identity": rt.settings.get("identity")}
