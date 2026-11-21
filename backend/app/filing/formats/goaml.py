"""goAML XML, the UNODC format financial intelligence units in many countries take reports in.

goAML is one format with many national variants: each FIU publishes its own schema (which elements it allows, which
it forbids, its local currency, its business rules as XSD 1.1 asserts) and its own lookup tables for the coded
fields. So the renderer takes an FIU profile (goaml_profiles.py) for everything that differs, and every document is
validated against the schema the client's FIU publishes (app/filing/validation.py).

Built and validated against the schema FIU Luxembourg (CRF) publishes, goAML 5.2 / schema 5.0.2 of 2026-10-19. The
coded values in the profile follow goAML's usual lookup tables and must be confirmed against the FIU's own lists;
nothing has been uploaded to a goAML test site.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from ...domain import identity
from .common import add, common_problems, pretty, split_name
from .goaml_profiles import Profile, profile_for

REASON_MAX = 4000


def _clip(text: object, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


def _amount(t: dict, prof: Profile) -> float | None:
    """The amount in the FIU's local currency, or None when there is no rate to convert it with."""
    if t["currency"] == prof.currency:
        return t["amount"]
    rate = prof.rates.get(t["currency"])
    return round(t["amount"] * rate, 2) if rate else None


def _phone(parent: ET.Element, number: str, prof: Profile, *, business: bool) -> None:
    if not number:
        return
    ph = add(add(parent, "phones"), "phone")
    add(ph, "tph_contact_type", prof.contact_business if business else prof.contact_private)
    add(ph, "tph_communication_type", prof.communication_landline)
    add(ph, "tph_number", _clip(number, 50))


def _address(parent: ET.Element, a: dict, country: str, prof: Profile, *, business: bool, wrap: bool = True) -> None:
    if not (a.get("line1") or a.get("city")):
        return
    el = add(add(parent, "addresses"), "address") if wrap else parent
    add(el, "address_type", prof.contact_business if business else prof.contact_private)
    add(el, "address", _clip(" ".join(x for x in (a.get("line1"), a.get("line2")) if x), 100) or "-")
    add(el, "city", _clip(a.get("city") or "-", 255))
    if a.get("postal_code"):
        add(el, "zip", _clip(a["postal_code"], 10))
    add(el, "country_code", (a.get("country") or country)[:2])
    if a.get("region"):
        add(el, "state", _clip(a["region"], 255))


def _person(parent: ET.Element, subject: dict, prof: Profile) -> None:
    """One of our own customers, a person, with what the FIU asks to identify them."""
    first, last = split_name(subject["name"])
    add(parent, "first_name", _clip(first or "-", 100))
    add(parent, "last_name", _clip(last, 100))
    if subject.get("born"):
        add(parent, "birthdate", f"{subject['born'][:10]}T00:00:00")
    ids = subject.get("identifiers") or []
    ssn = next((i for i in ids if i["kind"] == "ssn"), None)
    if ssn:
        add(parent, "ssn", identity.normalize(ssn["value"])[:25])
    passport = next((i for i in ids if i["kind"] == "passport"), None)
    if passport:
        add(parent, "passport_number", _clip(passport["value"], 25))
        if passport.get("issuer_country"):
            add(parent, "passport_country", passport["issuer_country"])
    if subject.get("nationality"):
        add(parent, "nationality1", subject["nationality"])
    add(parent, "residence", subject["country"])
    _phone(parent, subject.get("phone", ""), prof, business=False)
    _address(parent, subject.get("address") or {}, subject["country"], prof, business=False)
    if subject.get("email"):
        add(parent, "email", _clip(subject["email"], 255))
    if subject.get("occupation"):
        add(parent, "occupation", _clip(subject["occupation"], 255))
    other = [i for i in ids if i["kind"] in prof.id_types]
    if other:
        doc = add(parent, "identification")
        i = other[0]
        add(doc, "type", prof.id_types[i["kind"]])
        add(doc, "number", _clip(i["value"], 255))
        if i.get("expires"):
            add(doc, "expiry_date", f"{i['expires'][:10]}T00:00:00")
        add(doc, "issue_country", i.get("issuer_country") or subject["country"])
    tax = next((i for i in ids if i["kind"] in ("tin", "ssn")), None)
    if tax:
        add(parent, "tax_number", _clip(tax["value"], 100))


def _entity(parent: ET.Element, subject: dict, prof: Profile, *, mine: bool) -> None:
    add(parent, "name", _clip(subject["name"], 255))
    ids = subject.get("identifiers") or []
    reg = next((i for i in ids if i["kind"] == "company_registration"), None)
    if reg:
        add(parent, "incorporation_number", _clip(reg["value"], 50))
    if subject.get("occupation"):
        add(parent, "business", _clip(subject["occupation"], 255))
    _phone(parent, subject.get("phone", ""), prof, business=True)
    _address(parent, subject.get("address") or {}, subject["country"], prof, business=True)
    if subject.get("email"):
        add(parent, "email", _clip(subject["email"], 255))
    add(parent, "incorporation_country_code", subject.get("nationality") or subject["country"])
    if subject.get("born"):
        add(parent, "incorporation_date", f"{subject['born'][:10]}T00:00:00")
    tax = next((i for i in ids if i["kind"] in ("ein", "tin")), None)
    if tax:
        add(parent, "tax_number", _clip(tax["value"], 100))


def _my_account(parent: ET.Element, tag: str, subject: dict, account_id: str, inst: dict, prof: Profile) -> None:
    acct = add(parent, tag)
    add(acct, "institution_name", _clip(inst.get("name", ""), 255))
    add(acct, "swift", inst.get("swift") or prof.placeholder_bic)
    add(acct, "account", _clip(account_id, 50))
    add(acct, "currency_code", prof.currency)
    add(acct, "account_name", _clip(subject["name"], 255))
    add(acct, "client_number", _clip(subject["id"], 30))
    add(acct, "personal_account_type", prof.account_type_current)
    if subject["kind"] == "person":
        sig = add(acct, "signatory")
        add(sig, "is_primary", "true")
        _person(add(sig, "t_person"), subject, prof)
        add(sig, "role", prof.role_holder)
    else:
        _entity(add(acct, "t_entity"), subject, prof, mine=True)
    status = next((a["status"] for a in subject["accounts"] if a["id"] == account_id), "open")
    add(acct, "balance", "0")  # not held by this platform; FIUs that require it take a balance from the core system
    add(acct, "status_code", prof.status_closed if status == "closed" else prof.status_active)


def _their_side(parent: ET.Element, side: str, t: dict, prof: Profile) -> None:
    """The counterparty, who is not our customer: an account when we know it, otherwise just a name."""
    if t.get("counterparty_account"):
        acct = add(parent, f"{side}_account")
        add(acct, "institution_name", _clip(t.get("counterparty_institution") or "Unknown", 255))
        bank = t.get("counterparty_institution") or ""
        add(acct, "swift" if len(bank) in (8, 11) and bank.isalnum() else "institution_code", _clip(bank or "UNKNOWN", 11 if len(bank) in (8, 11) else 50))
        add(acct, "account", _clip(t["counterparty_account"], 50))
        add(acct, "currency_code", t["currency"])
        add(acct, "account_name", _clip(t["counterparty"] or "Unknown", 255))
        if t["counterparty_account"][:2].isalpha() and len(t["counterparty_account"]) > 14:
            add(acct, "iban", t["counterparty_account"][:34])
        add(acct, "balance", "0")
        add(acct, "status_code", prof.status_active)
    else:
        ent = add(parent, f"{side}_entity")
        add(ent, "name", _clip(t["counterparty"] or "Unknown", 255))
        add(ent, "incorporation_country_code", t["counterparty_country"] or "ZZ")


def _render(report: dict, code: str) -> str:
    inst, subject = report["institution"], report["subject"]
    prof = profile_for(inst)
    root = ET.Element("report")
    add(root, "rentity_id", inst.get("reporting_entity_id", "") or "0")
    add(root, "submission_code", "E")
    add(root, "report_code", code)
    add(root, "entity_reference", _clip(report.get("case_id") or f"{subject['id']}-{report['activity']['from']}", 255))
    if report.get("prior_reference"):
        add(root, "fiu_ref_number", _clip(report["prior_reference"], 255))
    add(root, "submission_date", report["prepared_at"][:19])
    add(root, "currency_code_local", prof.currency)
    person = add(root, "reporting_person")
    first, last = split_name(inst.get("contact_name") or inst.get("contact_office", ""))
    add(person, "first_name", _clip(first or inst.get("contact_office", "-"), 100))
    add(person, "last_name", _clip(last or "-", 100))
    _phone(person, inst.get("contact_phone", ""), prof, business=True)
    if code in prof.reason_kinds:
        add(root, "reason", report["narrative"][:REASON_MAX] or _default_reason(report, code))
    if report.get("action"):
        add(root, "action", _clip(report["action"], 4000))
    for t in report["transactions"]:
        local = _amount(t, prof)
        x = add(root, "transaction")
        add(x, "transactionnumber", _clip(t["id"], 100))
        add(x, "internal_ref_number", _clip(t["id"], 100))
        if t.get("purpose"):
            add(x, "transaction_description", f"Purpose code {t['purpose']}")
        add(x, "date_transaction", t["ts"][:19])
        add(x, "transmode_code", prof.transmode.get(t["channel"], prof.transmode_other))
        add(x, "amount_local", f"{local if local is not None else t['amount']:.2f}")
        # Money out leaves our customer for the counterparty; money in comes the other way. Schema order is always from, then to.
        for side_tag in (("t_from_my_client", "t_to") if t["direction"] == "out" else ("t_from", "t_to_my_client")):
            side = "from" if side_tag.startswith("t_from") else "to"
            el = add(x, side_tag)
            add(el, f"{side}_funds_code", prof.funds.get(t["channel"], prof.funds_other))
            if t["currency"] != prof.currency:
                fx = add(el, f"{side}_foreign_currency")
                add(fx, "foreign_currency_code", t["currency"])
                add(fx, "foreign_amount", f"{t['amount']:.2f}")
                add(fx, "foreign_exchange_rate", f"{prof.rates.get(t['currency'], 0):.6f}")
            if side_tag.endswith("my_client") and subject["kind"] != "person":
                # A company's account must name the people who sign for it; until those are held, the company itself is the party.
                _entity(add(el, f"{side}_entity"), subject, prof, mine=True)
                add(el, f"{side}_country", inst.get("country") or subject["country"])
            elif side_tag.endswith("my_client"):
                _my_account(el, f"{side}_account", subject, t["account_id"], inst, prof)
                add(el, f"{side}_country", inst.get("country") or subject["country"])
            else:
                _their_side(el, side, t, prof)
                add(el, f"{side}_country", t["counterparty_country"] or "ZZ")
    indicators = [prof.indicators.get(c["key"], prof.indicator_other) for c in report["activity"]["categories"]]
    if indicators:
        ind = add(root, "report_indicators")
        for code_ in dict.fromkeys(indicators):  # each once, in the order found
            add(ind, "indicator", code_)
    return pretty(root)


def _default_reason(report: dict, code: str) -> str:
    act = report["activity"]
    if code == "STR":
        return "-"
    return (f"Cash transactions of {act['currency']} {act['total']:,.2f} in total on {act['from']}, above the reporting threshold "
            f"({act.get('count', len(report['transactions']))} movements).")


def render_str(report: dict) -> str:
    return _render(report, "STR")


def render_ctr(report: dict) -> str:
    return _render(report, profile_for(report["institution"]).cash_report_code)


def _entity_problems(report: dict) -> list[str]:
    out = []
    if not str(report["institution"].get("reporting_entity_id") or "").strip():
        out.append("The reporting entity id the financial intelligence unit issued is missing. An operator sets it for the client.")
    prof = profile_for(report["institution"])
    unconverted = sorted({t["currency"] for t in report["transactions"] if _amount(t, prof) is None})
    if unconverted:
        out.append(f"Amounts in {', '.join(unconverted)} need a rate to {prof.currency}; the FIU takes amounts in {prof.currency} only. "
                   "An operator sets rates in the client's goAML profile.")
    return out


def check_str(report: dict) -> list[str]:
    out = common_problems(report) + _entity_problems(report) + _subject_problems(report)
    text = report["narrative"].strip()
    if not text:
        out.append("An STR needs the reason for suspicion")
    elif len(text) > REASON_MAX:
        out.append(f"The reason is {len(text):,} characters; goAML takes at most {REASON_MAX:,}")
    return out


def check_ctr(report: dict) -> list[str]:
    return common_problems(report) + _entity_problems(report) + _subject_problems(report)


def _subject_problems(report: dict) -> list[str]:
    s = report["subject"]
    if s["kind"] == "person" and not s.get("born"):
        return ["goAML needs our customer's date of birth. Add it on the customer's record, then rebuild the report."]
    return []
