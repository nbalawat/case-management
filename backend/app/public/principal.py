"""Who is calling the public API.

Two kinds of caller, never mixed:

  app     an API client using a token from the client-credentials grant. It acts under roles from its client's
          access policy, chosen by that client's administrator, and sees what a person with those roles sees.
  person  someone signed in through their organisation's identity provider, calling through an application with
          their own token. They are exactly who they are on the web interface: same roles, same cases.

The client (tenant) always follows from the credential. A header can never move a caller to another client.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from ..domain.access import RESTRICT, RESTRICTED_VIEW, AccessPolicy, Persona, Sees
from ..tenants.registry import TenantRuntime
from . import scopes as scope_catalogue

APP_ACTOR = "app:"
# Restricted matters (staff, senior public figures, law-enforcement requests) never leave through an application,
# whatever its roles say. A cleared person still sees them with their own token.
NEVER_FOR_APPS = frozenset({RESTRICTED_VIEW, RESTRICT})


@dataclass(frozen=True)
class Principal:
    kind: str  # app | person
    runtime: TenantRuntime
    persona: Persona
    scopes: frozenset[str]
    client_id: str | None = None  # the API client, for apps
    token_id: str | None = None
    rate_limit: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def tenant_id(self) -> str:
        return self.runtime.id

    @property
    def caller(self) -> str:
        return self.persona.id

    @property
    def is_person(self) -> bool:
        return self.kind == "person"


class PrincipalError(ValueError):
    pass


def app_persona(policy: AccessPolicy, client_id: str, name: str, roles: list[str], queues: list[str]) -> Persona:
    """The persona an API client acts as: the union of its roles, exactly as for a person holding them."""
    if not roles:
        raise PrincipalError("An API client needs at least one role")
    missing = [r for r in roles if r not in policy.roles]
    if missing:
        raise PrincipalError(f"Unknown role(s) in this client's access policy: {', '.join(missing)}")
    held = [policy.roles[r] for r in roles]
    if len(held) > 1 and any(r.read_only for r in held):
        raise PrincipalError("A read-only role cannot be combined with another role")
    granted = frozenset().union(*(r.permissions for r in held)) - NEVER_FOR_APPS
    forbidden = sorted(frozenset().union(*(r.permissions for r in held)) & scope_catalogue.FORBIDDEN_PERMISSIONS)
    if forbidden:
        raise PrincipalError(f"An API client cannot hold a role that grants {', '.join(forbidden)}: applications never manage access")
    bad = [q for q in queues if q not in policy.queues]
    if bad:
        raise PrincipalError(f"Unknown queue(s): {', '.join(bad)}")
    views: tuple[Sees, ...] | None = None if any(r.sees is None for r in held) else tuple(dict.fromkeys(r.sees for r in held))
    return Persona(APP_ACTOR + client_id, name, held[0].id, f"API client ({held[0].title})", tuple(queues),
                   "An application calling the public API", granted, held[0].read_only, tuple(roles), views)


def ip_allowed(ip: str, blocks: list[str]) -> bool:
    if not blocks:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(b, strict=False) for b in blocks)


def check_blocks(blocks: list[str]) -> list[str]:
    out = []
    for b in blocks:
        try:
            out.append(str(ipaddress.ip_network(str(b).strip(), strict=False)))
        except ValueError:
            raise PrincipalError(f"Not an IP address or CIDR block: {b!r}") from None
    return out
