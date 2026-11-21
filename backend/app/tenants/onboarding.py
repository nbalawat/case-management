"""Bringing a client onto the platform, and rebuilding every client's configuration at start-up.

Onboarding is one transaction: the client row, its first access policy and its runtime entry are created
together or not at all. Everything is validated first, so a client can never exist in a state where a
workflow names a role or a queue its policy does not have.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases import case_types as registry
from ..domain.access import AccessPolicy, AccessPolicyError, parse_policy
from ..domain.models import AccessPolicyVersion, Tenant, WorkflowVersion
from ..tenancy import TenantError, is_platform_session
from . import provider
from .registry import TenantRuntime, install, reset, runtime
from .templates import Template, templates

SLUG = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
DEPLOYMENTS = {"pooled": "Shared platform", "dedicated": "Dedicated stamp", "client-cloud": "Client's own cloud"}
STATUSES = ("live", "suspended")
RESERVED = {"platform", "admin", "api", "system"}


class OnboardingError(ValueError):
    pass


def _platform(s: Session) -> None:
    if not is_platform_session(s):
        raise TenantError("Client administration needs a platform session")


def validate_configuration(policy: AccessPolicy, case_type_names: list[str], origin: str = "client") -> list[dict]:
    """Every chosen case type must exist and be workable under this policy. Returns the definitions."""
    library = registry.library()
    if not case_type_names:
        raise OnboardingError(f"{origin}: choose at least one case type")
    unknown = [n for n in case_type_names if n not in library]
    if unknown:
        raise OnboardingError(f"{origin}: unknown case type(s): {', '.join(unknown)}")
    if not any(p.can("manage_access") for p in policy.people.values()):
        raise OnboardingError(f"{origin}: nobody holds the 'manage_access' permission, so the policy could never be changed")
    for name in case_type_names:
        try:
            registry.check_policy(library[name], policy, library[name]["title"])
        except registry.CaseTypeError as e:
            raise OnboardingError(str(e)) from e
    return [library[n] for n in case_type_names]


def _template(template_id: str) -> Template:
    try:
        return templates()[template_id]
    except KeyError:
        raise OnboardingError(f"Unknown template '{template_id}'") from None


def preview(template_id: str, case_types: list[str] | None = None, access_source: str | None = None) -> dict:
    """What onboarding from this template would create. Changes nothing."""
    tpl = _template(template_id)
    names = list(case_types) if case_types is not None else list(tpl.case_types)
    try:
        policy = parse_policy(_yaml(access_source or tpl.access_source), "access policy")
    except AccessPolicyError as e:
        raise OnboardingError(str(e)) from e
    definitions = validate_configuration(policy, names, tpl.title)
    return {"template": tpl.id, "title": tpl.title, "summary": tpl.summary, "policy": policy, "definitions": definitions,
            "case_types": names, "settings": tpl.settings}


def _yaml(source: str):
    import yaml

    try:
        return yaml.safe_load(source)
    except yaml.YAMLError as e:
        raise OnboardingError(f"access policy: not valid YAML ({str(e).splitlines()[0]})") from e


def onboard(s: Session, *, tenant_id: str, name: str, template: str, region: str = "us-east", deployment: str = "pooled",
            case_types: list[str] | None = None, access_source: str | None = None, by: str = "platform") -> TenantRuntime:
    _platform(s)
    if not SLUG.fullmatch(tenant_id or "") or tenant_id in RESERVED:
        raise OnboardingError("Client id must be 3 to 31 characters: lower-case letters, digits and hyphens, starting with a letter")
    if not (name or "").strip():
        raise OnboardingError("Client name is required")
    if deployment not in DEPLOYMENTS:
        raise OnboardingError(f"Deployment must be one of: {', '.join(DEPLOYMENTS)}")
    if s.get(Tenant, tenant_id) is not None:
        raise OnboardingError(f"A client with id '{tenant_id}' already exists")
    plan = preview(template, case_types, access_source)
    tpl = _template(template)
    row = Tenant(id=tenant_id, name=name.strip(), template=template, region=region, deployment=deployment, status="live",
                 case_types=plan["case_types"], settings=dict(plan["settings"]) | {"storage": [default_storage(tenant_id)]})
    s.add(row)
    s.add(AccessPolicyVersion(tenant_id=tenant_id, version=plan["policy"].version, source=access_source or tpl.access_source,
                              published_by=by, note=f"Created from the '{tpl.title}' template"))
    s.flush()
    return install(TenantRuntime(tenant_id, row.name, plan["policy"], list(row.case_types), row.status, dict(row.settings)))


def default_storage(tenant_id: str) -> dict:
    """Every client starts with one landing location of its own. In the cloud this is its bucket or container."""
    from ..config import settings

    return {"id": "landing", "label": "Landing zone", "url": f"file://{(settings.lake_dir / tenant_id).resolve()}/", "options": {},
            "provider": "Local directory"}


def set_filing(s: Session, tenant_id: str, profile: dict) -> TenantRuntime:
    """Record how a client reports to its regulator: the regime, who the filing institution is, and how reports leave."""
    from ..filing.formats import SUSPICION_KIND
    from ..filing.transmit import CHANNELS

    _platform(s)
    row = s.get(Tenant, tenant_id)
    if row is None:
        raise OnboardingError(f"Unknown client '{tenant_id}'")
    regime, channel = str(profile.get("regime") or "fincen"), str(profile.get("channel") or "outbox")
    if regime not in SUSPICION_KIND:
        raise OnboardingError(f"Unknown regime '{regime}'. Known: {', '.join(sorted(SUSPICION_KIND))}")
    if channel not in CHANNELS:
        raise OnboardingError(f"Unknown channel '{channel}'. Known: {', '.join(sorted(CHANNELS))}")
    institution = profile.get("institution") or {}
    if not isinstance(institution, dict) or any(not isinstance(v, str) for v in institution.values()):
        raise OnboardingError("The institution's details must be plain text fields")
    cleaned = {"regime": regime, "channel": channel, "institution": {k: v.strip()[:200] for k, v in institution.items()}}
    row.settings = dict(row.settings or {}) | {"filing": cleaned}
    s.flush()
    rt = runtime(tenant_id)
    rt.settings = dict(row.settings)
    return rt


def set_identity(s: Session, tenant_id: str, provider: dict | None) -> TenantRuntime:
    """Register the identity provider a client's people sign in through, or remove it. One provider speaks for one client."""
    from ..auth.identity import IdentityError, check_provider

    _platform(s)
    row = s.get(Tenant, tenant_id)
    if row is None:
        raise OnboardingError(f"Unknown client '{tenant_id}'")
    cleaned = None
    if provider is not None:
        try:
            cleaned = check_provider(provider)
        except IdentityError as e:
            raise OnboardingError(str(e)) from None
        for other in s.scalars(select(Tenant).where(Tenant.id != tenant_id)):
            if ((other.settings or {}).get("identity") or {}).get("issuer") == cleaned["issuer"]:
                raise OnboardingError("Another client already signs in through that issuer. Each client needs its own realm or tenant at the provider.")
    row.settings = {k: v for k, v in dict(row.settings or {}).items() if k != "identity"} | ({"identity": cleaned} if cleaned else {})
    s.flush()
    rt = runtime(tenant_id)
    rt.settings = dict(row.settings)
    return rt


def set_storage(s: Session, tenant_id: str, roots: list[dict]) -> TenantRuntime:
    """Replace the storage locations a client's pipelines may read. A location in use by a data source cannot be removed."""
    from ..ingest.locations import check_root
    from ..storage import StorageError

    _platform(s)
    row = s.get(Tenant, tenant_id)
    if row is None:
        raise OnboardingError(f"Unknown client '{tenant_id}'")
    try:
        cleaned = [check_root(tenant_id, r) for r in roots]
    except StorageError as e:
        raise OnboardingError(str(e)) from None
    if len({r["id"] for r in cleaned}) != len(cleaned):
        raise OnboardingError("Location ids must be unique")
    from ..domain.models import DataSource

    used = set(s.scalars(select(DataSource.root).where(DataSource.tenant_id == tenant_id).distinct()))
    missing = sorted(used - {r["id"] for r in cleaned})
    if missing:
        raise OnboardingError(f"Data sources still read from: {', '.join(missing)}")
    row.settings = dict(row.settings or {}) | {"storage": cleaned}
    s.flush()
    rt = runtime(tenant_id)
    rt.settings = dict(row.settings)
    return rt


def hydrate(s: Session) -> list[TenantRuntime]:
    """Rebuild every client's runtime from the database: the policy in force and its published workflow versions."""
    _platform(s)
    reset()
    out = []
    for row in s.scalars(select(Tenant).order_by(Tenant.created, Tenant.id)):
        source = s.scalars(select(AccessPolicyVersion).where(AccessPolicyVersion.tenant_id == row.id)
                           .order_by(AccessPolicyVersion.id.desc())).first()
        rt = install(TenantRuntime(row.id, row.name, parse_policy(_yaml(source.source), f"{row.id} access policy"),
                                   list(row.case_types), row.status, dict(row.settings or {})))
        for wv in s.scalars(select(WorkflowVersion).where(WorkflowVersion.tenant_id == row.id).order_by(WorkflowVersion.id)):
            registry.register_published(row.id, registry.parse(wv.source, f"{wv.case_type} {wv.version}"))
        provider.expire(s, row.id)
        provider.refresh(s, rt)
        _directory(s, rt)
        out.append(rt)
    return out


def _directory(s: Session, rt: TenantRuntime) -> None:
    """Add the people the client's identity provider provisioned, read under that client's own session."""
    from ..db import session_scope
    from ..directory import service as directory

    with session_scope(rt.id) as ts:
        directory.refresh(ts, rt)


def set_status(s: Session, tenant_id: str, status: str) -> TenantRuntime:
    _platform(s)
    if status not in STATUSES:
        raise OnboardingError(f"Status must be one of: {', '.join(STATUSES)}")
    row = s.get(Tenant, tenant_id)
    if row is None:
        raise OnboardingError(f"Unknown client '{tenant_id}'")
    row.status = status
    s.flush()
    rt = runtime(tenant_id)
    rt.status = status
    return rt


def set_case_types(s: Session, tenant_id: str, names: list[str], in_use: set[str] = frozenset()) -> TenantRuntime:
    """Change which case types a client uses. A type with open cases cannot be switched off."""
    _platform(s)
    row = s.get(Tenant, tenant_id)
    if row is None:
        raise OnboardingError(f"Unknown client '{tenant_id}'")
    rt = runtime(tenant_id)
    validate_configuration(rt.policy, names, rt.name)
    blocked = sorted(in_use - set(names))
    if blocked:
        raise OnboardingError(f"Open cases still use: {', '.join(blocked)}. Close them before switching the case type off")
    row.case_types = list(names)
    s.flush()
    rt.case_types = list(names)
    return rt
