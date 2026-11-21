"""SCIM 2.0 (RFC 7643, RFC 7644) resources: users and groups to and from JSON, filters, and PATCH operations.

Only what identity providers actually send is accepted (Entra ID, Okta, Keycloak's SCIM plugins, OneLogin): `eq`
filters on the identifying attributes, and add, replace and remove on the attributes stored here. Anything else is
refused with the SCIM error the standard names, never ignored, so a provider that sends it learns it did not apply.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import service
from .models import DirectoryGroup, DirectoryUser

USER = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
MEDIA = "application/scim+json"
MAX_RESULTS = 200


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None):
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type

    def body(self) -> dict:
        out = {"schemas": [ERROR], "status": str(self.status), "detail": self.detail}
        return out | ({"scimType": self.scim_type} if self.scim_type else {})


def _when(d) -> str:
    return service.stamp(d) or ""


# ---- resources ------------------------------------------------------------------------------------------------------
def user_out(s: Session, u: DirectoryUser, base: str) -> dict:
    groups = service.groups_of(s, u.id)
    first, _, last = (u.display_name or "").partition(" ")
    return {"schemas": [USER], "id": u.id, "externalId": u.external_id, "userName": u.user_name, "displayName": u.display_name,
            "name": {"formatted": u.display_name, "givenName": first, "familyName": last}, "title": u.title, "active": u.active,
            "emails": [{"value": u.email, "type": "work", "primary": True}] if u.email else [],
            "groups": [{"value": g.id, "display": g.display_name, "$ref": f"{base}/Groups/{g.id}"} for g in groups],
            "meta": {"resourceType": "User", "created": _when(u.created), "lastModified": _when(u.updated), "location": f"{base}/Users/{u.id}"}}


def group_out(s: Session, g: DirectoryGroup, base: str, members: bool = True) -> dict:
    out = {"schemas": [GROUP], "id": g.id, "externalId": g.external_id, "displayName": g.display_name,
           "meta": {"resourceType": "Group", "created": _when(g.created), "lastModified": _when(g.updated), "location": f"{base}/Groups/{g.id}"}}
    if members:
        out["members"] = [{"value": u.id, "display": u.user_name, "$ref": f"{base}/Users/{u.id}"} for u in service.members_of(s, g.id)]
    return out


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false"):  # Entra ID sends "False"
        return v.lower() == "true"
    raise ScimError(400, "active must be true or false", "invalidValue")


def _email(body: dict) -> str:
    emails = body.get("emails") or []
    if not isinstance(emails, list):
        raise ScimError(400, "emails must be a list", "invalidSyntax")
    chosen = next((e for e in emails if isinstance(e, dict) and e.get("primary")), emails[0] if emails else None)
    return str(chosen.get("value") or "") if isinstance(chosen, dict) else ""


def _display(body: dict) -> str:
    name = body.get("name") if isinstance(body.get("name"), dict) else {}
    joined = " ".join(x for x in (name.get("givenName"), name.get("familyName")) if x)
    return str(body.get("displayName") or name.get("formatted") or joined or "")


def user_fields(body: dict) -> dict:
    if not isinstance(body, dict) or USER not in (body.get("schemas") or [USER]):
        raise ScimError(400, f"A user is a {USER}", "invalidSyntax")
    enterprise = body.get(ENTERPRISE) if isinstance(body.get(ENTERPRISE), dict) else {}
    return {"user_name": str(body.get("userName") or ""), "display_name": _display(body), "email": _email(body),
            "title": str(body.get("title") or enterprise.get("department") or ""), "external_id": body.get("externalId"),
            "active": _bool(body.get("active", True))}


def _member_ids(value) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(m, dict) and isinstance(m.get("value"), str) for m in value):
        raise ScimError(400, "members is a list of {value: <user id>}", "invalidValue")
    return [m["value"] for m in value]


# ---- filters --------------------------------------------------------------------------------------------------------
FILTER = re.compile(r'^\s*(\w+(?:\.\w+)?)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$', re.IGNORECASE)
USER_FILTERS = {"username": DirectoryUser.user_name, "externalid": DirectoryUser.external_id, "id": DirectoryUser.id,
                "emails.value": DirectoryUser.email}
GROUP_FILTERS = {"displayname": DirectoryGroup.display_name, "externalid": DirectoryGroup.external_id, "id": DirectoryGroup.id}


def where(text: str | None, columns: dict):
    if not text:
        return None
    m = FILTER.match(text)
    if not m or m.group(1).lower() not in columns:
        raise ScimError(400, f"Only 'eq' filters on {', '.join(sorted(columns))} are supported", "invalidFilter")
    return columns[m.group(1).lower()] == m.group(2).replace('\\"', '"')


def listing(s: Session, model, filter_text: str | None, columns: dict, start: int, count: int, render) -> dict:
    q = select(model).order_by(model.created, model.id)
    cond = where(filter_text, columns)
    if cond is not None:
        q = q.where(cond)
    rows = list(s.scalars(q))
    start, count = max(1, start), max(0, min(count, MAX_RESULTS))
    page = rows[start - 1:start - 1 + count]
    return {"schemas": [LIST], "totalResults": len(rows), "startIndex": start, "itemsPerPage": len(page), "Resources": [render(r) for r in page]}


# ---- PATCH ----------------------------------------------------------------------------------------------------------
def _ops(body: dict) -> list[dict]:
    if not isinstance(body, dict) or PATCH not in (body.get("schemas") or []):
        raise ScimError(400, f"A PATCH body is a {PATCH}", "invalidSyntax")
    ops = body.get("Operations")
    if not isinstance(ops, list) or not ops:
        raise ScimError(400, "Operations is a non-empty list", "invalidSyntax")
    for op in ops:
        if not isinstance(op, dict) or str(op.get("op", "")).lower() not in ("add", "replace", "remove"):
            raise ScimError(400, "Each operation is add, replace or remove", "invalidSyntax")
    return ops


USER_PATHS = {"active": "active", "username": "user_name", "displayname": "display_name", "title": "title", "externalid": "external_id",
              "name.formatted": "display_name", 'emails[type eq "work"].value': "email", "emails": "email"}


def patch_user(s: Session, u: DirectoryUser, body: dict) -> DirectoryUser:
    fields = {"user_name": u.user_name, "display_name": u.display_name, "email": u.email, "title": u.title,
              "external_id": u.external_id, "active": u.active}
    for op in _ops(body):
        kind, path, value = str(op["op"]).lower(), op.get("path"), op.get("value")
        if path is None:  # a whole object of attributes
            if not isinstance(value, dict):
                raise ScimError(400, "An operation without a path takes an object of attributes", "invalidValue")
            items = list(value.items())
        else:
            items = [(path, value)]
        for key, v in items:
            target = USER_PATHS.get(str(key).lower())
            if target is None:
                if str(key).lower() in ("name.givenname", "name.familyname", "name"):
                    continue  # the display name carries the name here
                raise ScimError(400, f"'{key}' cannot be changed here", "invalidPath")
            if kind == "remove":
                if target in ("user_name", "active"):
                    raise ScimError(400, f"'{key}' cannot be removed", "mutability")
                fields[target] = None if target == "external_id" else ""
            elif target == "active":
                fields[target] = _bool(v)
            elif target == "email" and isinstance(v, list):
                fields[target] = _email({"emails": v})
            else:
                fields[target] = "" if v is None else str(v)
    return service.save_user(s, u, **fields)


def patch_group(s: Session, g: DirectoryGroup, body: dict) -> DirectoryGroup:
    name, external = g.display_name, g.external_id
    for op in _ops(body):
        kind, path, value = str(op["op"]).lower(), str(op.get("path") or ""), op.get("value")
        lower = path.lower()
        if lower == "members" or (not path and isinstance(value, dict) and "members" in value):
            ids = _member_ids(value["members"] if not path else value) if kind != "remove" or value is not None else []
            if kind == "add":
                service.add_members(s, g, ids)
            elif kind == "replace":
                service.set_members(s, g, ids)
            else:
                service.remove_members(s, g, ids) if ids else service.set_members(s, g, [])
            if not path:
                value = {k: v for k, v in value.items() if k != "members"}
                if not value:
                    continue
        m = re.match(r'^members\[value eq "([^"]+)"\]$', path, re.IGNORECASE)
        if m and kind == "remove":
            service.remove_members(s, g, [m.group(1)])
            continue
        if lower == "displayname" and kind in ("add", "replace"):
            name = str(value or "")
        elif lower == "externalid":
            external = None if kind == "remove" else value
        elif not path and isinstance(value, dict):
            for k, v in value.items():
                if k.lower() == "displayname":
                    name = str(v or "")
                elif k.lower() == "externalid":
                    external = v
                elif k.lower() != "id":
                    raise ScimError(400, f"'{k}' cannot be changed here", "invalidPath")
        elif lower != "members":
            raise ScimError(400, f"'{path}' cannot be changed here", "invalidPath")
    return service.save_group(s, g, display_name=name, external_id=external)


# ---- what this service supports -------------------------------------------------------------------------------------
def service_provider_config(base: str) -> dict:
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "documentationUri": "https://datatracker.ietf.org/doc/html/rfc7644",
            "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": MAX_RESULTS}, "changePassword": {"supported": False},
            "sort": {"supported": False}, "etag": {"supported": False},
            "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token", "primary": True,
                                       "description": "A SCIM token made on the client's Access page"}],
            "meta": {"resourceType": "ServiceProviderConfig", "location": f"{base}/ServiceProviderConfig"}}


def resource_types(base: str) -> dict:
    types = [{"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": n, "name": n, "endpoint": f"/{n}s", "schema": sc,
              "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/{n}"}}
             for n, sc in (("User", USER), ("Group", GROUP))]
    return {"schemas": [LIST], "totalResults": 2, "startIndex": 1, "itemsPerPage": 2, "Resources": types}
