"""Who a customer is: the kinds of identifying number, how they are compared, and how they are shown.

Pure functions and constants: no database, no client, no configuration. The filings, the entity matcher, the data
pipelines and the screens all read from here, so a new kind of identifier is one entry in KINDS.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class IdKind:
    id: str
    label: str
    applies_to: tuple[str, ...]  # person, company
    strength: float  # how much a match on this number alone says two records are one party: 0 to 1
    secret: bool  # shown masked outside the filing itself
    fincen: str  # FinCEN PartyIdentificationTypeCode: 1 SSN/ITIN, 2 EIN, 4 foreign TIN, 5 licence or state id, 6 passport, 7 alien registration, 9 other
    goaml: str  # where goAML puts it on t_person or t_entity: an element name, or "identification" for the repeating block


KINDS: dict[str, IdKind] = {k.id: k for k in (
    IdKind("ssn", "US Social Security or taxpayer number", ("person",), 0.98, True, "1", "ssn"),
    IdKind("tin", "Tax identification number", ("person", "company"), 0.95, True, "4", "tax_number"),
    IdKind("ein", "US employer identification number", ("company",), 0.97, False, "2", "tax_number"),
    IdKind("passport", "Passport", ("person",), 0.97, True, "6", "identification"),
    IdKind("national_id", "National identity card", ("person",), 0.96, True, "9", "identification"),
    IdKind("drivers_license", "Driver's licence", ("person",), 0.90, True, "5", "identification"),
    IdKind("alien_registration", "Alien registration number", ("person",), 0.95, True, "7", "identification"),
    IdKind("company_registration", "Company registration number", ("company",), 0.95, False, "9", "incorporation_number"),
    IdKind("lei", "Legal Entity Identifier", ("company",), 0.99, False, "9", "identification"),
)}
# goAML type codes for the repeating identification block. Each FIU publishes its own list; these are the common defaults.
GOAML_ID_TYPES = {"passport": "A", "national_id": "B", "drivers_license": "C", "alien_registration": "D", "lei": "E"}


class IdentifierError(ValueError):
    pass


def normalize(value: str) -> str:
    """What two writings of one number share: '123-45-6789' and '123 45 6789' are the same SSN."""
    return "".join(c for c in (value or "").upper() if c.isalnum())


def check(kind: str, value: str) -> str:
    """The normalized number, or IdentifierError saying what is wrong with it."""
    if kind not in KINDS:
        raise IdentifierError(f"unknown identifier kind {kind!r}")
    n = normalize(value)
    if not n:
        raise IdentifierError(f"{KINDS[kind].label} is empty")
    if kind in ("ssn", "ein") and (len(n) != 9 or not n.isdigit()):
        raise IdentifierError(f"{KINDS[kind].label} must have nine digits")
    if kind == "lei" and len(n) != 20:
        raise IdentifierError("an LEI has twenty characters")
    if len(n) > 64:
        raise IdentifierError(f"{KINDS[kind].label} is too long")
    return n


def mask(kind: str, value: str) -> str:
    """Last four characters only, for numbers that are secret."""
    k = KINDS.get(kind)
    if k is None or not k.secret:
        return value
    n = normalize(value)
    return "•" * max(0, len(n) - 4) + n[-4:]


def age_on(born: date | None, day: date) -> int | None:
    if born is None:
        return None
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def one_line_address(line1: str, line2: str, city: str, region: str, postal_code: str, country: str) -> str:
    return ", ".join(x for x in (line1, line2, city, " ".join(y for y in (region, postal_code) if y), country) if x)
