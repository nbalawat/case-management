"""SCIM 2.0 provisioning (RFC 7644): a client's identity provider creates, changes and removes its people and groups.

The identity provider authenticates with a SCIM token an administrator made on the client's Access page. The token
names its client, so no header or path can point a request at another client, and it is checked against that
client's own tokens under that client's session (row-level security applies as to any other request).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from ..db import session_scope
from ..directory import scim, service
from ..directory.models import DirectoryGroup, DirectoryUser
from ..directory.scim import MEDIA, ScimError
from ..tenants.registry import TenantRuntime, UnknownTenant, runtime

PUBLIC = True  # the identity provider has no person's sign-in; its SCIM token is checked below instead
PREFIX = "/api/scim/v2"


class ScimRoute(APIRoute):
    """Every refusal leaves as a SCIM error (RFC 7644 3.12), wherever it was raised, so no app-wide handler is needed."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            try:
                return await handler(request)
            except ScimError as e:
                return scim_error(e)
        return run


router = APIRouter(prefix=PREFIX, route_class=ScimRoute)


@dataclass
class Caller:
    s: Session
    rt: TenantRuntime
    base: str


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/") + PREFIX


def provisioner(request: Request, authorization: str | None = Header(default=None)) -> Iterator[Caller]:
    scheme, _, value = (authorization or "").partition(" ")
    value = value.strip()
    tenant = service.tenant_of_token(value) if scheme.lower() == "bearer" else None
    if not tenant:
        raise ScimError(401, "A SCIM bearer token is required")
    try:
        rt = runtime(tenant)
    except UnknownTenant:
        raise ScimError(401, "The SCIM token is not valid") from None
    if rt.status != "live":
        raise ScimError(403, f"{rt.name} is suspended")
    with session_scope(rt.id) as s:
        if not service.check_token(s, value):
            raise ScimError(401, "The SCIM token is not valid")
        yield Caller(s, rt, _base(request))


def scim_error(e: ScimError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if e.status == 401 else None
    return JSONResponse(e.body(), status_code=e.status, media_type=MEDIA, headers=headers)


def _out(body: dict, status: int = 200, location: str | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=MEDIA, headers={"Location": location} if location else None)


async def _body(request: Request) -> dict:
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise ScimError(400, "The body is not valid JSON", "invalidSyntax") from None
    if not isinstance(body, dict):
        raise ScimError(400, "The body is a JSON object", "invalidSyntax")
    return body


def _changed(c: Caller, fn: Callable):
    """Run one change, refusing what the directory refuses in SCIM's terms, then bring the client's people up to date."""
    try:
        out = fn()
        c.s.flush()
    except service.DirectoryError as e:
        raise ScimError(409 if "already exists" in str(e) else 400, str(e), "uniqueness" if "already exists" in str(e) else "invalidValue") from None
    service.refresh(c.s, c.rt)
    return out


def _user(c: Caller, uid: str) -> DirectoryUser:
    u = c.s.get(DirectoryUser, uid)
    if u is None:
        raise ScimError(404, f"User {uid} not found")
    return u


def _group(c: Caller, gid: str) -> DirectoryGroup:
    g = c.s.get(DirectoryGroup, gid)
    if g is None:
        raise ScimError(404, f"Group {gid} not found")
    return g


# ---- discovery ------------------------------------------------------------------------------------------------------
@router.get("/ServiceProviderConfig")
def config(c: Caller = Depends(provisioner)):
    return _out(scim.service_provider_config(c.base))


@router.get("/ResourceTypes")
def resource_types(c: Caller = Depends(provisioner)):
    return _out(scim.resource_types(c.base))


# ---- users ----------------------------------------------------------------------------------------------------------
@router.get("/Users")
def list_users(filter: str | None = None, startIndex: int = 1, count: int = 100, c: Caller = Depends(provisioner)):  # noqa: A002, N803
    return _out(scim.listing(c.s, DirectoryUser, filter, scim.USER_FILTERS, startIndex, count, lambda u: scim.user_out(c.s, u, c.base)))


@router.post("/Users")
async def create_user(request: Request, c: Caller = Depends(provisioner)):
    fields = scim.user_fields(await _body(request))
    u = _changed(c, lambda: service.save_user(c.s, None, **fields))
    out = scim.user_out(c.s, u, c.base)
    return _out(out, 201, out["meta"]["location"])


@router.get("/Users/{uid}")
def get_user(uid: str, c: Caller = Depends(provisioner)):
    return _out(scim.user_out(c.s, _user(c, uid), c.base))


@router.put("/Users/{uid}")
async def replace_user(uid: str, request: Request, c: Caller = Depends(provisioner)):
    u, fields = _user(c, uid), scim.user_fields(await _body(request))
    return _out(scim.user_out(c.s, _changed(c, lambda: service.save_user(c.s, u, **fields)), c.base))


@router.patch("/Users/{uid}")
async def patch_user(uid: str, request: Request, c: Caller = Depends(provisioner)):
    u, body = _user(c, uid), await _body(request)
    return _out(scim.user_out(c.s, _changed(c, lambda: scim.patch_user(c.s, u, body)), c.base))


@router.delete("/Users/{uid}", status_code=204)
def delete_user(uid: str, c: Caller = Depends(provisioner)):
    u = _user(c, uid)
    _changed(c, lambda: service.delete_user(c.s, u))
    return Response(status_code=204)


# ---- groups ---------------------------------------------------------------------------------------------------------
@router.get("/Groups")
def list_groups(filter: str | None = None, startIndex: int = 1, count: int = 100, excludedAttributes: str = "",  # noqa: A002, N803
                c: Caller = Depends(provisioner)):
    members = "members" not in excludedAttributes.lower()
    return _out(scim.listing(c.s, DirectoryGroup, filter, scim.GROUP_FILTERS, startIndex, count,
                             lambda g: scim.group_out(c.s, g, c.base, members)))


def _group_fields(body: dict) -> dict:
    if scim.GROUP not in (body.get("schemas") or [scim.GROUP]):
        raise ScimError(400, f"A group is a {scim.GROUP}", "invalidSyntax")
    members = body.get("members")
    return {"display_name": str(body.get("displayName") or ""), "external_id": body.get("externalId"),
            "members": None if members is None else scim._member_ids(members)}


@router.post("/Groups")
async def create_group(request: Request, c: Caller = Depends(provisioner)):
    fields = _group_fields(await _body(request))
    g = _changed(c, lambda: service.save_group(c.s, None, **fields))
    out = scim.group_out(c.s, g, c.base)
    return _out(out, 201, out["meta"]["location"])


@router.get("/Groups/{gid}")
def get_group(gid: str, excludedAttributes: str = "", c: Caller = Depends(provisioner)):  # noqa: N803
    return _out(scim.group_out(c.s, _group(c, gid), c.base, "members" not in excludedAttributes.lower()))


@router.put("/Groups/{gid}")
async def replace_group(gid: str, request: Request, c: Caller = Depends(provisioner)):
    g, fields = _group(c, gid), _group_fields(await _body(request))
    fields["members"] = fields["members"] or []  # a full replace: no members listed means none
    return _out(scim.group_out(c.s, _changed(c, lambda: service.save_group(c.s, g, **fields)), c.base))


@router.patch("/Groups/{gid}")
async def patch_group(gid: str, request: Request, c: Caller = Depends(provisioner)):
    g, body = _group(c, gid), await _body(request)
    _changed(c, lambda: scim.patch_group(c.s, g, body))
    return Response(status_code=204)  # RFC 7644 3.5.2: 204 when the response carries no resource; Entra ID expects it


@router.delete("/Groups/{gid}", status_code=204)
def delete_group(gid: str, c: Caller = Depends(provisioner)):
    g = _group(c, gid)
    _changed(c, lambda: service.delete_group(c.s, g))
    return Response(status_code=204)
