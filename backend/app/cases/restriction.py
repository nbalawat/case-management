"""Restricted cases: matters only named people may see, such as an investigation into a member of staff or a senior public figure.

Restricting hides the case, and every case opened from it, from everyone without the view_restricted permission,
whatever their role would otherwise show them. The owner is released if they can no longer see it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.access import RESTRICT, RESTRICTED_VIEW
from ..domain.models import Case
from ..domain.personas import Persona, policy_of
from ..visibility import system_work
from .service import TransitionError, record


def _family(s: Session, case: Case) -> list[Case]:
    out, frontier = [case], [case.id]
    while frontier:
        kids = list(s.scalars(select(Case).where(Case.parent_id.in_(frontier))))
        out += kids
        frontier = [k.id for k in kids]
    return out


@system_work
def set_restricted(s: Session, case: Case, persona: Persona, restricted: bool, reason: str = "") -> list[Case]:
    if not persona.can(RESTRICT):
        raise TransitionError(f"{persona.title} cannot restrict cases")
    reason = reason.strip()
    if not reason:
        raise TransitionError("Say why, for the record")
    people = policy_of(case.tenant_id).people
    changed = []
    for c in _family(s, case):
        if c.restricted == restricted:
            continue
        c.restricted, c.restricted_reason = restricted, reason if restricted else ""
        record(s, c.id, persona.id, "human", "case.restricted" if restricted else "case.unrestricted",
               {"reason": reason} | ({"because_of": case.id} if c.id != case.id else {}))
        owner = people.get(c.owner or "")
        if restricted and c.owner and (owner is None or not owner.can(RESTRICTED_VIEW)):
            record(s, c.id, persona.id, "human", "case.released", {"previous": c.owner, "queue": c.queue, "why": "restricted"})
            c.owner = None
        changed.append(c)
    return changed
