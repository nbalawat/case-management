"""Correcting a customer's identity by hand, and seeing the numbers that are masked. Every step leaves a record.

Most identity data arrives through the data pipelines from the client's own systems. A person fixes what is wrong
or missing here, for example while a KYC review or a report is open, and the change is kept with who made it.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import identity
from ..domain.models import Party, PartyEvent, PartyIdentifier, new_id, now
from ..domain.personas import Persona
from . import record

EDIT = "work"  # anyone who works cases may correct the record; the access layer can narrow this by attribute
# Fields a person may correct here. Name, kind and risk rating have their own flows (pipelines and the risk review).
EDITABLE = ("born", "nationality", "address_line1", "address_line2", "city", "region", "postal_code", "phone", "email", "segment",
            "booking_jurisdiction", "relationship_manager")
SEGMENTS = ("retail", "business", "private", "correspondent", "wealth", "institutional")


class CustomerError(ValueError):
    pass


def _party(s: Session, party_id: str) -> Party:
    p = s.scalars(select(Party).where(Party.id == party_id)).first()
    if p is None:
        raise CustomerError("Customer not found")
    return p


def _can(persona: Persona) -> None:
    if persona.read_only or not persona.can(EDIT):
        raise CustomerError(f"{persona.title} cannot change customer records")


def _note(s: Session, p: Party, persona: Persona, event: str, ts: datetime, **detail: object) -> None:
    s.add(PartyEvent(id=new_id("pev"), party_id=p.id, at=ts, by=persona.id, kind=event, detail=detail))


def _clean(field: str, value: object) -> object:
    text = str(value or "").strip()
    if field == "born":
        if not text:
            return None
        try:
            d = date.fromisoformat(text[:10])
        except ValueError:
            raise CustomerError("Enter the date as YYYY-MM-DD") from None
        if d > date.today() or d.year < 1850:
            raise CustomerError("That date cannot be right")
        return d
    if field in ("nationality", "booking_jurisdiction"):
        if text and (len(text) != 2 or not text.isalpha()):
            raise CustomerError(f"{field.replace('_', ' ').capitalize()} is a two-letter country code")
        return text.upper() or (None if field == "booking_jurisdiction" else "")
    if field == "segment":
        if text not in SEGMENTS:
            raise CustomerError(f"Segment is one of {', '.join(SEGMENTS)}")
        return text
    if field == "email" and text and "@" not in text:
        raise CustomerError("That is not an email address")
    if field == "relationship_manager":
        return text or None
    return text.upper() if field == "region" else text


def update(s: Session, party_id: str, changes: dict, persona: Persona, reason: str = "", ts: datetime | None = None) -> dict:
    """Correct identity fields. Only what changes is recorded, with the value before and after."""
    _can(persona)
    p, ts = _party(s, party_id), ts or now()
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise CustomerError(f"These cannot be changed here: {', '.join(sorted(unknown))}")
    diff = {}
    for field, raw in changes.items():
        new, old = _clean(field, raw), getattr(p, field)
        if new != old:
            diff[field] = {"from": old.isoformat() if isinstance(old, date) else old, "to": new.isoformat() if isinstance(new, date) else new}
            setattr(p, field, new)
    if diff:
        _note(s, p, persona, "identity.updated", ts, changes=diff, reason=reason.strip()[:300])
    s.flush()
    return record.identity_of(p)


def add_identifier(s: Session, party_id: str, kind: str, value: str, persona: Persona, issuer_country: str = "", expires: str = "",
                   ts: datetime | None = None) -> dict:
    _can(persona)
    p, ts = _party(s, party_id), ts or now()
    try:
        normalized = identity.check(kind, value)
    except identity.IdentifierError as e:
        raise CustomerError(str(e)) from None
    if kind in identity.KINDS and p.kind not in identity.KINDS[kind].applies_to:
        raise CustomerError(f"A {identity.KINDS[kind].label.lower()} does not apply to a {p.kind}")
    if any(i.kind == kind and i.normalized == normalized for i in p.identifiers):
        raise CustomerError("This customer already has that number")
    issuer = _clean("nationality", issuer_country)
    try:
        exp = date.fromisoformat(expires.strip()[:10]) if expires.strip() else None
    except ValueError:
        raise CustomerError("Enter the expiry date as YYYY-MM-DD") from None
    p.identifiers.append(PartyIdentifier(id=new_id("pid"), tenant_id=p.tenant_id, kind=kind, value=value.strip()[:64], normalized=normalized,
                                         issuer_country=issuer, expires=exp, source=f"entered by {persona.id}"[:40]))
    _note(s, p, persona, "identifier.added", ts, kind=kind, value=identity.mask(kind, value))
    s.flush()
    return record.identity_of(p)


def remove_identifier(s: Session, party_id: str, kind: str, value: str, persona: Persona, reason: str, ts: datetime | None = None) -> dict:
    _can(persona)
    p, ts = _party(s, party_id), ts or now()
    if not reason.strip():
        raise CustomerError("Say why the number is being removed")
    n = identity.normalize(value)
    gone = [i for i in p.identifiers if i.kind == kind and (i.normalized == n or identity.mask(kind, i.value) == value)]
    if len(gone) != 1:
        raise CustomerError("No such number on this customer")
    p.identifiers.remove(gone[0])
    _note(s, p, persona, "identifier.removed", ts, kind=kind, value=identity.mask(kind, gone[0].value), reason=reason.strip()[:300])
    s.flush()
    return record.identity_of(p)


def reveal(s: Session, party_id: str, persona: Persona, reason: str, ts: datetime | None = None) -> dict:
    """The full numbers, for someone who needs them for the work in hand. Who looked, and why, is kept."""
    if persona.read_only or not persona.can(EDIT):
        raise CustomerError(f"{persona.title} cannot see full identity numbers")
    if not reason.strip():
        raise CustomerError("Say why you need the full numbers")
    p, ts = _party(s, party_id), ts or now()
    _note(s, p, persona, "identifiers.revealed", ts, reason=reason.strip()[:300], kinds=sorted({i.kind for i in p.identifiers}))
    s.flush()
    return record.identity_of(p, reveal=True)


def history(s: Session, party_id: str, limit: int = 50) -> list[dict]:
    return [{"at": e.at.isoformat(), "by": e.by, "kind": e.kind, "detail": e.detail}
            for e in s.scalars(select(PartyEvent).where(PartyEvent.party_id == party_id).order_by(PartyEvent.at.desc()).limit(limit))]
