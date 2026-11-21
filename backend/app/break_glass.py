"""Break-glass access: open one case or customer you cannot otherwise see, for a stated reason, for a few hours.

The client's policy switches it on (its `break_glass` section) and says for how long; the `break_glass` permission says
who may use it. Opening is immediate, because the moment it is needed is usually urgent, and accountability comes
after: every opening is written to the client's access record and to the case's own history, and waits for someone
else, holding the certify_access permission, to judge it justified or not.

What it never does: open a restricted case unless the policy allows that, let a customer-facing person in (tipping-off
rules are not for breaking), or last beyond its hours. The visibility filter reads live grants directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain.access import BREAK_GLASS, Persona
from .domain.models import AccessEvent, AccessGrant, Case, CaseEvent, Party, new_id, now
from .domain.personas import policy_of
from .tenancy import tenant_of
from .visibility import can_see, can_see_party, system, viewer_of

KINDS = ("case", "customer")
REVIEW = "certify_access"
OUTCOMES = {"justified": "Justified", "not_justified": "Not justified"}
MIN_REASON = 20


class BreakGlassError(ValueError):
    pass


def _live(g: AccessGrant, at: datetime) -> bool:
    return g.closed is None and g.ends > at


def _event(s: Session, actor: str, org: str, kind: str, subject: str, **detail) -> None:
    s.add(AccessEvent(actor=actor, org=org, kind=kind, subject=subject, detail=detail))


def open_grant(s: Session, p: Persona, kind: str, target: str, reason: str, at: datetime | None = None) -> AccessGrant:
    at = at or now()
    policy = policy_of(tenant_of(s))
    terms = policy.break_glass
    if terms is None:
        raise BreakGlassError("This client does not use break-glass access")
    if not p.can(BREAK_GLASS) or p.customer_facing:
        raise BreakGlassError(f"{p.title} cannot break glass")
    if kind not in KINDS:
        raise BreakGlassError("Break glass opens a case or a customer")
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise BreakGlassError(f"Say why, in at least {MIN_REASON} characters. Someone will review it.")
    v = viewer_of(p, policy)
    with system(s):
        if kind == "case":
            c = s.scalars(select(Case).where(Case.id == target)).first()
            if c is None:
                raise BreakGlassError("No case with that id")
            if c.restricted and not (v.sees_restricted or terms.restricted):
                raise BreakGlassError("Restricted cases cannot be opened by breaking glass. Ask someone cleared for restricted matters.")
            acted = s.scalars(select(CaseEvent.id).where(CaseEvent.case_id == c.id, CaseEvent.actor == p.id, CaseEvent.actor_kind == "human")).first()
            if can_see(v, c, acted is not None, party=c.party):
                raise BreakGlassError("You can already see this case")
        else:
            party = s.scalars(select(Party).where(Party.id == target)).first()
            if party is None:
                raise BreakGlassError("No customer with that id")
            if can_see_party(v, party):
                raise BreakGlassError("You can already see this customer")
        live = s.scalars(select(AccessGrant).where(AccessGrant.person == p.id, AccessGrant.kind == kind, AccessGrant.target == target,
                                                   AccessGrant.closed.is_(None), AccessGrant.ends > at)).first()
        if live is not None:
            return live
        g = AccessGrant(id=new_id("brk"), person=p.id, kind=kind, target=target, reason=reason[:1000], opened=at,
                        ends=at + timedelta(hours=terms.hours))
        s.add(g)
        _event(s, p.id, p.org, "break_glass.opened", f"{kind} {target}", grant=g.id, reason=g.reason, hours=terms.hours)
        if kind == "case":
            # Written as the system, not as the person: a person's own events would keep the case in their sight for good.
            s.add(CaseEvent(case_id=target, actor="break-glass", actor_kind="system", kind="access.break_glass", ts=at,
                            payload={"person": p.id, "reason": g.reason, "until": g.ends.isoformat(), "grant": g.id}))
        s.flush()
    return g


def close(s: Session, grant_id: str, p: Persona, at: datetime | None = None) -> AccessGrant:
    at = at or now()
    with system(s):
        g = s.scalars(select(AccessGrant).where(AccessGrant.id == grant_id)).first()
        if g is None or g.person != p.id:
            raise BreakGlassError("No such grant of yours")
        if not _live(g, at):
            raise BreakGlassError("This grant has already ended")
        g.closed = at
        _event(s, p.id, p.org, "break_glass.closed", f"{g.kind} {g.target}", grant=g.id)
        s.flush()
    return g


def review(s: Session, grant_id: str, p: Persona, outcome: str, note: str = "", at: datetime | None = None) -> AccessGrant:
    at = at or now()
    if not p.can(REVIEW) or p.external:
        raise BreakGlassError("Reviewing break-glass openings needs the 'certify access' permission")
    if outcome not in OUTCOMES:
        raise BreakGlassError(f"Outcome is one of: {', '.join(OUTCOMES)}")
    if outcome == "not_justified" and not note.strip():
        raise BreakGlassError("Say what was wrong, so it can be followed up")
    with system(s):
        g = s.scalars(select(AccessGrant).where(AccessGrant.id == grant_id)).first()
        if g is None:
            raise BreakGlassError("No such grant")
        if g.person == p.id:
            raise BreakGlassError("Someone else must review your own break-glass opening")
        if g.review:
            raise BreakGlassError("This opening has already been reviewed")
        g.review, g.reviewed_by, g.reviewed, g.review_note = outcome, p.id, at, note.strip()[:1000]
        if _live(g, at) and outcome == "not_justified":
            g.closed = at  # a judged-unjustified opening ends at once
        _event(s, p.id, p.org, f"break_glass.{outcome}", f"{g.kind} {g.target}", grant=g.id, person=g.person, note=g.review_note)
        s.flush()
    return g


def listing(s: Session, p: Persona, at: datetime | None = None) -> dict:
    at = at or now()
    with system(s):
        rows = list(s.scalars(select(AccessGrant).order_by(AccessGrant.opened.desc()).limit(500)))
    reviewer = p.can(REVIEW) and not p.external
    return {"mine": [out(g, at) for g in rows if g.person == p.id],
            "to_review": [out(g, at) for g in rows if reviewer and g.review is None and g.person != p.id],
            "reviewed": [out(g, at) for g in rows if reviewer and g.review is not None][:50],
            "can_review": reviewer, "can_open": p.can(BREAK_GLASS) and not p.customer_facing and policy_of(tenant_of(s)).break_glass is not None,
            "hours": (policy_of(tenant_of(s)).break_glass.hours if policy_of(tenant_of(s)).break_glass else None)}


def out(g: AccessGrant, at: datetime) -> dict:
    return {"id": g.id, "person": g.person, "kind": g.kind, "target": g.target, "reason": g.reason, "opened": g.opened.isoformat(),
            "ends": g.ends.isoformat(), "live": _live(g, at), "closed": g.closed.isoformat() if g.closed else None,
            "review": g.review, "review_label": OUTCOMES.get(g.review or "", "Waiting for review"), "reviewed_by": g.reviewed_by,
            "reviewed": g.reviewed.isoformat() if g.reviewed else None, "review_note": g.review_note}


__all__ = ["BreakGlassError", "close", "listing", "open_grant", "review"]
