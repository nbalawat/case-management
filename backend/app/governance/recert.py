"""Access recertification: a snapshot of who holds what, and a manager's decision on each person.

A review records decisions; it does not edit the access policy. What it asks to be changed stays outstanding until a
later policy shows the change, so the request and the change are two records made by two people.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.access import Persona
from ..domain.models import AccessReview, Case, CaseEvent, new_id, now
from ..domain.personas import policy_of
from ..tenancy import tenant_of
from ..visibility import system_work

CERTIFY = "certify_access"
DORMANT_DAYS = 30
DECISIONS = ("keep", "change", "remove")
# Pairs that put both sides of a control in one person's hands. Holding both is not forbidden; it is for the reviewer to see and accept.
CONFLICTS = [("manage_access", "certify_access", "Sets access and certifies it"),
             ("manage_access", "work", "Sets access and works cases"),
             ("publish_workflow", "work", "Changes workflows and works cases under them"),
             ("tune_detection", "approve_tuning", "Proposes detection changes and can approve them (never their own)")]


class RecertError(ValueError):
    pass


def _get(s: Session, review_id: str) -> AccessReview:
    r = s.get(AccessReview, review_id)
    if r is None:
        raise RecertError("Review not found")
    return r


def flags(p: Persona, last_active: datetime | None, at: datetime) -> list[str]:
    out = [text for a, b, text in CONFLICTS if a in p.permissions and b in p.permissions and not p.read_only]
    if p.can("work") and (last_active is None or at - last_active > timedelta(days=DORMANT_DAYS)):
        out.append(f"No case activity in {DORMANT_DAYS} days" if last_active else "No case activity on record")
    return out


@system_work
def snapshot(s: Session, at: datetime | None = None) -> list[dict]:
    """Everyone at the client with what they hold today and when they last did anything on a case."""
    at = at or now()
    last = dict(s.execute(select(CaseEvent.actor, func.max(CaseEvent.ts)).where(CaseEvent.actor_kind == "human", CaseEvent.ts <= at).group_by(CaseEvent.actor)).all())
    open_cases = dict(s.execute(select(Case.owner, func.count()).where(Case.closed.is_(None), Case.owner.is_not(None)).group_by(Case.owner)).all())
    return [{"person": p.id, "name": p.name, "title": p.title, "roles": list(p.roles or (p.role,)), "permissions": sorted(p.permissions), "queues": list(p.queues),
             "read_only": p.read_only, "last_active": last[p.id].isoformat() if p.id in last else None, "open_cases": open_cases.get(p.id, 0),
             "flags": flags(p, last.get(p.id), at), "decision": None, "note": "", "decided_by": None, "decided_at": None}
            for p in policy_of(tenant_of(s)).people.values()]


def start(s: Session, persona: Persona, name: str, due_days: int = 14, at: datetime | None = None) -> AccessReview:
    if not persona.can(CERTIFY):
        raise RecertError(f"Starting a recertification needs the '{CERTIFY}' permission")
    if len(name.strip()) < 5:
        raise RecertError("Give the review a name, such as the quarter it covers")
    if isinstance(due_days, bool) or not isinstance(due_days, int) or not 1 <= due_days <= 90:
        raise RecertError("Days to complete must be between 1 and 90")
    if s.scalars(select(AccessReview.id).where(AccessReview.status == "open")).first():
        raise RecertError("A recertification is already open. Close it before starting another")
    at = at or now()
    r = AccessReview(id=new_id("acr"), name=name.strip()[:160], policy_version=policy_of(tenant_of(s)).version, started_by=persona.id, started=at,
                     due=at + timedelta(days=due_days), items=snapshot(s, at))
    s.add(r)
    s.flush()
    return r


def decide(s: Session, review_id: str, person: str, decision: str, note: str, persona: Persona, at: datetime | None = None) -> AccessReview:
    r = _get(s, review_id)
    if not persona.can(CERTIFY):
        raise RecertError(f"Deciding a recertification needs the '{CERTIFY}' permission")
    if r.status != "open":
        raise RecertError("This review is closed")
    if decision not in DECISIONS:
        raise RecertError(f"A decision is one of: {', '.join(DECISIONS)}")
    item = next((i for i in r.items if i["person"] == person), None)
    if item is None:
        raise RecertError("Person not found in this review")
    if person == persona.id:
        raise RecertError("Someone else must certify your own access")
    note = note.strip()
    if decision != "keep" and len(note) < 10:
        raise RecertError("Say what should change, so that whoever maintains the access policy can do it")
    if decision == "keep" and item["flags"] and len(note) < 10:
        raise RecertError("This person is flagged. Say why the access is still right")
    r.items = [i | {"decision": decision, "note": note[:500], "decided_by": persona.id, "decided_at": (at or now()).isoformat()} if i["person"] == person else i for i in r.items]
    s.flush()
    return r


def close(s: Session, review_id: str, persona: Persona, at: datetime | None = None) -> AccessReview:
    r = _get(s, review_id)
    if not persona.can(CERTIFY):
        raise RecertError(f"Closing a recertification needs the '{CERTIFY}' permission")
    if r.status != "open":
        raise RecertError("This review is already closed")
    waiting = [i["name"] for i in r.items if not i["decision"]]
    if waiting:
        raise RecertError(f"{len(waiting)} people are not yet decided: {', '.join(waiting[:5])}{'…' if len(waiting) > 5 else ''}")
    r.status, r.closed_by, r.closed_at = "closed", persona.id, at or now()
    s.flush()
    return r


def _done(item: dict, people: dict[str, Persona]) -> bool:
    """Whether the change a review asked for can be seen in the access policy now in force."""
    p = people.get(item["person"])
    if item["decision"] == "remove":
        return p is None
    return p is None or sorted(p.permissions) != item["permissions"] or list(p.queues) != item["queues"] or list(p.roles or (p.role,)) != item["roles"]


def row(s: Session, r: AccessReview) -> dict:
    people = policy_of(tenant_of(s)).people
    items = [i | {"done": _done(i, people) if i["decision"] in ("change", "remove") else None} for i in r.items]
    decided = sum(bool(i["decision"]) for i in items)
    asked = [i for i in items if i["decision"] in ("change", "remove")]
    return {"id": r.id, "name": r.name, "status": r.status, "policy_version": r.policy_version, "started_by": r.started_by, "started": r.started.isoformat(),
            "due": r.due.isoformat(), "overdue": r.status == "open" and r.due < now(), "closed_by": r.closed_by, "closed_at": r.closed_at.isoformat() if r.closed_at else None,
            "items": items, "people": len(items), "decided": decided, "flagged": sum(bool(i["flags"]) for i in items),
            "changes": len(asked), "outstanding": sum(not i["done"] for i in asked)}


def view(s: Session, persona: Persona) -> dict:
    reviews = [row(s, r) for r in s.scalars(select(AccessReview).order_by(AccessReview.started.desc()))]
    return {"reviews": reviews, "can_certify": persona.can(CERTIFY) and not persona.read_only, "me": persona.id, "dormant_days": DORMANT_DAYS,
            "current": None if any(r["status"] == "open" for r in reviews) else {"flagged": sum(bool(i["flags"]) for i in snapshot(s)), "people": len(snapshot(s))}}
