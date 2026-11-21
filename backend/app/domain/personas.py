"""People, roles and queues of one client, read from that client's access policy."""
from __future__ import annotations

from ..tenants.registry import runtime
from .access import AccessPolicy, Persona


def policy_of(tenant_id: str) -> AccessPolicy:
    return runtime(tenant_id).policy


def get_persona(tenant_id: str, pid: str | None) -> Persona:
    return policy_of(tenant_id).person(pid)


def queue_members(tenant_id: str, queue: str | None) -> list[Persona]:
    return policy_of(tenant_id).queue_members(queue)


__all__ = ["Persona", "get_persona", "policy_of", "queue_members"]
