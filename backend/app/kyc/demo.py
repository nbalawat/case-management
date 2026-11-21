"""Synthetic KYC material for the demo: company customers with ownership structures, documents, ratings and review triggers.

Everything here is invented. It goes through the same functions a person's actions do, as the client's own people.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import load_case_types
from ..domain.models import Account, Case, Party, Transaction
from ..domain.personas import Persona, policy_of
from ..tenancy import tenant_of
from . import evidence, ownership, risk, triggers


def pdf(title: str, lines: list[str]) -> bytes:
    """A one-page PDF with plain text, built by hand so the demo needs no PDF library."""
    esc = lambda x: x.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    text = "BT /F1 16 Tf 60 760 Td (" + esc(title) + ") Tj /F1 11 Tf " + " ".join(f"0 -22 Td ({esc(x)}) Tj" for x in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(text)} >>\nstream\n{text}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def _person(key: str, name: str, country: str = "US", pep: bool = False, verified: bool = True) -> dict:
    return {"key": key, "name": name, "kind": "person", "country": country, "pep": pep, "verified": verified}


def _firm(key: str, name: str, kind: str = "company", country: str = "US") -> dict:
    return {"key": key, "name": name, "kind": kind, "country": country}


def _own(owner: str, owned: str, percent: float, control: bool = False) -> dict:
    return {"owner": owner, "owned": owned, "percent": percent, "control": control}


SOURCE_OF_FUNDS = """Source of funds statement

Customer: {name}
Date of birth: 14 March 1979
Contact: {email}, +1 415 555 0134

The opening deposit of USD 180,000 comes from the sale of the customer's share in a family business.
Proceeds were paid to account 004417720931 at First Harbor Bank on 3 June and will be transferred from there.
The purchaser's solicitors confirmed completion by letter. Tax reference 512-44-9087.
"""


def seed(s: Session, end: datetime) -> dict:
    """Four company customers and one person, each showing a different part of KYC. Returns what was made, for the caller's counts."""
    tenant = tenant_of(s)
    if "kyc_refresh" not in load_case_types(tenant):
        return {"parties": 0, "transactions": 0}
    people = policy_of(tenant).people
    analysts = [p for p in people.values() if p.can("work") and not p.read_only and "kyc" in p.queues]
    others = [p for p in people.values() if p.can("work") and not p.read_only and p not in analysts]
    if not analysts or not others:
        return {"parties": 0, "transactions": 0}
    kyc, second = analysts[0], others[0]

    def company(n: int, name: str, business: str, country: str = "US", days: int = 900) -> Party:
        p = Party(id=f"C{20000 + n}", kind="company", name=name, occupation=business, country=country, risk_rating="low", onboarded=end - timedelta(days=days))
        s.add_all([p, Account(id=f"A{p.id[1:]}", party_id=p.id, product="business checking")])
        s.flush()
        return p

    def doc(p: Party, kind: str, title: str, filename: str, data: bytes, media_type: str, days_ago: int, expires: datetime | None = None,
            verified: bool = True, by: Persona = kyc) -> None:
        at = end - timedelta(days=days_ago)
        e = evidence.add(s, p.id, kind=kind, title=title, filename=filename, media_type=media_type, data=data, persona=by, expires=expires, at=at)
        if verified:
            evidence.verify(s, e.id, second if by is kyc else kyc, at=at + timedelta(hours=3))

    def passport(p: Party, holder: str, days_ago: int, expires: datetime, verified: bool = True) -> None:
        doc(p, "passport", f"Passport of {holder}", f"passport-{holder.lower().replace(' ', '-')}.pdf",
            pdf("PASSPORT (SYNTHETIC SPECIMEN)", [f"Holder: {holder}", f"Expires: {expires.date().isoformat()}", "This is invented data for a demonstration."]),
            "application/pdf", days_ago, expires, verified)

    def incorporation(p: Party, days_ago: int) -> None:
        doc(p, "certificate_of_incorporation", f"Certificate of incorporation, {p.name}", "certificate-of-incorporation.pdf",
            pdf("CERTIFICATE OF INCORPORATION (SYNTHETIC)", [f"Company: {p.name}", "Registered number: 0000000", "This is invented data for a demonstration."]),
            "application/pdf", days_ago)

    with triggers.resumed():
        # 1. A plain structure, fully evidenced. The analyst proposes a lower rating, which waits for a second person.
        a = company(1, "Halcyon Imports Ltd", "import-export trading company")
        ownership.record(s, a.id, [_person("ward", "Eleanor Ward"), _person("osei", "Daniel Osei")],
                         [_own("ward", "customer", 60, True), _own("osei", "customer", 40)], kyc, "Register of shareholders", ts=end - timedelta(days=400))
        incorporation(a, 400)
        doc(a, "ownership_register", "Register of shareholders", "register-of-shareholders.txt",
            b"Halcyon Imports Ltd\nRegister of shareholders\n\nEleanor Ward   600 ordinary shares\nDaniel Osei    400 ordinary shares\n", "text/plain", 400)
        passport(a, "Eleanor Ward", 400, end + timedelta(days=22))  # expiring soon: noted, no case
        passport(a, "Daniel Osei", 400, end + timedelta(days=1500))
        for n, (direction, firm, country, amount) in enumerate([("out", "Rheinhafen Spedition GmbH", "DE", 48200), ("out", "Straits Freight Pte", "SG", 31750),
                                                               ("in", "Harbor Grocers", "US", 22400), ("out", "Gulf Gate Trading FZE", "AE", 27900),
                                                               ("in", "Lakeside Fitness", "US", 9800), ("in", "Atlas Fuel", "US", 61300), ("out", "Rheinhafen Spedition GmbH", "DE", 39900)]):
            s.add(Transaction(id=f"TC{a.id}{n:02d}", account_id=f"A{a.id[1:]}", party_id=a.id, ts=end - timedelta(days=80 - 11 * n), amount=float(amount),
                              direction=direction, channel="wire", counterparty=firm, counterparty_country=country))
        s.flush()
        risk.assess(s, a.id, "kyc-scheduler", at=end - timedelta(days=3))
        risk.propose_override(s, a.id, "low", "mitigating_controls", "All shipments are pre-cleared through a licensed customs broker and paid by letter of credit.",
                              kyc, at=end - timedelta(days=2))

        # 2. A structure that became opaque: the first version named a person, the second routes most of it through a trust.
        b = company(2, "Northwind Holdings LLC", "property investment company")
        ownership.record(s, b.id, [_person("ward", "Anselm Ward")], [_own("ward", "customer", 100, True)], kyc, "Onboarding declaration", ts=end - timedelta(days=700))
        incorporation(b, 700)
        risk.assess(s, b.id, "kyc-scheduler", at=end - timedelta(days=699))
        ownership.record(s, b.id, [_firm("bluepeak", "Bluepeak Holdings SA", country="LU"), _firm("tidewater", "Tidewater Family Trust", "trust", "JE"),
                                   _person("ward", "Anselm Ward")],
                         [_own("bluepeak", "customer", 100, True), _own("tidewater", "bluepeak", 80, True), _own("ward", "bluepeak", 20)],
                         kyc, "Annual return", "Shares were transferred to a Luxembourg holding company in the last year.", ts=end - timedelta(days=6))
        risk.assess(s, b.id, "kyc-scheduler", at=end - timedelta(days=6, hours=-1))

        # 3. A politically exposed owner two layers up, and a listed company alongside. The analyst raised the rating further.
        c = company(3, "Castellan Marine Services Inc", "ship management company")
        ownership.record(s, c.id, [_firm("holdco", "Castellan Group BV", country="NL"), _firm("midco", "Halberg Shipping AS", country="NO"),
                                   _person("halberg", "Viktor Halberg", "NO", pep=True, verified=False), _firm("listed", "Nordkap Lines ASA", "listed_company", "NO"),
                                   _person("lund", "Ingrid Lund", "NO")],
                         [_own("holdco", "customer", 100, True), _own("midco", "holdco", 55, True), _own("listed", "holdco", 45), _own("halberg", "midco", 70, True),
                          _own("lund", "midco", 30)], kyc, "Group structure chart", ts=end - timedelta(days=120))
        incorporation(c, 120)
        passport(c, "Ingrid Lund", 120, end + timedelta(days=900))
        risk.assess(s, c.id, "kyc-scheduler", at=end - timedelta(days=119))
        risk.propose_override(s, c.id, "high", "emerging_concern", "Mr Halberg was appointed to a ministerial post last month; his identity documents are still outstanding.",
                              kyc, at=end - timedelta(days=4))

        # 4. A higher-risk business whose owner's passport has expired, which opens a review by itself.
        d = company(4, "Sable and Finch Jewellers Inc", "jewellery and precious metals dealer")
        ownership.record(s, d.id, [_person("finch", "Margaret Finch")], [_own("finch", "customer", 100, True)], kyc, "Register of shareholders", ts=end - timedelta(days=800))
        incorporation(d, 800)
        passport(d, "Margaret Finch", 800, end - timedelta(days=9))
        risk.assess(s, d.id, "kyc-scheduler", at=end - timedelta(days=30))

        # 5. A person in review, with a text document that holds details worth redacting before it is shared.
        review = s.scalars(select(Case).join(Party, (Party.id == Case.party_id) & (Party.tenant_id == Case.tenant_id))
                           .where(Case.case_type == "kyc_refresh", Case.closed.is_(None), Case.restricted.is_(False), Party.kind == "person")
                           .order_by(Case.created, Case.id)).first()
        if review is not None:
            p = s.scalars(select(Party).where(Party.id == review.party_id)).one()
            email = f"{p.name.lower().replace(' ', '.')}@example.com"
            e = evidence.add(s, p.id, kind="source_of_funds", title="Source of funds statement", filename="source-of-funds.txt", media_type="text/plain",
                             data=SOURCE_OF_FUNDS.format(name=p.name, email=email).encode(), persona=kyc, case_id=review.id, at=end - timedelta(days=1))
            evidence.redact(s, e.id, kyc, ["account_number", "national_id"])
            passport(p, p.name, 1, end + timedelta(days=2100), verified=False)
        triggers.scan(s, at=end)
    # The lower rating proposed for Halcyon Imports is left waiting: the approver decides it on screen.
    return {"parties": 4, "transactions": 7}
