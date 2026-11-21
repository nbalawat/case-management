"""The client's directory on its Access page: who its identity provider provisioned, and the SCIM tokens it uses."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..directory import service
from ..domain.personas import Persona
from .deps import get_session, require

router = APIRouter(prefix="/api/directory")
admin = require("manage_access")


def _own(p: Persona) -> Persona:
    if p.external:
        raise HTTPException(403, "Only the client's own administrators manage its directory")
    return p


@router.get("")
def overview(s: Session = Depends(get_session), p: Persona = Depends(admin)):
    _own(p)
    return service.overview(s)


class TokenIn(BaseModel):
    label: str = Field(default="", max_length=120)


@router.post("/tokens", status_code=201)
def new_token(body: TokenIn, s: Session = Depends(get_session), p: Persona = Depends(admin)):
    """A token for the identity provider's SCIM settings. Its value is shown once, here, and never again."""
    return service.new_token(s, _own(p).id, body.label)


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, s: Session = Depends(get_session), p: Persona = Depends(admin)):
    try:
        service.revoke_token(s, token_id, _own(p).id)
    except service.DirectoryError as e:
        raise HTTPException(404, str(e)) from None
    return {"revoked": token_id}
