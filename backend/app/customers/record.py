"""The customer record as screens, agents and reports read it. One place decides what is shown and what is masked."""
from __future__ import annotations

from datetime import date

from ..domain import identity
from ..domain.models import Party, PartyIdentifier

# What a complete record holds. A KYC reviewer sees which of these are missing; filings warn when they are.
REQUIRED = {"person": ("born", "address_line1", "city", "postal_code", "nationality", "identifier"),
            "company": ("born", "address_line1", "city", "nationality", "identifier")}
LABELS = {"born": "date of birth", "address_line1": "street address", "city": "city", "postal_code": "postal code",
          "nationality": "nationality", "identifier": "an identifying number"}
COMPANY_LABELS = LABELS | {"born": "date of incorporation", "nationality": "country of incorporation"}


def identifier(i: PartyIdentifier, *, reveal: bool = False, today: date | None = None) -> dict:
    kind = identity.KINDS.get(i.kind)
    today = today or date.today()
    return {"kind": i.kind, "label": kind.label if kind else i.kind, "value": i.value if reveal else identity.mask(i.kind, i.value),
            "masked": not reveal and bool(kind and kind.secret), "issuer_country": i.issuer_country,
            "issued": i.issued.isoformat() if i.issued else None, "expires": i.expires.isoformat() if i.expires else None,
            "expired": bool(i.expires and i.expires < today), "source": i.source}


def missing(p: Party) -> list[str]:
    """What a regulator or a KYC review would ask for that this record does not hold, in words."""
    labels = COMPANY_LABELS if p.kind == "company" else LABELS
    out = []
    for f in REQUIRED.get(p.kind, REQUIRED["person"]):
        have = bool(p.identifiers) if f == "identifier" else bool(getattr(p, f))
        if not have:
            out.append(labels[f])
    return out


def identity_of(p: Party, *, reveal: bool = False) -> dict:
    """Identity, address and contact details. Secret numbers show their last four characters unless `reveal`."""
    return {"born": p.born.isoformat() if p.born else None, "age": identity.age_on(p.born, date.today()) if p.kind == "person" else None,
            "nationality": p.nationality, "segment": p.segment, "booking_jurisdiction": p.booking_jurisdiction,
            "relationship_manager": p.relationship_manager,
            "address": {"line1": p.address_line1, "line2": p.address_line2, "city": p.city, "region": p.region, "postal_code": p.postal_code,
                        "country": p.country,
                        "one_line": identity.one_line_address(p.address_line1, p.address_line2, p.city, p.region, p.postal_code, p.country)},
            "phone": p.phone, "email": p.email, "identifiers": [identifier(i, reveal=reveal) for i in p.identifiers], "missing": missing(p)}
