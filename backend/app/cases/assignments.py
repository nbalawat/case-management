"""Who works a case: claiming from a queue, releasing, and reassignment by a lead."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..domain.models import Case
from ..domain.personas import Persona, policy_of
from .service import TransitionError, record
from ..visibility import system_work


def can_claim(case: Case, persona: Persona) -> bool:
    return case.closed is None and persona.can("work") and case.queue in persona.queues


@system_work
def claim(s: Session, case: Case, persona: Persona, ts: datetime | None = None) -> None:
    if not can_claim(case, persona):
        raise TransitionError(f"{persona.name} is not a member of the queue this case is in")
    if case.owner == persona.id:
        return
    previous, case.owner = case.owner, persona.id
    record(s, case.id, persona.id, "human", "case.assigned", {"owner": persona.id, "previous": previous, "how": "claimed"}, ts=ts)


@system_work
def release(s: Session, case: Case, persona: Persona) -> None:
    if case.owner != persona.id and not persona.can("reassign"):
        raise TransitionError("Only the owner or a lead can release a case")
    previous, case.owner = case.owner, None
    record(s, case.id, persona.id, "human", "case.released", {"previous": previous, "queue": case.queue})


@system_work
def reassign(s: Session, case: Case, to: str, persona: Persona, reason: str = "") -> None:
    if not persona.can("reassign"):
        raise TransitionError("Reassignment needs a team lead")
    if case.closed is not None:
        raise TransitionError("The case is closed")
    policy = policy_of(case.tenant_id)
    target = policy.people.get(to)
    if target is None or target not in policy.queue_members(case.queue):
        raise TransitionError("The new owner must be a member of the case's queue")
    previous, case.owner = case.owner, target.id
    record(s, case.id, persona.id, "human", "case.assigned",
           {"owner": target.id, "previous": previous, "how": "reassigned", "reason": reason})
