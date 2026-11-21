"""What the app shell needs to shape itself around one person: the desks they work and the counts on their menu.

Nothing here names a role. A person works a desk (a case type's domain) when one of their queues appears in
that case type's workflow, when one of their roles is limited to that case type, or when they hold a permission
that only makes sense on that desk. The interface uses this to show each person the menu that fits their job.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .domain.access import Persona
from .domain.models import Alert, Case

# Permissions that put a person on a desk even when no queue of theirs routes there (deciders and list owners).
DESK_PERMISSIONS: dict[str, frozenset[str]] = {
    "Sanctions": frozenset({"sanctions_decisions", "manage_lists", "info_sharing"}),
    "KYC": frozenset({"approve_risk"}),
}


def desks(p: Persona, case_types: dict[str, dict]) -> list[str]:
    """The domains this person works, in a stable order."""
    mine = set(p.queues)
    out: set[str] = set()
    for name, ct in case_types.items():
        domain = ct.get("domain")
        if not domain:
            continue
        queues = {st.get("queue") for st in ct.get("states", {}).values()} - {None}
        if (p.can("work") and mine & queues) or any(v.case_types and name in v.case_types for v in p.views or ()):
            out.add(domain)
    out |= {d for d, perms in DESK_PERMISSIONS.items() if any(p.can(x) for x in perms)}
    return sorted(out)


def badges(s: Session, p: Persona) -> dict[str, int]:
    """Small counts for the menu. Counted through the person's own session, so they only count what they may see."""
    open_case = Case.closed.is_(None)
    count = lambda *where: s.scalar(select(func.count()).select_from(Case).where(open_case, *where)) or 0  # noqa: E731
    return {
        "my_open": count(Case.owner == p.id),
        "queue_unassigned": count(Case.owner.is_(None), Case.queue.in_(p.queues)) if p.queues else 0,
        "alerts_waiting": s.scalar(select(func.count()).select_from(Alert).where(Alert.status == "hibernated")) or 0,
    }
