"""The directory: who the client's identity provider says works there, and what their groups let them do here.

Two ways people get roles from their identity provider, both through the `groups:` section of the access policy:

  provisioned  The provider pushes people and groups over SCIM (app/api/scim.py). Each active person in a mapped
               group is a person here, with the roles and queues of all their groups, before they ever sign in.
               Deactivating them at the provider (or removing them from every mapped group) takes them out at once.
  at sign-in   A token that lists the person's groups (a `groups` claim) gives the same roles for that request, for
               clients that do not provision. Nobody named in the policy is changed this way.

Someone named in the access policy keeps exactly the roles the policy gives them. If the directory has them and
they are deactivated there, they cannot sign in: the identity provider is the source of truth for who still works
at the client.
"""
from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import replace
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..domain.access import AccessPolicy, AccessPolicyError, Persona, make_persona
from ..domain.models import AccessEvent, now
from ..public import credentials
from ..tenancy import tenant_of
from ..tenants.registry import TenantRuntime, runtime
from .models import DirectoryGroup, DirectoryMember, DirectoryUser, ScimToken

DIRECTORY = "directory"
TOKEN_PREFIX = "cfscim_"
# A person id here is the name their token carries; the same characters the policy accepts, plus what usernames use.
USER_NAME = re.compile(r"^[A-Za-z0-9._@+-]{1,200}$")


class DirectoryError(ValueError):
    pass


# ---- from groups to a person ----------------------------------------------------------------------------------------
def persona_from_groups(policy: AccessPolicy, pid: str, name: str, groups: list[str], title: str = "") -> Persona | None:
    """The person these groups make, or None when no group they are in is mapped. Raises on a bad combination."""
    grants = [policy.groups[g] for g in groups if g in policy.groups]
    if not grants:
        return None
    roles = list(dict.fromkeys(r for g in grants for r in g.roles))
    queues = list(dict.fromkeys(q for g in grants for q in g.queues))
    via = ", ".join(sorted(g.group for g in grants))
    return make_persona(policy.roles, policy.queues, pid, name or pid, roles, queues, title or None,
                        f"From the identity provider: {via}.", source=DIRECTORY)


def people(s: Session, policy: AccessPolicy) -> tuple[dict[str, Persona], list[dict]]:
    """Every active provisioned person the policy's groups give a role to, and why each other one has none."""
    users = {u.id: u for u in s.scalars(select(DirectoryUser).where(DirectoryUser.active.is_(True)))}
    names = {g.id: g.display_name for g in s.scalars(select(DirectoryGroup))}
    member_of: dict[str, list[str]] = {}
    for m in s.scalars(select(DirectoryMember)):
        if m.group_id in names:
            member_of.setdefault(m.user_id, []).append(names[m.group_id])
    out, skipped = {}, []
    for u in users.values():
        if u.user_name in policy.people and policy.people[u.user_name].source != DIRECTORY:
            continue  # named in the policy: the policy decides their roles
        try:
            p = persona_from_groups(policy, u.user_name, u.display_name, sorted(member_of.get(u.id, [])), u.title)
        except AccessPolicyError as e:
            skipped.append({"user": u.user_name, "reason": str(e)})
            continue
        if p is None:
            skipped.append({"user": u.user_name, "reason": "in no group the access policy maps to a role"})
            continue
        out[p.id] = p
    return out, skipped


def apply(s: Session, policy: AccessPolicy) -> AccessPolicy:
    """The policy with the directory's people in it, in place of whoever the directory gave it before."""
    found, _ = people(s, policy)
    off = set(s.scalars(select(DirectoryUser.user_name).where(DirectoryUser.active.is_(False))))
    named = {k: p for k, p in (policy.people | policy.switched_off).items() if p.source != DIRECTORY}
    # Someone the policy names whom the identity provider has switched off is out too: no sign-in, no new cases.
    out = {k: p for k, p in named.items() if k in off and not p.external}
    kept = {k: p for k, p in named.items() if k not in out}
    return replace(policy, people=kept | {k: p for k, p in found.items() if k not in kept}, switched_off=out)


def refresh(s: Session, rt: TenantRuntime) -> TenantRuntime:
    rt.policy = apply(s, rt.policy)
    return rt


def person(s: Session, policy: AccessPolicy, user_name: str) -> Persona | None:
    """This provisioned person as the directory says right now, read from the database, not from this process's copy.

    Several processes serve requests; a SCIM change lands in one of them. Reading here at sign-in means a group
    change or a new person counts everywhere at once, not only after a restart.
    """
    u = s.scalars(select(DirectoryUser).where(DirectoryUser.user_name == user_name, DirectoryUser.active.is_(True))).first()
    if u is None:
        return None
    try:
        return persona_from_groups(policy, u.user_name, u.display_name, [g.display_name for g in groups_of(s, u.id)], u.title)
    except AccessPolicyError:
        return None


def signing_in(s: Session, policy: AccessPolicy, user_name: str, groups: tuple[str, ...] = (), name: str = "") -> Persona | None:
    """The person a client's user is as they sign in, or None when nothing here gives them a role.

    A user the identity provider provisioned is whatever the directory says now: refused if deactivated, their
    groups' roles otherwise (unless the policy names them). A user it never provisioned is who the policy names, or
    else whatever the groups listed in their token give.
    """
    u = s.scalars(select(DirectoryUser).where(DirectoryUser.user_name == user_name)).first()
    if u is not None and not u.active:
        raise DirectoryError(f"'{user_name}' has been deactivated by {runtime(tenant_of(s)).name}'s identity provider")
    named = policy.people.get(user_name)
    if named is not None and named.source != DIRECTORY:
        return named
    if u is not None:
        return person(s, policy, user_name)
    try:
        return persona_from_groups(policy, user_name, name, list(groups))
    except AccessPolicyError:
        return None


def deactivated(s: Session, user_name: str) -> bool:
    """Whether the identity provider has this person and has switched them off."""
    u = s.scalars(select(DirectoryUser).where(DirectoryUser.user_name == user_name)).first()
    return u is not None and not u.active


# ---- tokens the identity provider provisions with -------------------------------------------------------------------
def new_token(s: Session, by: str, label: str = "") -> dict:
    """A SCIM token. Shown once: only its keyed hash is kept. It names the client, so a request finds its client."""
    tenant = tenant_of(s)
    value = f"{TOKEN_PREFIX}{tenant}_{secrets.token_urlsafe(32)}"
    row = ScimToken(id=f"scim-{uuid.uuid4().hex[:12]}", digest=credentials.digest(value), hint=credentials.hint(value),
                    label=label.strip()[:120], created_by=by)
    s.add(row)
    s.add(AccessEvent(actor=by, org="client", kind="directory.token_created", subject="SCIM token", detail={"token": row.id}))
    s.flush()
    return {"id": row.id, "token": value, "hint": row.hint, "label": row.label}


def revoke_token(s: Session, token_id: str, by: str) -> None:
    row = s.get(ScimToken, token_id)
    if row is None or row.revoked is not None:
        raise DirectoryError("No such token")
    row.revoked = now()
    s.add(AccessEvent(actor=by, org="client", kind="directory.token_revoked", subject="SCIM token", detail={"token": token_id}))


def tokens(s: Session) -> list[dict]:
    return [{"id": t.id, "hint": t.hint, "label": t.label, "created_by": t.created_by, "created": t.created.isoformat(),
             "last_used": t.last_used.isoformat() if t.last_used else None, "revoked": t.revoked.isoformat() if t.revoked else None}
            for t in s.scalars(select(ScimToken).order_by(ScimToken.created.desc()))]


def tenant_of_token(value: str) -> str | None:
    """Which client a token claims to be for. Only a hint: the token is then checked against that client's own."""
    if not value.startswith(TOKEN_PREFIX):
        return None
    tenant, sep, _ = value[len(TOKEN_PREFIX):].partition("_")
    return tenant if sep else None


def check_token(s: Session, value: str) -> bool:
    want = credentials.digest(value)
    row = s.scalars(select(ScimToken).where(ScimToken.digest == want, ScimToken.revoked.is_(None))).first()
    if row is None:
        return False
    row.last_used = now()
    return True


# ---- people and groups, as SCIM changes them ------------------------------------------------------------------------
def _changed(s: Session, by: str, kind: str, subject: str, **detail) -> None:
    s.add(AccessEvent(actor=by, org="client", kind=f"directory.{kind}", subject=subject[:200], detail=detail))


def save_user(s: Session, u: DirectoryUser | None, *, user_name: str, display_name: str = "", email: str = "", title: str = "",
              external_id: str | None = None, active: bool = True, by: str = "scim") -> DirectoryUser:
    if not USER_NAME.match(user_name or ""):
        raise DirectoryError("userName must be 1 to 200 letters, digits or . _ @ + -")
    if user_name.startswith("ms."):
        raise DirectoryError("userNames starting 'ms.' are kept for the platform provider's staff")
    clash = s.scalars(select(DirectoryUser).where(DirectoryUser.user_name == user_name)).first()
    if clash is not None and (u is None or clash.id != u.id):
        raise DirectoryError(f"userName '{user_name}' already exists", )
    created = u is None
    if u is None:
        u = DirectoryUser(id=uuid.uuid4().hex)
        s.add(u)
    was_active = u.active if not created else None
    u.user_name, u.display_name, u.email, u.title = user_name, display_name[:200], email[:320], title[:200]
    u.external_id, u.active, u.updated = external_id, bool(active), now()
    s.flush()
    if created:
        _changed(s, by, "user_created", user_name, active=u.active)
    elif was_active != u.active:
        _changed(s, by, "user_activated" if u.active else "user_deactivated", user_name)
    else:
        _changed(s, by, "user_updated", user_name)
    return u


def delete_user(s: Session, u: DirectoryUser, by: str = "scim") -> None:
    s.execute(delete(DirectoryMember).where(DirectoryMember.user_id == u.id))
    _changed(s, by, "user_deleted", u.user_name)
    s.delete(u)


def save_group(s: Session, g: DirectoryGroup | None, *, display_name: str, external_id: str | None = None,
               members: list[str] | None = None, by: str = "scim") -> DirectoryGroup:
    name = (display_name or "").strip()
    if not name or len(name) > 200:
        raise DirectoryError("displayName is required (at most 200 characters)")
    clash = s.scalars(select(DirectoryGroup).where(DirectoryGroup.display_name == name)).first()
    if clash is not None and (g is None or clash.id != g.id):
        raise DirectoryError(f"A group named '{name}' already exists")
    created = g is None
    if g is None:
        g = DirectoryGroup(id=uuid.uuid4().hex)
        s.add(g)
    g.display_name, g.external_id, g.updated = name, external_id, now()
    s.flush()
    if members is not None:
        set_members(s, g, members)
    _changed(s, by, "group_created" if created else "group_updated", name)
    return g


def set_members(s: Session, g: DirectoryGroup, user_ids: list[str]) -> None:
    known = set(s.scalars(select(DirectoryUser.id).where(DirectoryUser.id.in_(user_ids)))) if user_ids else set()
    unknown = [u for u in user_ids if u not in known]
    if unknown:
        raise DirectoryError(f"No user with id '{unknown[0]}'")
    s.execute(delete(DirectoryMember).where(DirectoryMember.group_id == g.id))
    for uid in dict.fromkeys(user_ids):
        s.add(DirectoryMember(group_id=g.id, user_id=uid))
    s.flush()


def add_members(s: Session, g: DirectoryGroup, user_ids: list[str]) -> None:
    have = set(s.scalars(select(DirectoryMember.user_id).where(DirectoryMember.group_id == g.id)))
    set_members(s, g, sorted(have | set(user_ids)))


def remove_members(s: Session, g: DirectoryGroup, user_ids: list[str]) -> None:
    have = set(s.scalars(select(DirectoryMember.user_id).where(DirectoryMember.group_id == g.id)))
    set_members(s, g, sorted(have - set(user_ids)))


def delete_group(s: Session, g: DirectoryGroup, by: str = "scim") -> None:
    s.execute(delete(DirectoryMember).where(DirectoryMember.group_id == g.id))
    _changed(s, by, "group_deleted", g.display_name)
    s.delete(g)


def groups_of(s: Session, user_id: str) -> list[DirectoryGroup]:
    ids = list(s.scalars(select(DirectoryMember.group_id).where(DirectoryMember.user_id == user_id)))
    return list(s.scalars(select(DirectoryGroup).where(DirectoryGroup.id.in_(ids)).order_by(DirectoryGroup.display_name))) if ids else []


def members_of(s: Session, group_id: str) -> list[DirectoryUser]:
    ids = list(s.scalars(select(DirectoryMember.user_id).where(DirectoryMember.group_id == group_id)))
    return list(s.scalars(select(DirectoryUser).where(DirectoryUser.id.in_(ids)).order_by(DirectoryUser.user_name))) if ids else []


def overview(s: Session) -> dict:
    """The directory as the client's administrators see it: each person, their groups, and what they hold here."""
    rt = runtime(tenant_of(s))
    found, skipped = people(s, rt.policy)
    why = {x["user"]: x["reason"] for x in skipped}
    rows = []
    for u in s.scalars(select(DirectoryUser).order_by(DirectoryUser.user_name)):
        p = rt.policy.people.get(u.user_name)
        if not u.active:
            status, roles = "deactivated", []
        elif p is not None and p.source != DIRECTORY:
            status, roles = "named in the policy", list(p.roles)
        elif u.user_name in found:
            status, roles = "active", list(found[u.user_name].roles)
        else:
            status, roles = why.get(u.user_name, "no role"), []
        rows.append({"id": u.id, "user_name": u.user_name, "name": u.display_name, "email": u.email, "active": u.active,
                     "groups": [g.display_name for g in groups_of(s, u.id)], "roles": roles, "status": status,
                     "updated": u.updated.isoformat()})
    return {"people": rows, "groups": {g: {"roles": list(x.roles), "queues": list(x.queues)} for g, x in rt.policy.groups.items()},
            "sign_in": rt.policy.sign_in.describe(), "tokens": tokens(s)}


def stamp(at: datetime | None) -> str | None:
    return at.isoformat(timespec="seconds") + "Z" if at else None
