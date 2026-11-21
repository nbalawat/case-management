"""Management reporting and governance: the period report, the change register, access recertification and audit search."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..domain.personas import Persona
from ..governance import audit, recert, register
from ..reporting import mi
from ..tenants.registry import TenantRuntime
from .deps import actor, get_session, persona, tenant

router = APIRouter(prefix="/api")


def manager(p: Persona = Depends(persona)) -> Persona:
    """Management views show the whole operation, so they need the permission that shows the team."""
    if not p.can(mi.VIEW):
        raise HTTPException(403, f"{p.title} does not have the management view. It needs the 'view team' permission.")
    return p


def _do(fn):
    try:
        return fn()
    except (mi.MiError, audit.AuditError) as e:
        raise HTTPException(400, str(e)) from None
    except recert.RecertError as e:
        text = str(e)
        raise HTTPException(404 if "not found" in text else 403 if " needs the " in text else 409, text) from None


def _file(text: str, name: str, kind: str = "text/csv") -> PlainTextResponse:
    return PlainTextResponse(text, media_type=kind, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/mi")
def get_mi(period: str | None = None, s: Session = Depends(get_session), p: Persona = Depends(manager)):
    return _do(lambda: mi.report(s, period))


@router.get("/mi/export.csv")
def get_mi_csv(period: str | None = None, s: Session = Depends(get_session), p: Persona = Depends(manager), rt: TenantRuntime = Depends(tenant)):
    r = _do(lambda: mi.report(s, period))
    return _file(mi.as_csv(r), f"management-report-{rt.id}-{r['period']}.csv")


@router.get("/mi/pack.md")
def get_mi_pack(period: str | None = None, s: Session = Depends(get_session), p: Persona = Depends(manager), rt: TenantRuntime = Depends(tenant)):
    r = _do(lambda: mi.report(s, period))
    return _file(mi.as_pack(r, rt.name), f"management-report-{rt.id}-{r['period']}.md", "text/markdown")


def _since(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        raise HTTPException(400, "The first date is not a date") from None


@router.get("/governance")
def get_governance(area: str | None = None, since: str | None = None, s: Session = Depends(get_session), p: Persona = Depends(manager)):
    if area and area not in register.AREAS:
        raise HTTPException(400, f"An area is one of: {', '.join(register.AREAS)}")
    return {"register": register.entries(s, area, _since(since)), "areas": list(register.AREAS), "access": recert.view(s, p), "audit": audit.facets(s)}


@router.get("/governance/register.csv")
def get_register_csv(area: str | None = None, since: str | None = None, s: Session = Depends(get_session), p: Persona = Depends(manager), rt: TenantRuntime = Depends(tenant)):
    return _file(register.as_csv(register.entries(s, area if area in register.AREAS else None, _since(since))), f"change-register-{rt.id}.csv")


class ReviewIn(BaseModel):
    name: str
    due_days: int = 14


@router.post("/governance/access-reviews")
def post_review(body: ReviewIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return recert.row(s, _do(lambda: recert.start(s, p, body.name, body.due_days)))


class DecisionIn(BaseModel):
    decision: str
    note: str = ""


@router.post("/governance/access-reviews/{review_id}/people/{person}")
def post_review_decision(review_id: str, person: str, body: DecisionIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return recert.row(s, _do(lambda: recert.decide(s, review_id, person, body.decision, body.note, p)))


@router.post("/governance/access-reviews/{review_id}/close")
def post_review_close(review_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return recert.row(s, _do(lambda: recert.close(s, review_id, p)))


def _filters(actor: str | None, kind: str | None, case_id: str | None, date_from: str | None, date_to: str | None, actor_kind: str | None, text: str | None) -> dict:
    return {"actor": actor, "kind": kind, "case_id": case_id, "date_from": date_from, "date_to": date_to, "actor_kind": actor_kind, "text": text}


@router.get("/audit")
def get_audit(actor: str | None = None, kind: str | None = None, case_id: str | None = None, date_from: str | None = None, date_to: str | None = None,
              actor_kind: str | None = None, text: str | None = None, page: int = 1, s: Session = Depends(get_session), p: Persona = Depends(manager)):
    return _do(lambda: audit.search(s, **_filters(actor, kind, case_id, date_from, date_to, actor_kind, text), page=page))


@router.get("/audit/export.csv")
def get_audit_csv(actor: str | None = None, kind: str | None = None, case_id: str | None = None, date_from: str | None = None, date_to: str | None = None,
                  actor_kind: str | None = None, text: str | None = None, s: Session = Depends(get_session), p: Persona = Depends(manager),
                  rt: TenantRuntime = Depends(tenant)):
    return _file(_do(lambda: audit.as_csv(s, **_filters(actor, kind, case_id, date_from, date_to, actor_kind, text))), f"audit-trail-{rt.id}.csv")
