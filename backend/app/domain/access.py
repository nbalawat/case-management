"""Access policy loaded from configuration: permissions, queues, roles and the people who hold them.

The policy file is the single source of truth. Code asks the policy; it never names a role.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class AccessPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Role:
    id: str
    title: str
    permissions: frozenset[str]
    read_only: bool = False


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    title: str
    queues: tuple[str, ...] = ()
    about: str = ""
    permissions: frozenset[str] = frozenset()
    read_only: bool = False
    roles: tuple[str, ...] = ()  # every role held; `role` is the first and is the one shown

    def holds(self, roles) -> bool:
        return any(r in roles for r in (self.roles or (self.role,)))

    def can(self, permission: str) -> bool:
        """Read-only roles may look (view_*) but hold no permission that changes anything."""
        return permission in self.permissions and (not self.read_only or permission.startswith("view_"))


@dataclass(frozen=True)
class AccessPolicy:
    name: str
    version: str
    permissions: dict[str, str]
    queues: dict[str, str]
    roles: dict[str, Role]
    people: dict[str, Persona]
    default_person: str

    def person(self, pid: str | None) -> Persona:
        return self.people.get(pid or "", self.people[self.default_person])

    def queue_members(self, queue: str | None) -> list[Persona]:
        return [p for p in self.people.values() if queue in p.queues]

    def holders(self, role: str) -> list[Persona]:
        return [p for p in self.people.values() if p.holds((role,))]


def parse_policy(raw: dict, origin: str = "access policy") -> AccessPolicy:
    def fail(msg: str):
        raise AccessPolicyError(f"{origin}: {msg}")

    if not isinstance(raw, dict):
        fail("must be a YAML mapping")
    for key in ("permissions", "queues", "roles", "people"):
        if not raw.get(key):
            fail(f"missing '{key}'")
    permissions, queues = dict(raw["permissions"]), dict(raw["queues"])
    roles: dict[str, Role] = {}
    for rid, r in raw["roles"].items():
        unknown = [p for p in r.get("permissions", []) if p not in permissions]
        if unknown:
            fail(f"role '{rid}' grants unknown permission(s): {', '.join(unknown)}")
        read_only = bool(r.get("read_only", False))
        if read_only and any(not p.startswith("view_") for p in r.get("permissions", [])):
            fail(f"role '{rid}' is read-only but is granted a permission that changes data")
        roles[rid] = Role(rid, r.get("title", rid), frozenset(r.get("permissions", [])), read_only)
    people: dict[str, Persona] = {}
    for item in raw["people"]:
        pid = item.get("id")
        if not pid or pid in people:
            fail(f"person id missing or repeated: {pid!r}")
        names = item.get("roles") or ([item["role"]] if item.get("role") else [])
        if not names or not isinstance(names, list):
            fail(f"person '{pid}' needs a role")
        missing = [n for n in names if n not in roles]
        if missing:
            fail(f"person '{pid}' has unknown role '{missing[0]}'")
        held = [roles[n] for n in names]
        if len(held) > 1 and any(r.read_only for r in held):
            fail(f"person '{pid}' combines a read-only role with another role")
        role = held[0]
        granted = frozenset().union(*(r.permissions for r in held))
        bad = [q for q in item.get("queues", []) if q not in queues]
        if bad:
            fail(f"person '{pid}' is in unknown queue(s): {', '.join(bad)}")
        if item.get("queues") and "work" not in granted:
            fail(f"person '{pid}' is in a queue but holds no role that can work cases")
        people[pid] = Persona(pid, item.get("name", pid), role.id, item.get("title", role.title), tuple(item.get("queues", [])),
                              item.get("about", ""), granted, role.read_only, tuple(names))
    default = raw.get("default_person") or next(iter(people))
    if default not in people:
        fail(f"default_person '{default}' is not one of the people")
    return AccessPolicy(str(raw.get("policy", "default")), str(raw.get("version", "1")), permissions, queues, roles, people, default)


def load_policy(path: Path) -> AccessPolicy:
    return parse_policy(yaml.safe_load(path.read_text()), path.name)
