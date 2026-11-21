"""goAML XML, the UNODC format financial intelligence units in many countries take reports in.

Written to the general goAML report layout. Each country publishes its own schema version, code lists and business
rules; none of those has been applied here, and nothing has been uploaded to a goAML test site.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from .common import add, common_problems, pretty, split_name

REASON_MAX = 4000
MODES = {"cash": "CASH", "wire": "WIRE", "ach": "EFT", "card": "CARD", "p2p": "EFT"}
INDICATORS = {"structuring": "STRUCT", "rapid_movement": "RAPID", "high_risk_geo": "HRJ", "sanctions_name": "WATCH"}


def _client(parent: ET.Element, side: str, subject: dict, account_id: str, inst: dict) -> None:
    """Our own customer's side of a transaction: the account, and the person or entity who holds it."""
    el = add(parent, f"t_{side}_my_client")
    add(el, f"{side}_funds_code", "K")
    acct = add(el, f"{side}_account")
    add(acct, "institution_name", inst.get("name", ""))
    add(acct, "account", account_id)
    if subject["kind"] == "person":
        first, last = split_name(subject["name"])
        who = add(add(acct, "signatory"), "t_person")
        add(who, "first_name", first)
        add(who, "last_name", last)
        add(who, "occupation", subject.get("occupation", ""))
    else:
        add(add(acct, "t_entity"), "name", subject["name"])
    add(el, f"{side}_country", subject["country"])


def _other(parent: ET.Element, side: str, t: dict) -> None:
    el = add(parent, f"t_{side}")
    add(el, f"{side}_funds_code", "K")
    if t["counterparty"]:
        add(add(el, f"{side}_entity"), "name", t["counterparty"])
    add(el, f"{side}_country", t["counterparty_country"])


def _render(report: dict, code: str) -> str:
    inst, subject = report["institution"], report["subject"]
    root = ET.Element("report")
    add(root, "rentity_id", inst.get("reporting_entity_id", ""))
    add(root, "submission_code", "E")
    add(root, "report_code", code)
    add(root, "entity_reference", report.get("case_id") or f"{subject['id']}-{report['activity']['from']}")
    if report.get("prior_reference"):
        add(root, "fiu_ref_number", report["prior_reference"])
    add(root, "submission_date", report["prepared_at"][:19])
    add(root, "currency_code_local", report["activity"]["currency"])
    person = add(root, "reporting_person")
    add(person, "last_name", inst.get("contact_office", ""))
    add(add(add(person, "phones"), "phone"), "tph_number", inst.get("contact_phone", ""))
    loc = add(root, "location")
    add(loc, "address", inst.get("address", ""))
    add(loc, "city", inst.get("city", ""))
    add(loc, "country_code", inst.get("country", ""))
    if code == "STR":
        add(root, "reason", report["narrative"])
    for t in report["transactions"]:
        x = add(root, "transaction")
        add(x, "transactionnumber", t["id"])
        add(x, "date_transaction", t["ts"][:19])
        add(x, "transmode_code", MODES.get(t["channel"], "OTHER"))
        add(x, "amount_local", f"{t['amount']:.2f}")
        if t["direction"] == "out":
            _client(x, "from", subject, t["account_id"], inst)
            _other(x, "to", t)
        else:
            _other(x, "from", t)
            _client(x, "to", subject, t["account_id"], inst)
    if report["activity"]["categories"]:
        ind = add(root, "report_indicators")
        for c in report["activity"]["categories"]:
            add(ind, "indicator", INDICATORS.get(c["key"], "OTHER"))
    return pretty(root)


def render_str(report: dict) -> str:
    return _render(report, "STR")


def render_ctr(report: dict) -> str:
    return _render(report, "CTR")


def _entity_problems(report: dict) -> list[str]:
    return [] if str(report["institution"].get("reporting_entity_id") or "").strip() else [
        "The reporting entity id the financial intelligence unit issued is missing. An operator sets it for the client."]


def check_str(report: dict) -> list[str]:
    out = common_problems(report) + _entity_problems(report)
    text = report["narrative"].strip()
    if not text:
        out.append("An STR needs the reason for suspicion")
    elif len(text) > REASON_MAX:
        out.append(f"The reason is {len(text):,} characters; goAML takes at most {REASON_MAX:,}")
    return out


def check_ctr(report: dict) -> list[str]:
    return common_problems(report) + _entity_problems(report)
