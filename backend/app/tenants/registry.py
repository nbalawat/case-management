"""Per-client runtime configuration, held in memory and rebuilt from the database at start-up.

Each client has its own access policy, its own choice of case types and its own published workflow versions.
Nothing here is shared between clients except the read-only case type library they start from.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.access import AccessPolicy


class UnknownTenant(LookupError):
    pass


@dataclass
class TenantRuntime:
    id: str
    name: str
    policy: AccessPolicy
    case_types: list[str]
    status: str = "live"
    settings: dict = field(default_factory=dict)
    # (case_type, version) -> definition, in publish order
    published: dict[tuple[str, str], dict] = field(default_factory=dict)


_RUNTIMES: dict[str, TenantRuntime] = {}


def install(rt: TenantRuntime) -> TenantRuntime:
    _RUNTIMES[rt.id] = rt
    return rt


def runtime(tenant_id: str | None) -> TenantRuntime:
    try:
        return _RUNTIMES[tenant_id or ""]
    except KeyError:
        raise UnknownTenant(f"Unknown client '{tenant_id}'") from None


def all_runtimes() -> list[TenantRuntime]:
    return list(_RUNTIMES.values())


def reset() -> None:
    _RUNTIMES.clear()


def swap(runtimes: dict[str, TenantRuntime]) -> dict[str, TenantRuntime]:
    """Replace the whole registry and return what was there. For tests that need a clean, restorable state."""
    global _RUNTIMES
    old, _RUNTIMES = _RUNTIMES, runtimes
    return old
