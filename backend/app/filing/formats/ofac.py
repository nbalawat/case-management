"""Reports to a sanctions authority: property blocked, and payments rejected.

US law gives ten business days to report either to OFAC (31 CFR 501.603 and 501.604). OFAC takes these through its own
reporting system and forms; this renders the same facts as a structured document for the person who enters them
there. It is not the authority's own file format.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from .common import add, pretty

TITLES = {"BLOCK": "ReportOfBlockedProperty", "REJECT": "ReportOfRejectedTransaction"}


def render(report: dict) -> str:
    root = ET.Element(TITLES[report["kind"]], {"preparedAt": report["prepared_at"], "caseId": report.get("case_id") or ""})
    inst = add(root, "ReportingInstitution")
    for k, v in report["institution"].items():
        add(inst, k.title().replace("_", ""), v)
    target = add(root, "SanctionsTarget")
    for k in ("name", "list", "reference"):
        add(target, k.capitalize(), report["target"].get(k, ""))
    for p in report["target"].get("programs", []):
        add(target, "Program", p)
    subject = add(root, "AccountParty", id=report["subject"]["id"])
    add(subject, "Name", report["subject"]["name"])
    add(subject, "Country", report["subject"]["country"])
    prop = add(root, "Property")
    for k in ("description", "kind", "identifier", "location"):
        add(prop, k.capitalize(), report["property"].get(k, ""))
    add(prop, "Value", f"{report['property']['value']:.2f}", currency=report["property"]["currency"])
    add(prop, "DateBlocked" if report["kind"] == "BLOCK" else "DateRejected", report["property"]["date"][:10])
    for t in report["transactions"]:
        x = add(root, "Transaction", id=t["id"])
        add(x, "Date", t["ts"][:10])
        add(x, "Amount", f"{t['amount']:.2f}", currency=t["currency"])
        add(x, "Direction", "incoming" if t["direction"] == "in" else "outgoing")
        add(x, "Method", t["channel"])
        add(x, "OtherParty", t["counterparty"])
        add(x, "OtherPartyCountry", t["counterparty_country"])
    add(root, "Basis", report["narrative"])
    if report.get("prior_reference"):
        add(root, "CorrectsReport", report["prior_reference"])
    return pretty(root)


def check(report: dict) -> list[str]:
    out = []
    inst = report["institution"]
    missing = [f.replace("_", " ") for f in ("name", "address", "city", "country", "contact_office", "contact_phone") if not str(inst.get(f) or "").strip()]
    if missing:
        out.append(f"The reporting institution's details are incomplete: {', '.join(missing)}. An operator sets these for the client.")
    if not report["target"].get("name"):
        out.append("No list entry is named as the sanctions target. The report must say whose property this is.")
    if report["kind"] == "REJECT" and not report["transactions"]:
        out.append("A rejected-transaction report needs the payment that was rejected")
    if report["property"]["value"] <= 0 and report["kind"] == "REJECT":
        out.append("The rejected payment has no value")
    if len(report["narrative"].strip()) < 10:
        out.append("The basis for the action is missing")
    return out
