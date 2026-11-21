"""The active access policy, exposed in the shapes the rest of the code uses.

Everything here is read from the policy file (see access.py and backend/access/default.yaml).
"""
from __future__ import annotations

from ..config import settings
from .access import AccessPolicy, Persona, load_policy

POLICY: AccessPolicy = load_policy(settings.access_policy)
QUEUES: dict[str, str] = POLICY.queues
PERMISSIONS: dict[str, frozenset[str]] = {r.id: r.permissions for r in POLICY.roles.values()}
PERSONAS: dict[str, Persona] = POLICY.people


def get_persona(pid: str | None) -> Persona:
    return POLICY.person(pid)


def queue_members(queue: str | None) -> list[Persona]:
    return POLICY.queue_members(queue)


__all__ = ["PERMISSIONS", "PERSONAS", "POLICY", "QUEUES", "Persona", "get_persona", "queue_members"]
