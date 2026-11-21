"""The app shell's view of the signed-in person: desks to show and counts for the menu."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..cases.case_types import load_case_types
from ..domain.access import Persona
from ..navigation import badges, desks
from ..tenants.registry import TenantRuntime
from .deps import get_session, persona, tenant

router = APIRouter(prefix="/api")


@router.get("/navigation")
def navigation(s: Session = Depends(get_session), p: Persona = Depends(persona), rt: TenantRuntime = Depends(tenant)):
    return {"desks": desks(p, load_case_types(rt.id)), "badges": badges(s, p)}
