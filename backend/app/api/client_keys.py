"""Per-client keys in the operator console: each client's key, and rotating it."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import keys
from ..tenants import client_keys
from ..tenants.registry import UnknownTenant, all_runtimes, runtime
from .deps import operator

router = APIRouter(prefix="/api/platform/keys")


@router.get("")
def listing(_: dict = Depends(operator)):
    out = []
    for rt in all_runtimes():
        sealed, plain = client_keys.documents(rt.id)
        out.append(client_keys.status(rt.id) | {"name": rt.name, "documents": {"sealed": sealed, "not_sealed": plain}})
    return {"provider": keys.provider().name if keys.provider() else "none", "clients": out}


@router.post("/{tenant_id}/rotate")
def rotate(tenant_id: str, who: dict = Depends(operator)):
    try:
        runtime(tenant_id)
    except UnknownTenant:
        raise HTTPException(404, "No such client") from None
    try:
        return client_keys.rotate(tenant_id, who["id"])
    except keys.KeyServiceError as e:
        raise HTTPException(422, str(e)) from None
