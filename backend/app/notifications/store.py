"""Notification rows, written as case events are flushed, and read and cleared by the person they are for."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, event, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..db import Base
from ..tenancy import TenantScoped
from . import rules

LATEST = 30


def _now() -> datetime:
    from ..domain.models import now

    return now()


class Notification(TenantScoped, Base):
    __tablename__ = "notification"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    person: Mapped[str] = mapped_column(String(60))
    at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (Index("ix_notification_person", "tenant_id", "person", "read_at", "at"),)


def row(n: Notification) -> dict:
    return {"id": n.id, "at": n.at.isoformat(), "kind": n.kind, "title": n.title, "body": n.body, "case_id": n.case_id, "read": n.read_at is not None}


def inbox(s: Session, person: str, limit: int = LATEST) -> dict:
    unread = s.scalar(select(func.count()).select_from(Notification).where(Notification.person == person, Notification.read_at.is_(None))) or 0
    latest = s.scalars(select(Notification).where(Notification.person == person).order_by(Notification.at.desc(), Notification.id.desc()).limit(limit))
    return {"unread": unread, "items": [row(n) for n in latest]}


def mark_read(s: Session, person: str, ids: list[str] | None = None, at: datetime | None = None) -> int:
    q = update(Notification).where(Notification.person == person, Notification.read_at.is_(None))
    if ids is not None:
        q = q.where(Notification.id.in_(ids))
    n = s.execute(q.values(read_at=at or _now())).rowcount
    s.flush()
    return n or 0


# --- written as case events are flushed ----------------------------------------------------------------------------------
@event.listens_for(Session, "before_flush")
def _notify(session: Session, _context, _instances) -> None:
    from ..domain.models import Case, CaseEvent, new_id
    from ..domain.personas import policy_of
    from ..tenancy import TENANT_KEY

    tenant = session.info.get(TENANT_KEY)
    events = [o for o in session.new if isinstance(o, CaseEvent) and not getattr(o, "_notified", False) and o.kind in rules.RULES]
    if not events:
        return
    pending = {o.id: o for o in session.new if isinstance(o, Case)}
    for ev in events:
        ev._notified = True
        owner_tenant = ev.tenant_id or tenant
        if owner_tenant is None:
            continue
        case = pending.get(ev.case_id)
        if case is None:
            with session.no_autoflush:
                case = session.scalars(select(Case).where(Case.id == ev.case_id)).first()
        policy = policy_of(owner_tenant)
        ctx = rules.Context(owner=case.owner if case else None, holders=lambda role: [p.id for p in policy.holders(role)])
        note = rules.notes_for(ev.kind, ev.case_id, ev.payload or {}, ev.actor, ctx)
        if note is None:
            continue
        people = note.people if not (case and case.restricted) else tuple(p for p in note.people if p == case.owner)  # restricted: owner only
        for person in people:
            if person in policy.people:
                session.add(Notification(id=new_id("ntf"), tenant_id=owner_tenant, person=person, at=ev.ts or _now(), kind=ev.kind,
                                         title=note.title[:300], body=note.body, case_id=ev.case_id))
