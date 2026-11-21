"""Whether two customer records are one party: what agrees, what disagrees, and how much each says.

Pure functions over plain values: no database, no client, no configuration. The resolver turns each Party into Facts,
asks `blocks` which records are worth comparing at all, and asks `compare` how alike two of them are and why.

A name alone never proposes a duplicate. A strong identifier (passport, SSN, LEI...) with a matching birth date can,
even when the names are written differently, because people change their names and data entry gets them wrong. A disagreement on something
that should not differ (date of birth, an SSN) counts against the match, so twins and namesakes are not proposed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..domain.identity import KINDS
from . import names

STRONG_AT = 0.95  # identifier kinds at least this strong (domain.identity.KINDS) can propose a match on their own
ONE_PER_PARTY = {"ssn", "ein", "lei", "tin"}  # two different numbers of these kinds mean two different parties
MAX_BLOCK = 50  # a shared phone or birthday held by more records than this tells us nothing


@dataclass(frozen=True)
class Facts:
    id: str
    name: str
    kind: str = "person"
    country: str = ""
    occupation: str = ""
    born: date | None = None
    postal_code: str = ""
    street: str = ""
    phone: str = ""
    email: str = ""
    identifiers: frozenset[tuple[str, str]] = field(default_factory=frozenset)  # (kind, normalized)

    @property
    def key(self) -> str:
        return names.key(self.name)

    @property
    def surname(self) -> str:
        ts = names.tokens(self.name)
        return ts[-1] if ts else ""


def _digits(text: str) -> str:
    return "".join(c for c in text or "" if c.isdigit())


def _phrase(label: str) -> str:
    """A label inside a sentence: 'Passport' becomes 'passport', 'US Social Security...' keeps its capitals."""
    return label[:1].lower() + label[1:] if label[1:2].islower() else label


def facts_of(p) -> Facts:
    """Facts from a Party (or anything shaped like one)."""
    phone = _digits(getattr(p, "phone", ""))
    return Facts(id=p.id, name=p.name, kind=p.kind or "person", country=p.country or "", occupation=p.occupation or "",
                 born=getattr(p, "born", None), postal_code=names.fold(getattr(p, "postal_code", "") or "").replace(" ", ""),
                 street=names.key(getattr(p, "address_line1", "") or ""), phone=phone[-10:] if len(phone) >= 7 else "",
                 email=(getattr(p, "email", "") or "").strip().lower(),
                 identifiers=frozenset((i.kind, i.normalized) for i in getattr(p, "identifiers", []) or []))


def blocks(f: Facts) -> set[str]:
    """Cheap keys: only records sharing one are ever compared."""
    out = {f"n:{f.key}"} if f.key else set()
    out |= {f"id:{k}:{v}" for k, v in f.identifiers if k in KINDS and KINDS[k].strength >= STRONG_AT}
    if f.born and f.surname:
        out.add(f"b:{f.born.isoformat()}:{f.surname}")
    if f.phone:
        out.add(f"ph:{f.phone}")
    if f.email:
        out.add(f"em:{f.email}")
    if f.postal_code and f.street:
        out.add(f"ad:{f.postal_code}:{f.street}")
    return out


@dataclass
class Verdict:
    score: float
    reasons: list[str]


def compare(a: Facts, b: Facts, shared_partners: int = 0) -> Verdict:
    """How alike two records are, 0 to 0.99, with the reasons in the order a person would want to read them."""
    reasons: list[str] = []
    if a.kind != b.kind:
        return Verdict(0.0, ["One is a person and the other a company"])
    if a.key and a.key == b.key:
        score = 0.60
        reasons.append("Same name")
    else:
        alike = names.similarity(a.name, b.name)
        score = 0.50 if alike >= 0.88 else 0.30 if alike >= 0.70 else 0.0
        reasons.append(f"Names are {alike:.0%} alike" if score else "Names differ")
    shared = sorted((KINDS[k].strength, k) for k, _ in a.identifiers & b.identifiers if k in KINDS)[::-1]
    for n, (strength, kind) in enumerate(shared):  # the strongest shared number says most; each further one adds a little
        score += (0.65 if n == 0 else 0.10) * strength
        reasons.append(f"Same {_phrase(KINDS[kind].label)}")
    mine, theirs = dict(a.identifiers), dict(b.identifiers)
    for kind in sorted(ONE_PER_PARTY & mine.keys() & theirs.keys()):
        if (kind, mine[kind]) not in a.identifiers & b.identifiers:
            score -= 0.40
            reasons.append(f"Different {_phrase(KINDS[kind].label)}")
    if a.born and b.born:
        if a.born == b.born:
            score += 0.15
            reasons.append("Same date of birth" if a.kind == "person" else "Same date of incorporation")
        else:
            score -= 0.30
            reasons.append("Different dates of birth" if a.kind == "person" else "Different dates of incorporation")
    if a.postal_code and a.street and (a.postal_code, a.street) == (b.postal_code, b.street):
        score += 0.10
        reasons.append("Same address")
    if a.phone and a.phone == b.phone:
        score += 0.10
        reasons.append("Same phone number")
    if a.email and a.email == b.email:
        score += 0.10
        reasons.append("Same email address")
    if a.country and a.country == b.country:
        score += 0.05
        reasons.append("Same country")
    if a.occupation and a.occupation == b.occupation:
        score += 0.20
        reasons.append("Same occupation")
    if shared_partners:
        score += 0.15
        reasons.append(f"Both transact with {shared_partners} of the same uncommon counterparties")
    return Verdict(round(max(0.0, min(score, 0.99)), 2), reasons)
