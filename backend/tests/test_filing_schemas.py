"""Every report the platform writes passes the regulator's own published schema.

FinCEN's batch schemas ship with the app, so those tests always run. goAML schemas are each FIU's own and may not be
redistributed; the goAML tests run when TEST_GOAML_SCHEMA_DIR points at a folder holding FIU Luxembourg's schema
(scripts/fetch-goaml-schema.sh downloads it).
"""
import copy
import os
from pathlib import Path

import pytest

from app.config import settings
from app.filing import validation
from app.filing.formats import fincen, goaml
from app.filing.formats.goaml_profiles import profile_for
from test_filing import LU, US, body

FULL = {"born": "1980-02-03", "nationality": "US", "phone": "617-555-0101", "email": "ada@example.com",
        "address": {"line1": "1 Main St", "line2": "Apt 4", "city": "Boston", "region": "MA", "postal_code": "02110", "country": "US"},
        "identifiers": [{"kind": "ssn", "value": "900-12-3456", "issuer_country": "US", "expires": None},
                        {"kind": "passport", "value": "X1234567", "issuer_country": "GB", "expires": "2030-01-01"}]}
COMPANY = {"kind": "company", "name": "Stone Holdings Ltd", "born": "2011-05-01", "nationality": "LU", "country": "LU", "occupation": "holding company",
           "address": {"line1": "5 Rue Neuve", "line2": "", "city": "Luxembourg", "region": "", "postal_code": "L-1111", "country": "LU"},
           "identifiers": [{"kind": "company_registration", "value": "B123456", "issuer_country": "LU", "expires": None},
                           {"kind": "tin", "value": "LU123456789", "issuer_country": "LU", "expires": None}]}


def report(kind="SAR", institution=US, subject=None, **kw):
    r = body(kind, institution, **kw)
    if subject is not None and "born" not in subject:
        r["subject"].pop("born")
    r["subject"] |= copy.deepcopy(subject if subject is not None else FULL)
    return r


VARIANTS = {
    "complete person": {},
    "nothing known but the name": {"subject": {}},
    "a company": {"subject": COMPANY},
    "an amendment": {"prior": "31000123456789"},
    "a closed account and a long narrative": {"subject": FULL | {"accounts": [{"id": "A1", "product": "checking", "status": "closed"}]}, "narrative": "x" * 9500},
}


def _valid(format_id, institution, doc):
    out = validation.validate(format_id, institution, doc)
    assert out.checked, out.note
    assert out.errors == [], "\n".join(out.errors)
    return out


@pytest.mark.parametrize("variant", VARIANTS, ids=list(VARIANTS))
def test_every_fincen_sar_passes_the_published_schema(variant):
    r = report(**VARIANTS[variant])
    out = _valid("fincen_sar_xml", US, fincen.render_sar(r))
    assert out.schema.startswith("FinCEN SAR batch schema 1.2")


def test_a_sar_names_several_subjects_and_every_category_once():
    r = report()
    r["other_subjects"] = [report(subject=COMPANY)["subject"] | {"id": "C2"}]
    r["activity"]["categories"] = [{"key": k, "group": "x", "label": k} for k in ("structuring", "rapid_movement", "high_risk_geo", "sanctions_name", "made_up")]
    doc = fincen.render_sar(r)
    _valid("fincen_sar_xml", US, doc)
    assert doc.count("<fc2:ActivityPartyTypeCode>33</fc2:ActivityPartyTypeCode>") == 2
    assert doc.count("<fc2:SuspiciousActivitySubtypeID>9999</fc2:SuspiciousActivitySubtypeID>") == 1  # 'other' once, however many fall into it


@pytest.mark.parametrize("variant", VARIANTS, ids=list(VARIANTS))
def test_every_fincen_ctr_passes_the_published_schema(variant):
    kw = {k: v for k, v in VARIANTS[variant].items() if k != "narrative"}
    _valid("fincen_ctr_xml", US, fincen.render_ctr(report("CTR", cash_in=27006.0, cash_out=9004.0, **kw)))
    _valid("fincen_ctr_xml", US, fincen.render_ctr(report("CTR", cash_in=27006.0, cash_out=0.0, **kw)))


def test_what_is_known_about_the_subject_reaches_the_sar():
    doc = fincen.render_sar(report())
    for text in ("<fc2:IndividualBirthDateText>19800203</fc2:IndividualBirthDateText>", "<fc2:RawStreetAddress1Text>1 Main St</fc2:RawStreetAddress1Text>",
                 "<fc2:RawZIPCode>02110</fc2:RawZIPCode>", "<fc2:PartyIdentificationNumberText>900123456</fc2:PartyIdentificationNumberText>",
                 "<fc2:ElectronicAddressText>ada@example.com</fc2:ElectronicAddressText>", "<fc2:OtherIssuerCountryText>GB</fc2:OtherIssuerCountryText>"):
        assert text in doc
    bare = fincen.render_sar(report(subject={}))
    for text in ("<fc2:BirthDateUnknownIndicator>Y</fc2:BirthDateUnknownIndicator>", "<fc2:TINUnknownIndicator>Y</fc2:TINUnknownIndicator>",
                 "<fc2:StreetAddressUnknownIndicator>Y</fc2:StreetAddressUnknownIndicator>"):
        assert text in bare  # FinCEN wants "unknown" said out loud, not left blank


def test_a_document_the_schema_refuses_is_caught_and_explained():
    doc = fincen.render_sar(report()).replace("<fc2:RawZIPCode>02110</fc2:RawZIPCode>", "<fc2:RawZIPCode>0211000000000</fc2:RawZIPCode>")
    out = validation.validate("fincen_sar_xml", US, doc)
    assert out.checked and not out.valid and "RawZIPCode" in out.errors[0]
    assert validation.problems(out)[0].startswith("The regulator's schema refuses it")


def test_a_format_without_a_schema_is_marked_unchecked_and_blocks_only_when_the_platform_insists(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.__class__, "goaml_schema_dir", tmp_path, raising=False)
    object.__setattr__(settings, "goaml_schema_dir", tmp_path)
    out = validation.validate("goaml_str_xml", LU, goaml.render_str(report("STR", LU)))
    assert not out.checked and "not installed" in out.note and validation.problems(out) == []
    object.__setattr__(settings, "filing_require_schema", True)
    try:
        assert "not installed" in validation.problems(out)[0]
    finally:
        object.__setattr__(settings, "filing_require_schema", False)
    assert not validation.validate("goaml_str_xml", LU | {"goaml_schema": "../../etc/passwd"}, "<report/>").checked


# --- goAML, when the FIU's schema is at hand ---------------------------------------------------------------------------------
GOAML_DIR = os.getenv("TEST_GOAML_SCHEMA_DIR", "")
needs_goaml = pytest.mark.skipif(not (GOAML_DIR and (Path(GOAML_DIR) / validation.DEFAULT_GOAML_FILE).is_file()),
                                 reason="set TEST_GOAML_SCHEMA_DIR to a folder holding FIU Luxembourg's goAML schema")


@pytest.fixture
def goaml_dir():
    old = settings.goaml_schema_dir
    object.__setattr__(settings, "goaml_schema_dir", Path(GOAML_DIR))
    yield
    object.__setattr__(settings, "goaml_schema_dir", old)


@needs_goaml
@pytest.mark.parametrize("variant", VARIANTS, ids=list(VARIANTS))
def test_every_goaml_str_and_cash_report_passes_the_fius_schema(goaml_dir, variant):
    if variant == "nothing known but the name":
        r = report("STR", LU, subject={})
        assert "date of birth" in goaml.check_str(r)[-1]  # goAML insists on it, and the check says so before the schema does
        assert any("birthdate" in e for e in validation.validate("goaml_str_xml", LU, goaml.render_str(r)).errors)
        return
    kw = VARIANTS[variant] | ({"narrative": "x" * 3900} if "narrative" in VARIANTS[variant] else {})
    _valid("goaml_str_xml", LU, goaml.render_str(report("STR", LU, **kw)))
    _valid("goaml_ctr_xml", LU, goaml.render_ctr(report("CTR", LU, **{k: v for k, v in kw.items() if k != "narrative"})))


@needs_goaml
def test_a_counterparty_account_is_reported_as_an_account(goaml_dir):
    r = report("STR", LU)
    for t in r["transactions"]:
        t |= {"counterparty": "Volkov Maritime Trading", "counterparty_account": "LU280019400644750000", "counterparty_institution": "BCEELULL", "purpose": "TRAD"}
    doc = goaml.render_str(r)
    _valid("goaml_str_xml", LU, doc)
    assert "<iban>LU280019400644750000</iban>" in doc and "<swift>BCEELULL</swift>" in doc


def test_an_fiu_profile_can_be_adjusted_per_client():
    p = profile_for(LU | {"goaml": {"rates": {"JPY": 0.0061}, "placeholder_bic": "ALDRLULL"}})
    assert p.rates["JPY"] == 0.0061 and p.rates["USD"] == 0.92 and p.placeholder_bic == "ALDRLULL" and p.currency == "EUR"
    r = report("STR", LU)
    r["transactions"][0]["currency"] = "SEK"
    assert any("need a rate" in x for x in goaml.check_str(r))


# --- the reports the demo writes, and answers coming back -------------------------------------------------------------------
def test_every_report_the_demo_wrote_was_checked_against_the_schema_and_passed(client):
    from test_client_api import h

    for tenant in ("meridian", "kestrel"):
        rows = client.get("/api/filings", headers=h(tenant, "maya" if tenant == "meridian" else "joy")).json()["filings"]
        fincen_rows = [r for r in rows if r["format"].startswith("fincen_")]
        assert fincen_rows, tenant
        for r in fincen_rows:
            v = r["validation"]
            assert v["checked"] and v["valid"], (tenant, r["id"], v)


def test_a_channel_that_is_not_connected_refuses_in_words():
    from app.filing.transmit import CHANNELS, TransmitError

    for cid in ("fincen_sdtm", "goaml_web_service"):
        with pytest.raises(TransmitError, match="not connected"):
            CHANNELS[cid].send(None, "xml")


def test_answers_dropped_in_the_inbox_are_recorded_once(client):
    import json

    import app.db as appdb
    from app.domain.personas import policy_of
    from app.filing import answers
    from app.ingest.locations import store_for
    from test_client_api import h

    sent = [r for r in client.get("/api/filings", headers=h("meridian", "maya")).json()["filings"] if r["status"] == "submitted"]
    assert len(sent) >= 1
    lines = [{"filing_id": sent[0]["id"], "status": "accepted", "reference": "31000999888777"},
             {"filing_id": "no-such-report", "status": "accepted", "reference": "1"}]
    if len(sent) > 1:
        lines.append({"receipt": sent[1]["receipt"], "status": "rejected", "reason": "Subject address incomplete"})
    store_for("meridian", "landing").write("regulatory/inbox/answers-1.jsonl", "\n".join(json.dumps(x) for x in lines).encode())
    r = client.post("/api/filings/answers/collect", headers=h("meridian", "maya"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["files"], out["recorded"]) == (1, len(lines) - 1) and out["problems"][0]["why"] == "no report matches this answer"
    assert client.get(f"/api/filings/{sent[0]['id']}", headers=h("meridian", "maya")).json()["reference"] == "31000999888777"
    assert client.post("/api/filings/answers/collect", headers=h("meridian", "maya")).json()["files"] == 0  # read once
    assert client.post("/api/filings/answers/collect", headers=h("meridian", "ana")).status_code == 403
    with appdb.session_scope("meridian") as s:
        assert answers.collect(s, policy_of("meridian").people["maya"])["files"] == 0
