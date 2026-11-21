"""Synthetic identity for the demo's customers and payment details for its transactions.

Runs once, after every other part of the seed has made its customers and transactions. Each value is drawn from a
random stream fixed by the client and the customer's id, so adding this step moves nothing that was planted before
it, and every run gives the same people the same birthdays, addresses and numbers.

Every number here is invalid on purpose: SSNs use area 9xx with a group below 50 (never issued as an SSN or ITIN),
EINs start 00 (never assigned), and LEIs start with the reserved prefix 0000.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.identity import normalize
from ..domain.models import Account, Party, PartyIdentifier, Transaction
from ..domain.personas import policy_of
from ..entities import names
from ..tenancy import tenant_of

STREETS = ["Harbor Street", "Maple Avenue", "Elm Court", "Cedar Lane", "Beacon Road", "Willow Way", "Granite Place", "Orchard Drive",
           "Lakeview Terrace", "Mill Road", "Station Street", "Chestnut Hill", "Bayside Avenue", "Kingsbury Road", "Juniper Close"]
# city, state, ZIP prefix
US_CITIES = [("Boston", "MA", "021"), ("Cambridge", "MA", "021"), ("Providence", "RI", "029"), ("Hartford", "CT", "061"),
             ("New York", "NY", "100"), ("Newark", "NJ", "071"), ("Philadelphia", "PA", "191"), ("Baltimore", "MD", "212"),
             ("Miami", "FL", "331"), ("Chicago", "IL", "606"), ("Houston", "TX", "770"), ("San Francisco", "CA", "941"),
             ("Oakland", "CA", "946"), ("Seattle", "WA", "981"), ("Denver", "CO", "802")]
ABROAD = {"GB": [("London", "", "EC2A"), ("Manchester", "", "M1")], "DE": [("Berlin", "", "10115"), ("Hamburg", "", "20095")],
          "LU": [("Luxembourg", "", "L-1111"), ("Esch-sur-Alzette", "", "L-4002")], "SG": [("Singapore", "", "018989")],
          "AE": [("Dubai", "", "00000")], "NL": [("Rotterdam", "", "3011")], "NO": [("Oslo", "", "0150")], "JE": [("St Helier", "", "JE2")],
          "CH": [("Geneva", "", "1201")], "FR": [("Paris", "", "75008")], "CY": [("Limassol", "", "3036")], "PA": [("Panama City", "", "0801")]}
FOREIGN_NATIONALITIES = ["GB", "DE", "FR", "IN", "BR", "NG", "MX", "PH", "CN", "PL", "IT", "GH"]
BOOKED = {"alder": "LU"}  # where each demo client books its relationships; others book in the US
RELATIONSHIP_MANAGERS = {"meridian": "rosa", "alder": "gwen", "kestrel": "noor"}
# Two firms whose names differ only a little but who are, in this story, one firm: they pay into the same account.
SAME_FIRM = {"meridian trading partners intl": "meridian trade partners", "orion global consultants": "orion global consulting"}
PURPOSE = {("ach", "in"): "SALA", ("card", "out"): "GDDS", ("ach", "out"): "SUPP", ("p2p", "out"): "GIFT", ("p2p", "in"): "GIFT",
           ("wire", "out"): "TRAD", ("wire", "in"): "TRAD"}


def _rng(*parts: str) -> random.Random:
    return random.Random(hashlib.sha256("|".join(parts).encode()).digest())


def _digits(r: random.Random, n: int) -> str:
    return "".join(str(r.randint(0, 9)) for _ in range(n))


def _address(r: random.Random, country: str) -> dict:
    if country == "US":
        city, state, zip3 = r.choice(US_CITIES)
        return {"address_line1": f"{r.randint(2, 980)} {r.choice(STREETS)}", "address_line2": r.choice(["", "", "", f"Apt {r.randint(1, 40)}"]),
                "city": city, "region": state, "postal_code": f"{zip3}{r.randint(0, 99):02d}"}
    city, region, post = r.choice(ABROAD.get(country) or [("Capital City", "", "")])
    return {"address_line1": f"{r.randint(1, 120)} {r.choice(STREETS)}", "address_line2": "", "city": city, "region": region, "postal_code": post}


def _identifiers(r: random.Random, p: Party, nationality: str, today: date) -> list[tuple[str, str, str, date | None]]:
    """(kind, value, issuing country, expiry) for one customer."""
    if p.kind == "company":
        # Registration numbers in each register's own form: Luxembourg RCS numbers look like B123456.
        reg = f"B{r.randint(10000, 299999)}" if p.country == "LU" else f"{p.country}-{_digits(r, 8)}"
        out = [("company_registration", reg, p.country, None)]
        if p.country == "US":
            out.append(("ein", f"00-{_digits(r, 7)}", "US", None))
        else:
            out.append(("tin", f"{p.country}{_digits(r, 9)}", p.country, None))
        if r.random() < 0.5:
            out.append(("lei", "0000" + "".join(r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(16)), "", None))
        return out
    out = []
    expires = today + timedelta(days=r.randint(-200, 3200))
    if p.country == "US":
        out.append(("ssn", f"9{_digits(r, 2)}-{r.randint(10, 49)}-{_digits(r, 4)}", "US", None))
        if r.random() < 0.6:
            out.append(("drivers_license", f"S{_digits(r, 8)}", "US", expires))
    else:
        out.append(("national_id", f"{p.country}{_digits(r, 9)}", p.country, expires))
    if nationality != p.country or r.random() < 0.25:
        out.append(("passport", f"{nationality[:1]}{_digits(r, 8)}", nationality, expires + timedelta(days=r.randint(0, 900))))
    return out


def enrich_party(p: Party, tenant_id: str, managers: tuple[str, ...], today: date) -> None:
    """Fill what is missing on one customer. Values already set (by a pipeline, or a planted story) are kept."""
    r = _rng("identity", tenant_id, p.id)
    if p.kind == "company":
        p.born = p.born or (p.onboarded.date() - timedelta(days=r.randint(400, 9000)))
        p.nationality = p.nationality or p.country
        p.segment = "business" if p.segment in ("", "retail") else p.segment
    else:
        p.born = p.born or date(r.randint(1946, 2004), r.randint(1, 12), r.randint(1, 28))
        p.nationality = p.nationality or (r.choice(FOREIGN_NATIONALITIES) if r.random() < 0.15 else p.country)
        if p.pep or (tenant_id == "alder" and r.random() < 0.7):
            p.segment = "private"
    if not p.address_line1:
        for k, v in _address(r, p.country).items():
            setattr(p, k, v)
    p.phone = p.phone or (f"+1 {r.choice(['617', '212', '415', '312', '305'])} 555 {r.randint(100, 199):04d}" if p.country == "US"
                          else f"+{r.randint(30, 99)} 555 {_digits(r, 6)}")
    if not p.email:
        local = ".".join(names.tokens(p.name)[:2]) or p.id.lower()
        p.email = f"{local}@{'example.com' if p.kind == 'person' else local.replace('.', '') + '.example'}"
    p.booking_jurisdiction = p.booking_jurisdiction or BOOKED.get(tenant_id, "US")
    if p.relationship_manager is None and managers and p.segment in ("business", "private"):
        p.relationship_manager = managers[r.randrange(len(managers))]
    have = {(i.kind, i.normalized) for i in p.identifiers}
    if not have:
        for n, (kind, value, issuer, expires) in enumerate(_identifiers(r, p, p.nationality, today)):
            p.identifiers.append(PartyIdentifier(id=f"pid-{hashlib.sha1(f'{tenant_id}|{p.id}|{n}'.encode()).hexdigest()[:12]}", tenant_id=tenant_id,
                                                 kind=kind, value=value, normalized=normalize(value), issuer_country=issuer, expires=expires))


def _account_for(name: str, tenant_id: str) -> tuple[str, str]:
    """The same firm always pays into the same account, whichever way its name is spelled in the payment."""
    k = names.key(name)
    k = SAME_FIRM.get(k, k)
    r = _rng("account", tenant_id, k)
    country = "LU"
    return (f"{country}{r.randint(10, 99)}{_digits(r, 16)}", f"{''.join(r.choice('ABCDEFGHKLMNPRSTUVW') for _ in range(4))}{country}LL")


def enrich_transactions(s: Session, tenant_id: str) -> int:
    """Payment details the canonical model carries: the other side's account and bank, and a purpose code."""
    n = 0
    for t in s.scalars(select(Transaction).where(Transaction.purpose == "")):
        t.purpose = PURPOSE.get((t.channel, t.direction), "")
        if t.channel in ("wire", "ach") and t.counterparty and not t.counterparty_account:
            acct, bank = _account_for(t.counterparty, tenant_id)
            if t.channel == "ach":
                r = _rng("ach", tenant_id, names.key(t.counterparty))
                acct, bank = _digits(r, 10), f"0{_digits(r, 8)}"  # routing numbers starting 0 are reserved for the Federal Reserve
            t.counterparty_account, t.counterparty_institution = acct, bank
        n += 1
    return n


REOPENED = {"meridian": "P90001"}  # the client where one customer was onboarded twice, and the second record's id


def plant_duplicate(s: Session, end: datetime) -> int:
    """One customer onboarded a second time, years later, by a branch that did not find the first record.

    The new record writes the name with a middle initial and gives a new email, but the SSN, birth date and address are
    the same: the kind of duplicate only the identity details can catch. Returns how many customers it added.
    """
    tenant = tenant_of(s)
    new_id = REOPENED.get(tenant)
    if new_id is None or s.scalars(select(Party.id).where(Party.id == new_id)).first():
        return 0
    first = next((p for p in s.scalars(select(Party).where(Party.kind == "person", Party.country == "US").order_by(Party.id))
                  if p.born and any(i.kind == "ssn" for i in p.identifiers) and len(names.tokens(p.name)) == 2), None)
    if first is None:
        return 0
    given, family = first.name.split(" ", 1) if " " in first.name else (first.name, "")
    twin = Party(id=new_id, name=f"{given} {_rng('initial', tenant).choice('ABCDEJLMR')}. {family}".strip(), occupation=first.occupation,
                 country=first.country, risk_rating="low", onboarded=end - timedelta(days=40), born=first.born, nationality=first.nationality,
                 address_line1=first.address_line1, address_line2=first.address_line2, city=first.city, region=first.region,
                 postal_code=first.postal_code, phone=first.phone, email=f"{given.lower()}{first.born.year % 100:02d}@example.net")
    ssn = next(i for i in first.identifiers if i.kind == "ssn")
    twin.identifiers.append(PartyIdentifier(id=f"pid-{hashlib.sha1(f'{tenant}|{new_id}|0'.encode()).hexdigest()[:12]}", tenant_id=tenant,
                                            kind="ssn", value=ssn.value, normalized=ssn.normalized, issuer_country="US", source="branch onboarding"))
    s.add_all([twin, Account(id=f"A{new_id[1:]}", party_id=new_id)])
    s.flush()
    return 1


def seed(s: Session, end: datetime) -> dict:
    tenant = tenant_of(s)
    rm = RELATIONSHIP_MANAGERS.get(tenant)
    managers = (rm,) if rm else tuple(sorted(p.id for p in policy_of(tenant).people.values() if p.can("work") and not p.read_only))[:2]
    parties = list(s.scalars(select(Party).order_by(Party.id)))
    for p in parties:
        enrich_party(p, tenant, managers, end.date())
    s.flush()
    return {"parties": len(parties), "transactions": enrich_transactions(s, tenant)}
