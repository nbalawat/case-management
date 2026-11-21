"""The customer record: identifying numbers, synthetic identity, pipelines that load it, corrections and who looked."""
from datetime import date

import pytest
from sqlalchemy import func, select

from app.customers import demo, record
from app.customers import service as customers
from app.domain import identity
from app.domain import models as m
from app.ingest.datasets import DATASETS
from app.ingest import pipeline
from support import PERSONAS, T
from test_client_api import h
from test_pipelines import CUSTOMERS, MAPS, lake, s  # noqa: F401 (fixtures)

ID_MAP = {"party_id": "CUST_NO", "kind": "ID_TYP_CD", "value": "ID_NO", "issuer_country": "ISSUING_CNTRY", "expires": "EXPIRY_DT"}


# --- identifiers ---------------------------------------------------------------------------------------------------------
def test_numbers_compare_however_they_are_written_and_are_checked_by_kind():
    assert identity.normalize(" 900-12 3456 ") == identity.normalize("900123456") == "900123456"
    assert identity.check("ssn", "900-12-3456") == "900123456" and identity.check("passport", "x1234567") == "X1234567"
    for kind, value, says in [("ssn", "12-34", "nine digits"), ("ein", "12-34567890", "nine digits"), ("lei", "ABC", "twenty"),
                              ("passport", " - ", "empty"), ("library_card", "1", "unknown")]:
        with pytest.raises(identity.IdentifierError, match=says):
            identity.check(kind, value)


def test_secret_numbers_show_only_their_last_four_characters():
    assert identity.mask("ssn", "900-12-3456") == "•••••3456" and identity.mask("passport", "X1234567") == "••••4567"
    assert identity.mask("ein", "00-1234567") == "00-1234567"  # a company's tax number is not secret
    assert identity.age_on(date(1990, 6, 15), date(2026, 6, 14)) == 35 and identity.age_on(date(1990, 6, 15), date(2026, 6, 15)) == 36


def test_every_identifier_kind_has_a_fincen_code_and_a_goaml_home():
    for k in identity.KINDS.values():
        assert k.fincen in {"1", "2", "4", "5", "6", "7", "9"} and k.goaml and k.applies_to


# --- synthetic identity --------------------------------------------------------------------------------------------------
def _party(pid="P1", kind="person", country="US", **kw):
    from datetime import datetime

    blank = dict.fromkeys(("nationality", "address_line1", "address_line2", "city", "region", "postal_code", "phone", "email"), "")
    return m.Party(id=pid, tenant_id=T, kind=kind, name="Ada Maria Stone" if kind == "person" else "Stone Holdings Ltd", country=country,
                   onboarded=datetime(2020, 1, 1), segment="retail", **(blank | kw))


def test_synthetic_identity_is_complete_repeatable_and_never_a_real_number():
    a, b = _party(), _party()
    demo.enrich_party(a, T, ("rosa",), date(2026, 11, 1))
    demo.enrich_party(b, T, ("rosa",), date(2026, 11, 1))
    assert (a.born, a.address_line1, a.postal_code, [i.value for i in a.identifiers]) == (b.born, b.address_line1, b.postal_code, [i.value for i in b.identifiers])
    assert record.missing(a) == []
    ssn = next(i for i in a.identifiers if i.kind == "ssn").normalized
    assert ssn[0] == "9" and 10 <= int(ssn[3:5]) <= 49  # area 9xx with a group under 50 is never issued as an SSN or an ITIN
    firm = _party("C1", "company")
    demo.enrich_party(firm, T, ("rosa",), date(2026, 11, 1))
    assert firm.segment == "business" and firm.relationship_manager == "rosa" and firm.born < firm.onboarded.date()
    ein = next(i for i in firm.identifiers if i.kind == "ein").normalized
    assert ein.startswith("00") and all(i.normalized.startswith("0000") for i in firm.identifiers if i.kind == "lei")


def test_what_a_pipeline_or_a_person_set_is_never_overwritten():
    p = _party(address_line1="1 Real Road", born=date(1970, 1, 2), relationship_manager="someone")
    p.identifiers.append(m.PartyIdentifier(id="pid-1", tenant_id=T, kind="passport", value="Z1", normalized="Z1"))
    demo.enrich_party(p, T, ("rosa",), date(2026, 11, 1))
    assert (p.address_line1, p.born, p.relationship_manager, [i.value for i in p.identifiers]) == ("1 Real Road", date(1970, 1, 2), "someone", ["Z1"])


def test_one_firm_pays_into_one_account_however_its_name_is_spelled():
    same = {demo._account_for(n, T) for n in ("Meridian Trade Partners", "MERIDIAN TRADE PARTNERS", "Meridian Trading Partners Intl")}
    assert len(same) == 1
    assert demo._account_for("Bluepeak Holdings SA", T) != demo._account_for("Bluepeak Holding Group SA", T)  # two firms after all


def test_missing_fields_are_named_in_words():
    p = _party()
    assert record.missing(p) == ["date of birth", "street address", "city", "postal code", "nationality", "an identifying number"]
    assert record.missing(_party("C1", "company"))[0] == "date of incorporation"


# --- pipelines ----------------------------------------------------------------------------------------------------------
def test_customer_records_carry_identity_and_identifier_records_are_checked():
    c = DATASETS["customers"].transform({"CUST_NO": "C1", "CUST_NM": "Ada", "DOB": "19800229", "NATL_CD": "gb", "ADDR1": "1 Kings Road", "STATE_CD": "ma",
                                         "SEG_CD": "PB"}, MAPS["customers"] | {"born": "DOB", "nationality": "NATL_CD", "address_line1": "ADDR1",
                                                                               "region": "STATE_CD", "segment": "SEG_CD"})
    assert (c["born"], c["nationality"], c["region"], c["segment"]) == (date(1980, 2, 29), "GB", "MA", "private")
    i = DATASETS["identifiers"].transform({"CUST_NO": "C1", "ID_TYP_CD": "SSN", "ID_NO": "900-12-3456", "ISSUING_CNTRY": "us", "EXPIRY_DT": ""}, ID_MAP)
    assert (i["kind"], i["normalized"], i["issuer_country"]) == ("ssn", "900123456", "US")
    for bad in ({"CUST_NO": "C1", "ID_TYP_CD": "SSN", "ID_NO": "12-34"}, {"CUST_NO": "C1", "ID_TYP_CD": "XX", "ID_NO": "1"},
                {"CUST_NO": "C1", "ID_TYP_CD": "PP", "ID_NO": "X1", "ISSUING_CNTRY": "GBR"}):
        with pytest.raises(ValueError):
            DATASETS["identifiers"].transform(bad, ID_MAP)
    with pytest.raises(ValueError, match="not an address"):
        DATASETS["customers"].transform({"CUST_NO": "C1", "CUST_NM": "Ada", "EM": "nope"}, MAPS["customers"] | {"email": "EM"})


def test_an_identifier_feed_adds_numbers_once_and_rejects_unknown_customers(lake, s):  # noqa: F811
    lake.land("customers/c.csv", CUSTOMERS)
    pipeline.run_source(s, lake.source(s, "customers"), "omar")
    feed = "CUST_NO,ID_TYP_CD,ID_NO,ISSUING_CNTRY,EXPIRY_DT\nC1,PP,X1234567,GB,2030-01-01\nC2,SSN,900-12-3456,US,\nC9,SSN,900-12-9999,US,\n"
    lake.land("ids/day1.csv", feed)
    src = pipeline.save_source(s, {"name": "Ids", "dataset": "identifiers", "root": "landing", "prefix": "ids", "mapping": ID_MAP,
                                   "rules": {"max_reject_rate": 0.5}}, "omar")
    run = pipeline.run_source(s, src, "omar")
    assert (run.stats["loaded"], run.stats["rejected"]) == (2, 1)
    lake.land("ids/day2.csv", feed.replace("2030-01-01", "2031-01-01"))
    run = pipeline.run_source(s, src, "omar")
    assert s.scalar(select(func.count()).select_from(m.PartyIdentifier)) == 2
    passport = s.scalars(select(m.PartyIdentifier).where(m.PartyIdentifier.kind == "passport")).one()
    assert passport.expires == date(2031, 1, 1) and run.stats["updated"] == 1


def test_payment_details_are_mapped_when_the_source_has_them():
    t = DATASETS["transactions"].transform({"TXN_REF": "T1", "ACCT_NBR": "A1", "CUST_NO": "C1", "TXN_DT": "20260105120000", "TXN_AMT": "10",
                                            "DR_CR_IND": "DR", "CHNL": "WIR", "BENE": "LU28 0019 4006 4475 0000", "BIC": "BCEELULL", "PURP": "sala"},
                                           MAPS["transactions"] | {"counterparty_account": "BENE", "counterparty_institution": "BIC", "purpose": "PURP"})
    assert (t["counterparty_account"], t["counterparty_institution"], t["purpose"]) == ("LU280019400644750000", "BCEELULL", "SALA")


# --- corrections, look-ups and the record of both -----------------------------------------------------------------------
@pytest.fixture
def ada(s):  # noqa: F811
    p = _party()
    s.add(p)
    s.flush()
    return p


ANALYST, AUDITOR = PERSONAS["kai"], next(p for p in PERSONAS.values() if p.read_only)


def test_a_correction_records_only_what_changed_with_the_reason(s, ada):  # noqa: F811
    out = customers.update(s, "P1", {"born": "1981-03-04", "city": "Boston", "region": "ma", "segment": "private"}, ANALYST, "New passport seen")
    assert (out["born"], out["address"]["region"], out["segment"]) == ("1981-03-04", "MA", "private")
    customers.update(s, "P1", {"city": "Boston"}, ANALYST)  # no change, no record
    events = customers.history(s, "P1")
    assert len(events) == 1 and events[0]["by"] == ANALYST.id and events[0]["detail"]["reason"] == "New passport seen"
    assert events[0]["detail"]["changes"]["born"] == {"from": None, "to": "1981-03-04"}
    for bad, says in [({"name": "Someone Else"}, "cannot be changed"), ({"born": "2999-01-01"}, "cannot be right"), ({"born": "04/03/1981"}, "YYYY-MM-DD"),
                      ({"nationality": "GBR"}, "two-letter"), ({"segment": "vip"}, "Segment"), ({"email": "x"}, "email")]:
        with pytest.raises(customers.CustomerError, match=says):
            customers.update(s, "P1", bad, ANALYST)
    with pytest.raises(customers.CustomerError, match="cannot change"):
        customers.update(s, "P1", {"city": "Paris"}, AUDITOR)


def test_numbers_are_added_once_masked_when_shown_and_removed_only_with_a_reason(s, ada):  # noqa: F811
    out = customers.add_identifier(s, "P1", "ssn", "900-12-3456", ANALYST, "US")
    assert out["identifiers"][0]["value"] == "•••••3456" and out["identifiers"][0]["masked"]
    with pytest.raises(customers.CustomerError, match="already has"):
        customers.add_identifier(s, "P1", "ssn", "900 12 3456", ANALYST)
    with pytest.raises(customers.CustomerError, match="does not apply"):
        customers.add_identifier(s, "P1", "ein", "00-1234567", ANALYST)
    with pytest.raises(customers.CustomerError, match="Say why"):
        customers.remove_identifier(s, "P1", "ssn", "•••••3456", ANALYST, "")
    assert customers.remove_identifier(s, "P1", "ssn", "•••••3456", ANALYST, "Typed against the wrong customer")["identifiers"] == []
    assert [e["kind"] for e in customers.history(s, "P1")] == ["identifier.removed", "identifier.added"]
    assert all("3456" in e["detail"]["value"] and "900" not in e["detail"]["value"] for e in customers.history(s, "P1"))  # the record is masked too


def test_seeing_the_full_numbers_needs_a_reason_and_leaves_a_trace(s, ada):  # noqa: F811
    customers.add_identifier(s, "P1", "passport", "X1234567", ANALYST, "GB")
    with pytest.raises(customers.CustomerError, match="Say why"):
        customers.reveal(s, "P1", ANALYST, " ")
    with pytest.raises(customers.CustomerError, match="cannot see"):
        customers.reveal(s, "P1", AUDITOR, "Audit sample")
    assert customers.reveal(s, "P1", ANALYST, "Completing the SAR")["identifiers"][0]["value"] == "X1234567"
    assert customers.history(s, "P1")[0]["kind"] == "identifiers.revealed"


# --- over HTTP, on the seeded demo ----------------------------------------------------------------------------------------
def test_the_seeded_customer_profile_shows_a_complete_masked_identity(client):
    profile = client.get("/api/customers/P10001", headers=h(T)).json()
    who = profile["party"]["identity"]
    assert who["born"] and who["address"]["one_line"] and who["missing"] == [] and who["booking_jurisdiction"] in ("US", "CA")  # Meridian books in the US, and a quarter in Canada
    assert any(i["kind"] == "ssn" and i["value"].startswith("•") for i in who["identifiers"])
    r = client.post("/api/customers/P10001/identifiers/reveal", json={"reason": "Checking a filing"}, headers=h(T, "kai"))
    assert r.status_code == 200 and not any(i["value"].startswith("•") for i in r.json()["identifiers"])
    assert client.get("/api/customers/P10001/history", headers=h(T)).json()["events"][0]["by"] == "kai"
    assert client.post("/api/customers/P10001/identity", json={"changes": {"born": "soon"}}, headers=h(T, "kai")).status_code == 400
    assert client.post("/api/customers/NOPE/identity", json={"changes": {}}, headers=h(T, "kai")).status_code == 404
