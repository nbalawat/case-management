"""FinCEN BSA XML: the batch layout used for discrete and batch e-filing of SARs (SARX) and CTRs (CTRX).

Written to FinCEN's published batch schemas (EFL_SARXBatchSchema.xsd and EFL_CTRXBatchSchema.xsd, version 1.2), which
ship with this module under app/filing/schemas/fincen and every document is validated against before it can be sent
(app/filing/validation.py). The schema fixes the structure, element order, lengths and code lists. FinCEN's business
rules beyond the schema (its "edit checks") and acceptance by the BSA E-Filing test system have not been tried.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from ...domain import identity
from .common import add, common_problems, compact_date, pretty, split_name

NS = "www.fincen.gov/base"
NARRATIVE_PART, NARRATIVE_PARTS = 4000, 5
# Party roles in the batch.
TRANSMITTER, TRANSMITTER_CONTACT, FILER, WHERE_IT_HAPPENED, SUBJECT, CONTACT_OFFICE, ACCOUNT_HOLDER_FI = "35", "37", "30", "34", "33", "8", "41"
CTR_PERSON = "50"  # a person on whose behalf a CTR transaction was conducted
# (type, subtype) per category of our own, from FinCEN's suspicious activity code list (FinCENReferenceCodes.xsd 1.5).
ACTIVITY_CODES = {"structuring": ("1", "114"),  # transaction(s) below the CTR threshold
                  "rapid_movement": ("9", "925"),  # no apparent economic, business or lawful purpose
                  "high_risk_geo": ("9", "928"),  # transaction(s) involving a foreign high-risk jurisdiction
                  }
OTHER = ("9", "9999")  # other suspicious activity, described in words (watchlist matches go here: FinCEN has no code for them)
TCC = "PSAPP000"  # the transmitter control code FinCEN issues to a filer; the client's own replaces this placeholder
ET.register_namespace("fc2", NS)


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _clip(text: object, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


def _digits(text: str) -> str:
    return "".join(c for c in text or "" if c.isdigit())


class _Batch:
    """Every element FinCEN counts carries a sequence number that runs through the whole file."""

    def __init__(self, form: str, report: dict, assoc: tuple[str, ...]):
        self.n = 0
        self.parties = 0
        self.root = ET.Element(_q("EFilingBatchXML"))
        add(self.root, _q("FormTypeCode"), form)
        self.activity = self.seq(self.root, "Activity")
        if report.get("prior_reference"):
            add(self.activity, _q("EFilingPriorDocumentNumber"), _digits(report["prior_reference"])[:14])
        add(self.activity, _q("FilingDateText"), compact_date(report["prepared_at"]))
        a = self.seq(self.activity, "ActivityAssociation")
        for tag in assoc:  # schema order: continuing, corrects/amends, initial, joint
            add(a, _q(tag), "Y")

    def seq(self, parent: ET.Element, tag: str) -> ET.Element:
        self.n += 1
        return add(parent, _q(tag), SeqNum=self.n)

    def party(self, role: str, **flags: str) -> ET.Element:
        """A party with its type code and any of PartyType's own fields, in the schema's (alphabetical) order."""
        self.parties += 1
        p = self.seq(self.activity, "Party")
        add(p, _q("ActivityPartyTypeCode"), role)
        for tag in sorted(flags):
            add(p, _q(tag), flags[tag])
        return p

    def name(self, p: ET.Element, *, full: str = "", last: str = "", first: str = "") -> None:
        el = self.seq(p, "PartyName")
        add(el, _q("PartyNameTypeCode"), "L")
        if last:
            add(el, _q("RawEntityIndividualLastName"), _clip(last, 150))
        if first:
            add(el, _q("RawIndividualFirstName"), _clip(first, 35))
        if full:
            add(el, _q("RawPartyFullName"), _clip(full, 150))

    def address(self, p: ET.Element, line1: str, city: str, region: str, postal: str, country: str) -> None:
        el = self.seq(p, "Address")
        if not city:
            add(el, _q("CityUnknownIndicator"), "Y")
        if not country:
            add(el, _q("CountryCodeUnknownIndicator"), "Y")
        if city:
            add(el, _q("RawCityText"), _clip(city, 50))
        if country:
            add(el, _q("RawCountryCodeText"), country[:2])
        if region and country in ("US", "CA", "MX"):
            add(el, _q("RawStateCodeText"), _clip(region, 3))
        if line1:
            add(el, _q("RawStreetAddress1Text"), _clip(line1, 100))
        if postal:
            add(el, _q("RawZIPCode"), _clip(postal.replace(" ", "").replace("-", ""), 9))
        if not region and country == "US":
            add(el, _q("StateCodeUnknownIndicator"), "Y")
        if not line1:
            add(el, _q("StreetAddressUnknownIndicator"), "Y")
        if not postal:
            add(el, _q("ZIPCodeUnknownIndicator"), "Y")

    def phone(self, p: ET.Element, number: str, kind: str = "") -> None:
        digits = _digits(number)
        if digits:
            el = self.seq(p, "PhoneNumber")
            add(el, _q("PhoneNumberText"), digits[:16])
            if kind:
                add(el, _q("PhoneNumberTypeCode"), kind)

    def ident(self, p: ET.Element, code: str, number: str, *, country: str = "", other: str = "") -> None:
        el = self.seq(p, "PartyIdentification")
        if country and code not in ("1", "2"):
            add(el, _q("OtherIssuerCountryText"), country[:2])
        if code == "9" and other:
            add(el, _q("OtherPartyIdentificationTypeText"), _clip(other, 50))
        add(el, _q("PartyIdentificationNumberText"), identity.normalize(number)[:25])
        add(el, _q("PartyIdentificationTypeCode"), code)

    def institution(self, role: str, inst: dict) -> None:
        p = self.party(role, **({"PrimaryRegulatorTypeCode": inst["regulator_code"]} if role in (FILER, WHERE_IT_HAPPENED) and inst.get("regulator_code") else {}))
        self.name(p, full=inst.get("name", ""))
        self.address(p, inst.get("address", ""), inst.get("city", ""), inst.get("state", ""), inst.get("postal_code", ""), inst.get("country", ""))
        if role == TRANSMITTER:
            self.phone(p, inst.get("contact_phone", ""))
            self.ident(p, "4", inst.get("tax_id", ""))
            self.ident(p, "28", inst.get("transmitter_control_code") or TCC)
        else:
            self.ident(p, "2", inst.get("tax_id", ""))

    def contact(self, role: str, inst: dict) -> None:
        p = self.party(role)
        self.name(p, full=inst.get("contact_office", ""))
        self.phone(p, inst.get("contact_phone", ""))

    def subject(self, role: str, subject: dict, inst: dict) -> ET.Element:
        person = subject["kind"] == "person"
        flags: dict[str, str] = {}
        if person and subject.get("born"):
            flags["IndividualBirthDateText"] = compact_date(subject["born"])
        elif person:
            flags["BirthDateUnknownIndicator"] = "Y"
        if not person:
            flags["PartyAsEntityOrganizationIndicator"] = "Y"
        p = self.party(role, **flags)
        if person:
            first, last = split_name(subject["name"])
            self.name(p, last=last, first=first)
        else:
            self.name(p, last=subject["name"])
        a = subject.get("address") or {}
        self.address(p, a.get("line1", ""), a.get("city", ""), a.get("region", ""), a.get("postal_code", ""), a.get("country") or subject["country"])
        self.phone(p, subject.get("phone", ""), "R" if person else "W")
        ids = subject.get("identifiers") or []
        tins = [i for i in ids if i["kind"] in ("ssn", "ein", "tin")]
        for i in tins[:1] + [i for i in ids if i not in tins][:3]:
            kind = identity.KINDS.get(i["kind"])
            if kind:
                self.ident(p, kind.fincen, i["value"], country=i.get("issuer_country", ""), other=kind.label)
        if not tins:
            el = self.seq(p, "PartyIdentification")
            add(el, _q("TINUnknownIndicator"), "Y")
        if subject.get("occupation"):
            occ = self.seq(p, "PartyOccupationBusiness")
            add(occ, _q("OccupationBusinessText"), _clip(subject["occupation"], 50))
        if subject.get("email"):
            el = self.seq(p, "ElectronicAddress")
            add(el, _q("ElectronicAddressText"), _clip(subject["email"], 517))
            add(el, _q("ElectronicAddressTypeCode"), "E")
        return p

    def accounts(self, p: ET.Element, subject: dict, inst: dict) -> None:
        """The subject's accounts, held at the filing institution."""
        if not subject["accounts"]:
            return
        assoc = self.seq(p, "PartyAccountAssociation")
        add(assoc, _q("PartyAccountAssociationTypeCode"), "7")
        fi = self.seq(assoc, "Party")
        add(fi, _q("ActivityPartyTypeCode"), ACCOUNT_HOLDER_FI)
        tin = self.seq(fi, "PartyIdentification")
        add(tin, _q("PartyIdentificationNumberText"), _digits(inst.get("tax_id", ""))[:25])
        add(tin, _q("PartyIdentificationTypeCode"), "2")
        for a in subject["accounts"][:99]:
            acct = self.seq(fi, "Account")
            add(acct, _q("AccountNumberText"), _clip(a["id"], 40))
            link = self.seq(acct, "PartyAccountAssociation")
            if a["status"] == "closed":
                add(link, _q("AccountClosedIndicator"), "Y")
            add(link, _q("PartyAccountAssociationTypeCode"), "7")

    def finish(self, total: float, **counts: int) -> str:
        self.root.set("ActivityCount", "1")
        self.root.set("TotalAmount", str(round(total)))
        self.root.set("PartyCount", str(self.parties))
        for k, v in counts.items():
            self.root.set(k, str(v))
        return pretty(self.root)


def _association(report: dict, *, sar: bool) -> tuple[str, ...]:
    if report.get("prior_reference"):
        return ("CorrectsAmendsPriorReportIndicator",)
    if sar and report.get("continuing"):
        return ("ContinuingActivityReportIndicator",)
    return ("InitialReportIndicator",)


def render_sar(report: dict) -> str:
    inst, act = report["institution"], report["activity"]
    b = _Batch("SARX", report, _association(report, sar=True))
    b.institution(TRANSMITTER, inst)
    b.contact(TRANSMITTER_CONTACT, inst)
    b.institution(FILER, inst)
    b.contact(CONTACT_OFFICE, inst)
    b.institution(WHERE_IT_HAPPENED, inst)
    for subject in [report["subject"], *report.get("other_subjects", [])]:
        b.accounts(b.subject(SUBJECT, subject, inst), subject, inst)
    sa = b.seq(b.activity, "SuspiciousActivity")
    add(sa, _q("SuspiciousActivityFromDateText"), compact_date(act["from"]))
    add(sa, _q("SuspiciousActivityToDateText"), compact_date(act["to"]))
    add(sa, _q("TotalSuspiciousAmountText"), round(act["total"]))  # whole dollars
    used: set[str] = set()
    for c in act["categories"]:
        kind, sub = ACTIVITY_CODES.get(c["key"], OTHER)
        if sub in used:  # the schema allows each subtype once per report
            continue
        used.add(sub)
        cl = b.seq(sa, "SuspiciousActivityClassification")
        if (kind, sub) == OTHER:
            add(cl, _q("OtherSuspiciousActivityTypeText"), _clip(c["label"], 50))
        add(cl, _q("SuspiciousActivitySubtypeID"), sub)
        add(cl, _q("SuspiciousActivityTypeID"), kind)
    text = report["narrative"]
    parts = [text[i:i + NARRATIVE_PART] for i in range(0, len(text), NARRATIVE_PART)][:NARRATIVE_PARTS] or [""]
    for n, part in enumerate(parts, 1):
        nar = b.seq(b.activity, "ActivityNarrativeInformation")
        add(nar, _q("ActivityNarrativeSequenceNumber"), n)
        add(nar, _q("ActivityNarrativeText"), part)
    return b.finish(act["total"], ActivityAttachmentCount=0, AttachmentCount=0)


def render_ctr(report: dict) -> str:
    inst, act, subject = report["institution"], report["activity"], report["subject"]
    b = _Batch("CTRX", report, ())
    a = b.activity.find(_q("ActivityAssociation"))
    amend = bool(report.get("prior_reference"))
    for tag, on in (("CorrectsAmendsPriorReportIndicator", amend), ("FinCENDirectBackFileIndicator", False), ("InitialReportIndicator", not amend)):
        add(a, _q(tag), "Y" if on else "")  # the CTR schema wants every indicator present, blank when it does not apply
    b.institution(TRANSMITTER, inst)
    b.contact(TRANSMITTER_CONTACT, inst)
    b.institution(FILER, inst)
    b.contact(CONTACT_OFFICE, inst)
    b.institution(WHERE_IT_HAPPENED, inst)
    _ctr_person(b, subject, act)
    cta = b.seq(b.activity, "CurrencyTransactionActivity")
    for tag in ("AggregateTransactionIndicator", "ArmoredCarServiceIndicator", "ATMIndicator", "MailDepositShipmentIndicator",
                "NightDepositIndicator", "SharedBranchingIndicator"):
        add(cta, _q(tag), "Y" if tag == "AggregateTransactionIndicator" and act.get("aggregated") else "")
    add(cta, _q("TotalCashInReceiveAmountText"), round(act.get("cash_in", 0)) or "")
    add(cta, _q("TotalCashOutAmountText"), round(act.get("cash_out", 0)) or "")
    add(cta, _q("TransactionDateText"), compact_date(act["from"]))
    totals = {"in": 0.0, "out": 0.0}
    for t in report["transactions"]:
        totals[t["direction"]] += t["amount"]
    for direction, code in (("in", "55"), ("out", "56")):  # deposits, withdrawals
        if totals[direction]:
            d = b.seq(cta, "CurrencyTransactionActivityDetail")
            add(d, _q("CurrencyTransactionActivityDetailTypeCode"), code)
            add(d, _q("DetailTransactionAmountText"), round(totals[direction]))
            add(d, _q("OtherCurrencyTransactionActivityDetailText"), "")
            add(d, _q("OtherForeignCurrencyCountryText"), "")
    return b.finish(act["total"])


def _ctr_person(b: _Batch, subject: dict, act: dict) -> None:
    """The customer on whose behalf the cash moved, with what went in and out of each account."""
    person = subject["kind"] == "person"
    flags = {"IndividualEntityCashInAmountText": str(round(act.get("cash_in", 0)) or ""),
             "IndividualEntityCashOutAmountText": str(round(act.get("cash_out", 0)) or "")}
    if person and subject.get("born"):
        flags["IndividualBirthDateText"] = compact_date(subject["born"])
    elif person:
        flags["BirthDateUnknownIndicator"] = "Y"
    else:
        flags["PartyAsEntityOrganizationIndicator"] = "Y"
    p = b.party(CTR_PERSON, **{k: v for k, v in flags.items() if v})
    if person:
        first, last = split_name(subject["name"])
        b.name(p, last=last, first=first)
    else:
        b.name(p, last=subject["name"])
    a = subject.get("address") or {}
    b.address(p, a.get("line1", ""), a.get("city", ""), a.get("region", ""), a.get("postal_code", ""), a.get("country") or subject["country"])
    digits = _digits(subject.get("phone", ""))
    if digits:
        add(b.seq(p, "PhoneNumber"), _q("PhoneNumberText"), digits[:16])
    ids = subject.get("identifiers") or []
    tin = next((i for i in ids if i["kind"] in ("ssn", "ein", "tin")), None)
    if tin:
        b.ident(p, identity.KINDS[tin["kind"]].fincen, tin["value"])
    else:
        add(b.seq(p, "PartyIdentification"), _q("TINUnknownIndicator"), "Y")
    if subject.get("occupation"):
        add(b.seq(p, "PartyOccupationBusiness"), _q("OccupationBusinessText"), _clip(subject["occupation"], 50))
    if subject.get("email"):
        add(b.seq(p, "ElectronicAddress"), _q("ElectronicAddressText"), _clip(subject["email"], 517))
    for acct in subject["accounts"][:1]:
        for code, amount in (("8", act.get("cash_in", 0)), ("9", act.get("cash_out", 0))):  # one entry for cash in, one for cash out
            if amount:
                el = b.seq(p, "Account")
                add(el, _q("AccountNumberText"), _clip(acct["id"], 40))
                add(b.seq(el, "PartyAccountAssociation"), _q("PartyAccountAssociationTypeCode"), code)


def _institution_problems(report: dict) -> list[str]:
    inst, out = report["institution"], []
    if inst.get("country") == "US" and not (inst.get("state") and inst.get("postal_code")):
        out.append("A US filing institution needs a state and ZIP code")
    digits = _digits(inst.get("tax_id", ""))
    if inst.get("tax_id") and len(digits) != 9:
        out.append("The filing institution's EIN must have nine digits")
    return out


def check_sar(report: dict) -> list[str]:
    out = common_problems(report) + _institution_problems(report)
    text = report["narrative"].strip()
    if not text:
        out.append("A SAR needs a narrative")
    elif len(text) > NARRATIVE_PART * NARRATIVE_PARTS:
        out.append(f"The narrative is {len(text):,} characters; FinCEN takes at most {NARRATIVE_PART * NARRATIVE_PARTS:,}")
    if not report["activity"]["categories"]:
        out.append("No category of suspicious activity is recorded")
    return out


def check_ctr(report: dict) -> list[str]:
    out = common_problems(report) + _institution_problems(report)
    act = report["activity"]
    if max(act.get("cash_in", 0), act.get("cash_out", 0)) <= 10_000:
        out.append("Neither cash in nor cash out exceeds $10,000 on this day, so no CTR is owed")
    return out
