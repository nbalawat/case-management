"""A client's access policy: check a draft, see its effect, publish it. Each client has its own, versioned.

Publishing is refused if it would leave any workflow the client runs without someone able to move a case,
or leave nobody able to change the policy again. Open cases whose owner loses their place go back to the queue.
"""
from __future__ import annotations

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases import case_types as registry
from ..cases.service import record
from ..domain.access import AccessPolicy, AccessPolicyError, Persona, parse_policy
from ..domain.models import AccessPolicyVersion, Case
from ..tenancy import tenant_of
from .registry import runtime
from ..visibility import system_work


def active(s: Session) -> AccessPolicyVersion:
    return s.scalars(select(AccessPolicyVersion).order_by(AccessPolicyVersion.id.desc())).first()


def history(s: Session) -> list[dict]:
    rows = list(s.scalars(select(AccessPolicyVersion).order_by(AccessPolicyVersion.id.desc())))
    return [{"version": r.version, "published_by": r.published_by, "published": r.published.isoformat(), "note": r.note,
             "active": i == 0} for i, r in enumerate(rows)]


def _definitions_in_use(s: Session, tenant_id: str) -> list[dict]:
    """Active definitions plus every older version an open case is still pinned to."""
    out = {(ct["case_type"], ct["version"]): ct for ct in registry.load_case_types(tenant_id).values()}
    pinned = s.execute(select(Case.case_type, Case.type_version).where(Case.closed.is_(None)).distinct())
    for case_type, version in pinned:
        ct = registry.get_definition(tenant_id, case_type, version)
        out[(ct["case_type"], ct["version"])] = ct
    return list(out.values())


def _displaced(s: Session, policy: AccessPolicy) -> list[Case]:
    """Open cases whose owner would no longer be a member of the queue the case is in."""
    members = {q: {p.id for p in policy.queue_members(q)} for q in policy.queues}
    return [c for c in s.scalars(select(Case).where(Case.closed.is_(None), Case.owner.is_not(None)))
            if c.owner not in members.get(c.queue or "", set())]


def _check_views(policy: AccessPolicy, case_types: dict[str, dict]) -> list[str]:
    """What people see must fit the work that reaches them. Returns warnings; raises on a policy that cannot work."""
    warnings = []
    for r in policy.roles.values():
        unknown = sorted((r.sees.case_types or set()) - set(case_types)) if r.sees else []
        if unknown:
            warnings.append(f"Role '{r.title}' names case type(s) this client does not run: {', '.join(unknown)}")
    arrives: dict[str, set[str]] = {}
    for ct in case_types.values():
        for st in ct["states"].values():
            if st.get("queue"):
                arrives.setdefault(st["queue"], set()).add(ct["case_type"])
    for q, types in arrives.items():
        for p in policy.queue_members(q):
            blind = sorted(t for t in types if p.views is not None and not any(
                v.scope != "own" and (v.case_types is None or t in v.case_types) for v in p.views))
            if blind:
                raise AccessPolicyError(f"draft: {p.name} is in the queue '{policy.queues[q]}' but could not see the "
                                        f"{', '.join(case_types[t]['title'] for t in blind)} cases that arrive there")
    if any("restrict_case" in r.permissions for r in policy.roles.values()):
        stuck = [policy.queues[q] for q in arrives if q in policy.queues and not any(p.can("view_restricted") for p in policy.queue_members(q))]
        if stuck:
            warnings.append(f"A restricted case waits to be moved on in: {', '.join(sorted(stuck))} (nobody there can see restricted cases)")
    return warnings


def diff(old: AccessPolicy, new: AccessPolicy) -> list[str]:
    out = []
    for rid, r in new.roles.items():
        if rid not in old.roles:
            out.append(f"Adds role '{r.title}'")
            continue
        gained, lost = sorted(r.permissions - old.roles[rid].permissions), sorted(old.roles[rid].permissions - r.permissions)
        if gained:
            out.append(f"'{r.title}' gains: {', '.join(g.replace('_', ' ') for g in gained)}")
        if lost:
            out.append(f"'{r.title}' loses: {', '.join(g.replace('_', ' ') for g in lost)}")
        if r.sees != old.roles[rid].sees:
            was, now_ = (x.sees.describe() if x.sees else "Every case" for x in (old.roles[rid], r))
            out.append(f"'{r.title}' now sees: {now_} (was: {was})")
        if r.read_only != old.roles[rid].read_only:
            out.append(f"'{r.title}' becomes {'read-only' if r.read_only else 'able to make changes'}")
    out += [f"Removes role '{r.title}'" for rid, r in old.roles.items() if rid not in new.roles]
    out += [f"Adds queue '{label}'" for q, label in new.queues.items() if q not in old.queues]
    out += [f"Removes queue '{label}'" for q, label in old.queues.items() if q not in new.queues]
    for pid, p in new.people.items():
        before = old.people.get(pid)
        if before is None:
            out.append(f"Adds {p.name} ({p.title})")
            continue
        if set(p.roles) != set(before.roles):
            out.append(f"{p.name}: roles change from {', '.join(before.roles)} to {', '.join(p.roles)}")
        joined, left = sorted(set(p.queues) - set(before.queues)), sorted(set(before.queues) - set(p.queues))
        if joined:
            out.append(f"{p.name} joins: {', '.join(new.queues[q] for q in joined)}")
        if left:
            out.append(f"{p.name} leaves: {', '.join(old.queues.get(q, q) for q in left)}")
    out += [f"Removes {p.name} ({p.title})" for pid, p in old.people.items() if pid not in new.people]
    return out or ["No change to roles, queues or people"]


@system_work
def check(s: Session, source: str) -> dict:
    """Validate a draft policy for this client and describe its effect. Changes nothing."""
    tenant = tenant_of(s)
    rt = runtime(tenant)
    try:
        raw = yaml.safe_load(source)
    except yaml.YAMLError as e:
        raise AccessPolicyError(f"draft: not valid YAML ({str(e).splitlines()[0]})") from e
    policy = parse_policy(raw, "draft")
    if registry.version_key(policy.version) <= registry.version_key(rt.policy.version):
        raise AccessPolicyError(f"draft: version must be higher than the active {rt.policy.version}")
    if not any(p.can("manage_access") for p in policy.people.values()):
        raise AccessPolicyError("draft: nobody would hold 'manage_access', so this policy could never be changed again")
    for ct in _definitions_in_use(s, tenant):
        try:
            registry.check_policy(ct, policy, f"{ct['title']} {ct['version']}")
        except registry.CaseTypeError as e:
            raise AccessPolicyError(f"draft: {e}") from e
    displaced = _displaced(s, policy)
    warnings = [f"{len(displaced)} open case(s) go back to their queue because the owner is no longer a member"] if displaced else []
    warnings += _check_views(policy, registry.load_case_types(tenant))
    return {"policy": policy, "changes": diff(rt.policy, policy), "warnings": warnings, "displaced": displaced}


@system_work
def publish(s: Session, source: str, persona: Persona, note: str = "") -> dict:
    if not persona.can("manage_access"):
        raise AccessPolicyError("Changing the access policy needs the 'manage access' permission")
    result = check(s, source)
    policy = result["policy"]
    s.add(AccessPolicyVersion(version=policy.version, source=source, published_by=persona.id, note=note))
    for c in result["displaced"]:
        record(s, c.id, "access-policy", "system", "case.handed_off",
               {"from_owner": c.owner, "queue": c.queue, "reason": f"access policy {policy.version}"})
        c.owner = None
    s.flush()
    runtime(tenant_of(s)).policy = policy
    return result
