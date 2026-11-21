"""Offboarding in the operator console: export a suspended client's data, hand it over, then erase the client."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import keys
from ..storage import StorageError
from ..tenants import offboarding
from ..tenants.offboarding import OffboardingError
from .deps import operator

router = APIRouter(prefix="/api/platform/offboarding")


def _refused(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


@router.get("")
def listing(client: str | None = None, _: dict = Depends(operator)):
    return {"offboardings": offboarding.listing(client)}


@router.post("/{tenant_id}/export", status_code=201)
def export(tenant_id: str, op: dict = Depends(operator)):
    try:
        return offboarding.export(tenant_id, op["id"])
    except (OffboardingError, StorageError, keys.KeyServiceError) as e:
        raise _refused(e) from None


@router.get("/exports/{oid}/download")
def download(oid: str, op: dict = Depends(operator)):
    try:
        name, blob = offboarding.download(oid, op["id"])
    except (OffboardingError, StorageError, keys.KeyServiceError) as e:
        raise _refused(e) from None
    return Response(blob, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


class EraseIn(BaseModel):
    confirm: str  # the client's name, typed


@router.post("/{tenant_id}/erase")
def erase(tenant_id: str, body: EraseIn, op: dict = Depends(operator)):
    try:
        return offboarding.offboard(tenant_id, op["id"], body.confirm)
    except (OffboardingError, keys.KeyServiceError) as e:
        raise _refused(e) from None
