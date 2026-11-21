"""The model gateway on the API: a client's model set-up and usage, and the policy platform staff set for it.

People at the client who hold the team view or manage agents can see which model answers their questions, where
it runs, the limits and the usage. Only platform staff change the policy, because it decides where data is sent.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai import gateway, policy, usage
from ..domain.models import Tenant
from ..domain.personas import Persona
from ..tenants.onboarding import OnboardingError
from .deps import get_session, persona, platform_session

router = APIRouter()


@router.get("/api/ai")
def get_ai(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    if not (p.can("view_team") or p.can("manage_agents")):
        raise HTTPException(403, f"{p.title} cannot see this. It needs the 'view team' or 'manage agents' permission.")
    return gateway.describe(s)


class PolicyIn(BaseModel):
    policy: dict | None = None  # None or {} returns the client to the deployment default


@router.get("/api/platform/tenants/{tenant_id}/model")
def get_model_policy(tenant_id: str, s: Session = Depends(platform_session)):
    row = s.get(Tenant, tenant_id)
    if row is None:
        raise HTTPException(404, f"Unknown client '{tenant_id}'")
    pol = policy.of(row.settings)
    return {"id": tenant_id, "policy": pol.as_dict(), "ask": gateway.resolve(pol, "ask").describe(),
            "agents": gateway.resolve(pol, "agents").describe(), "used_this_month": usage.used_this_month(s, tenant_id),
            "usage": usage.summary(s, tenant_id=tenant_id), "endpoints": [e.describe() for e in gateway.eps.endpoints().values()]}


@router.put("/api/platform/tenants/{tenant_id}/model")
def put_model_policy(tenant_id: str, body: PolicyIn, s: Session = Depends(platform_session)):
    """Set which model serves a client, where inference may run and its spend limits. Only platform staff can do this."""
    try:
        policy.set_policy(s, tenant_id, body.policy)
    except (OnboardingError, policy.PolicyError) as e:
        raise HTTPException(422, str(e)) from None
    return get_model_policy(tenant_id, s)
