"""The scope catalogue.

A scope says which part of the API a token may use. It never grants anything on its own: every call also needs
what the access policy requires of a person doing the same thing (the `permission` below, or the workflow's own
rules for case transitions), and only sees the cases the caller's roles see. Scopes only ever narrow.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    name: str
    description: str
    permission: str | None = None  # what the caller's roles must also hold, when the scope writes something
    people_only: bool = False  # decisions the workflow keeps for people: an API client alone cannot hold this


CATALOGUE: dict[str, Scope] = {s.name: s for s in (
    Scope("cases:read", "Read cases, their history, alerts, transactions and tasks"),
    Scope("cases:write", "Work cases: move them through the workflow, complete tasks, write narratives, raise requests", "work", people_only=True),
    Scope("cases:assign", "Assign cases to members of their queue", "reassign"),
    Scope("alerts:read", "Read alerts"),
    Scope("alerts:write", "Send alerts from a monitoring or screening engine; the hub groups them into cases"),
    Scope("customers:read", "Read customers, their accounts, KYC profile and network"),
    Scope("transactions:read", "Read transactions"),
    Scope("filings:read", "Read regulatory filings and their documents (also needs submit_filings or view_team)"),
    Scope("filings:write", "Send filings and record the regulator's answer", "submit_filings", people_only=True),
    Scope("screening:read", "Read screening lists and screen names against them"),
    Scope("workflows:read", "Read case types: states, transitions, tasks and clocks"),
    Scope("ask", "Ask questions in plain language across everything the caller can see"),
    Scope("agents:run", "Ask the investigation agent to brief a case", "work"),
    Scope("data:read", "Read data sources and pipeline runs"),
    Scope("data:write", "Run data pipelines", "load_data"),
    Scope("events:read", "Read the event feed: every change the caller can see, in order"),
    Scope("webhooks:manage", "Register, change and remove webhook endpoints"),
    Scope("metrics:read", "Read operational metrics"),
)}

# Never held by an API client: an application must not be able to change who may do what.
FORBIDDEN_PERMISSIONS = frozenset({"manage_access", "certify_access"})


class ScopeError(ValueError):
    pass


def check(names: list[str] | set[str]) -> list[str]:
    unknown = sorted(set(names) - set(CATALOGUE))
    if unknown:
        raise ScopeError(f"Unknown scope(s): {', '.join(unknown)}")
    return sorted(set(names))


def for_apps() -> list[str]:
    return sorted(n for n, s in CATALOGUE.items() if not s.people_only)


def describe() -> list[dict]:
    return [{"name": s.name, "description": s.description, "needs_permission": s.permission, "people_only": s.people_only}
            for s in CATALOGUE.values()]
