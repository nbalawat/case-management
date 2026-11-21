"""FinCEN BSA XML: the batch layout used for discrete and batch e-filing of SARs and CTRs.

Written to the published element layout. It has not been validated against FinCEN's schema files or accepted by the
BSA E-Filing test system, and the activity code table below must be confirmed against the current code list first.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from .common import add, common_problems, compact_date, pretty, split_name

NS = "www.fincen.gov/base"
NARRATIVE_PART, NARRATIVE_PARTS = 4000, 5
# Party roles in the batch.
TRANSMITTER, TRANSMITTER_CONTACT, FILER, WHERE_IT_HAPPENED, SUBJECT, CONTACT_OFFICE, CTR_PERSON = "35", "37", "30", "34", "33", "8", "50"
# (type, subtype) per category of our own. To be confirmed against FinCEN's current list before live use.
ACTIVITY_CODES = {"structuring": ("11", "111"), "rapid_movement": ("8", "820"), "high_risk_geo": ("8", "804"),
                  "sanctions_name": ("9", "9999")}
OTHER = ("9", "9999")
ET.register_namespace("fc2", NS)


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


class _Batch:
    """Every element FinCEN counts carries a sequence number that runs through the whole file."""

    def __init__(self, form: str, report: dict):
        self.n = 0
        self.root = ET.Element(_q("EFilingBatchXML"))
        add(self.root, _q("FormTypeCode"), form)
        self.activity = self.seq(self.root, "Activity")
        if report.get("prior_reference"):
            add(self.activity, _q("EFilingPriorDocumentNumber"), report["prior_reference"])
        add(self.activity, _q("FilingDateText"), compact_date(report["prepared_at"]))
        assoc = self.seq(self.activity, "ActivityAssociation")
        add(assoc, _q("CorrectsAmendsPriorReportIndicator" if report.get("prior_reference") else "InitialReportIndicator"), "Y")
        self.parties = 0

    def seq(self, parent: ET.Element, tag: str) -> ET.Element:
        self.n += 1
        return add(parent, _q(tag), SeqNum=self.n)

    def institution(self, role: str, inst: dict) -> None:
        p = self.party(role)
        name = self.seq(p, "PartyName")
        add(name, _q("PartyNameTypeCode"), "L")
        add(name, _q("RawPartyFullName"), inst.get("name", ""))
        addr = self.seq(p, "Address")
        add(addr, _q("RawCityText"), inst.get("city", ""))
        add(addr, _q("RawCountryCodeText"), inst.get("country", ""))
        add(addr, _q("RawStateCodeText"), inst.get("state", ""))
        add(addr, _q("RawStreetAddress1Text"), inst.get("address", ""))
        add(addr, _q("RawZIPCode"), inst.get("postal_code", ""))
        ident = self.seq(p, "PartyIdentification")
        add(ident, _q("PartyIdentificationNumberText"), "".join(c for c in inst.get("tax_id", "") if c.isalnum()))
        add(ident, _q("PartyIdentificationTypeCode"), "2")  # employer identification number
        if role == FILER and inst.get("regulator_code"):
            add(p, _q("PrimaryRegulatorTypeCode"), inst["regulator_code"])

    def contact(self, role: str, inst: dict) -> None:
        p = self.party(role)
        name = self.seq(p, "PartyName")
        add(name, _q("PartyNameTypeCode"), "L")
        add(name, _q("RawPartyFullName"), inst.get("contact_office", ""))
        phone = self.seq(p, "PhoneNumber")
        add(phone, _q("PhoneNumberText"), "".join(c for c in inst.get("contact_phone", "") if c.isdigit()))

    def person(self, role: str, subject: dict) -> None:
        p = self.party(role)
        name = self.seq(p, "PartyName")
        add(name, _q("PartyNameTypeCode"), "L")
        if subject["kind"] == "person":
            first, last = split_name(subject["name"])
            add(name, _q("RawEntityIndividualLastName"), last)
            add(name, _q("RawIndividualFirstName"), first)
        else:
            add(name, _q("RawEntityIndividualLastName"), subject["name"])
        addr = self.seq(p, "Address")
        add(addr, _q("CityUnknownIndicator"), "Y")  # this build holds no customer address
        add(addr, _q("RawCountryCodeText"), subject["country"])
        if subject.get("occupation"):
            occ = self.seq(p, "PartyOccupationBusiness")
            add(occ, _q("OccupationBusinessText"), subject["occupation"])
        for a in subject["accounts"]:
            acct = self.seq(p, "PartyAccountAssociation")
            add(acct, _q("PartyAccountAssociationTypeCode"), "7")
            holder = self.seq(acct, "Party")
            add(holder, _q("ActivityPartyTypeCode"), "41")
            number = self.seq(holder, "Account")
            add(number, _q("AccountNumberText"), a["id"])
            add(number, _q("AccountClosedIndicator"), "Y" if a["status"] == "closed" else "")

    def party(self, role: str) -> ET.Element:
        self.parties += 1
        p = self.seq(self.activity, "Party")
        add(p, _q("ActivityPartyTypeCode"), role)
        return p

    def finish(self, total: float, **counts: int) -> str:
        self.root.set("ActivityCount", "1")
        self.root.set("TotalAmount", str(round(total)))
        self.root.set("PartyCount", str(self.parties))
        for k, v in counts.items():
            self.root.set(k, str(v))
        return pretty(self.root)


def render_sar(report: dict) -> str:
    inst, act = report["institution"], report["activity"]
    b = _Batch("SARX", report)
    b.institution(TRANSMITTER, inst)
    b.contact(TRANSMITTER_CONTACT, inst)
    b.institution(FILER, inst)
    b.contact(CONTACT_OFFICE, inst)
    b.institution(WHERE_IT_HAPPENED, inst)
    b.person(SUBJECT, report["subject"])
    sa = b.seq(b.activity, "SuspiciousActivity")
    add(sa, _q("SuspiciousActivityFromDateText"), compact_date(act["from"]))
    add(sa, _q("SuspiciousActivityToDateText"), compact_date(act["to"]))
    add(sa, _q("TotalSuspiciousAmountText"), round(act["total"]))  # whole dollars
    for c in act["categories"]:
        kind, sub = ACTIVITY_CODES.get(c["key"], OTHER)
        cl = b.seq(sa, "SuspiciousActivityClassification")
        add(cl, _q("SuspiciousActivitySubtypeID"), sub)
        add(cl, _q("SuspiciousActivityTypeID"), kind)
        if (kind, sub) == OTHER:
            add(cl, _q("OtherSuspiciousActivityTypeText"), c["label"][:50])
    text = report["narrative"]
    parts = [text[i:i + NARRATIVE_PART] for i in range(0, len(text), NARRATIVE_PART)][:NARRATIVE_PARTS]
    for n, part in enumerate(parts, 1):
        nar = b.seq(b.activity, "ActivityNarrativeInformation")
        add(nar, _q("ActivityNarrativeSequenceNumber"), n)
        add(nar, _q("ActivityNarrativeText"), part)
    return b.finish(act["total"], ActivityNarrativeInformationCount=len(parts), SuspiciousActivityClassificationCount=len(act["categories"]))


def render_ctr(report: dict) -> str:
    inst, act = report["institution"], report["activity"]
    b = _Batch("CTRX", report)
    b.institution(TRANSMITTER, inst)
    b.contact(TRANSMITTER_CONTACT, inst)
    b.institution(FILER, inst)
    b.contact(CONTACT_OFFICE, inst)
    b.institution(WHERE_IT_HAPPENED, inst)
    b.person(CTR_PERSON, report["subject"])
    cta = b.seq(b.activity, "CurrencyTransactionActivity")
    add(cta, _q("TotalCashInReceiveAmountText"), round(act.get("cash_in", 0)))
    add(cta, _q("TotalCashOutAmountText"), round(act.get("cash_out", 0)))
    add(cta, _q("TransactionDateText"), compact_date(act["from"]))
    for t in report["transactions"]:
        d = b.seq(cta, "CurrencyTransactionActivityDetail")
        add(d, _q("CurrencyTransactionActivityDetailTypeCode"), "55" if t["direction"] == "in" else "34")  # deposit, withdrawal
        add(d, _q("DetailTransactionAmountText"), round(t["amount"]))
    return b.finish(act["total"], CurrencyTransactionActivityCount=1, CurrencyTransactionActivityDetailCount=len(report["transactions"]))


def _institution_problems(report: dict) -> list[str]:
    inst, out = report["institution"], []
    if inst.get("country") == "US" and not (inst.get("state") and inst.get("postal_code")):
        out.append("A US filing institution needs a state and ZIP code")
    digits = "".join(c for c in inst.get("tax_id", "") if c.isdigit())
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
