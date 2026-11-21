"""Regulatory reports: the formats, the checks before sending, the life of a report, the lock, and who may do what."""
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import select

from app.cases.service import TransitionError, complete_stage_tasks, save_narrative, transition
from app.domain import models as m
from app.filing import report as reports
from app.filing import service as filing
from app.filing.formats import FORMATS, fincen, format_for, goaml
from app.tenants import registry
from support import PERSONAS, T
from test_client_api import CLIENTS, EVERYONE, OP, _permissions, h
from test_core import _case

FC = {"fc2": fincen.NS}
US = {"name": "Meridian Retail Bank", "tax_id": "12-3456789", "address": "100 Harbor Street", "city": "Boston", "state": "MA",
      "postal_code": "02110", "country": "US", "contact_office": "FIU", "contact_phone": "617-555-0142"}
LU = {"name": "Alder Private Bank SA", "tax_id": "LU12345678", "reporting_entity_id": "4471", "address": "12 Rue de l'Eau", "city": "Luxembourg",
      "country": "LU", "contact_office": "Compliance", "contact_phone": "+352 555 0117"}


def body(kind="SAR", institution=US, narrative="Cash deposits were kept under the threshold on four days.", prior="", **activity):
    txns = [{"id": f"t{i}", "ts": f"2026-01-0{i}T10:00:00", "amount": 9000.0 + i, "currency": "USD", "direction": "in" if i < 4 else "out",
             "channel": "cash", "counterparty": "" if i < 4 else "Volkov Maritime Trading", "counterparty_country": "RU", "account_id": "A1"} for i in (1, 2, 3, 4)]
    return {"kind": kind, "case_id": "case-1", "prepared_at": "2026-02-01T09:30:00", "institution": dict(institution),
            "subject": {"id": "C1", "kind": "person", "name": "Ada Maria Stone", "country": "US", "occupation": "florist", "born": "1980-02-03",
                        "accounts": [{"id": "A1", "product": "checking", "status": "open"}]},
            "activity": {"from": "2026-01-01", "to": "2026-01-04", "total": 36010.0, "currency": "USD", "count": 4, "channels": ["cash"],
                         "categories": [{"key": "structuring", "group": "structuring", "label": "Below threshold"}]} | activity,
            "transactions": txns, "transactions_omitted": 0, "narrative": narrative, "prior_reference": prior}


# --- formats -----------------------------------------------------------------------------------------------------------
def test_a_fincen_sar_is_well_formed_and_says_what_the_report_says():
    root = ET.fromstring(fincen.render_sar(body()).encode())
    assert root.tag == f"{{{fincen.NS}}}EFilingBatchXML" and root.findtext("fc2:FormTypeCode", namespaces=FC) == "SARX"
    assert root.attrib == {"ActivityCount": "1", "TotalAmount": "36010", "PartyCount": "6", "ActivityAttachmentCount": "0", "AttachmentCount": "0"}
    act = root.find("fc2:Activity", FC)
    assert act.findtext("fc2:FilingDateText", namespaces=FC) == "20260201"
    assert act.find("fc2:ActivityAssociation/fc2:InitialReportIndicator", FC).text == "Y"
    roles = [p.findtext("fc2:ActivityPartyTypeCode", namespaces=FC) for p in act.findall("fc2:Party", FC)]
    assert roles == ["35", "37", "30", "8", "34", "33"]  # transmitter, its contact, filer, contact office, where it happened, subject
    subject = act.findall("fc2:Party", FC)[-1]
    assert subject.findtext("fc2:PartyName/fc2:RawEntityIndividualLastName", namespaces=FC) == "Stone"
    assert subject.findtext("fc2:PartyName/fc2:RawIndividualFirstName", namespaces=FC) == "Ada Maria"
    assert subject.findtext(".//fc2:AccountNumberText", namespaces=FC) == "A1"
    filer = act.findall("fc2:Party", FC)[2]
    assert filer.findtext("fc2:PartyIdentification/fc2:PartyIdentificationNumberText", namespaces=FC) == "123456789"  # no dash
    sa = act.find("fc2:SuspiciousActivity", FC)
    assert (sa.findtext("fc2:SuspiciousActivityFromDateText", namespaces=FC), sa.findtext("fc2:SuspiciousActivityToDateText", namespaces=FC),
            sa.findtext("fc2:TotalSuspiciousAmountText", namespaces=FC)) == ("20260101", "20260104", "36010")
    assert sa.findtext("fc2:SuspiciousActivityClassification/fc2:SuspiciousActivityTypeID", namespaces=FC) == "1"
    assert sa.findtext("fc2:SuspiciousActivityClassification/fc2:SuspiciousActivitySubtypeID", namespaces=FC) == "114"  # below the CTR threshold
    assert act.findtext("fc2:ActivityNarrativeInformation/fc2:ActivityNarrativeText", namespaces=FC).startswith("Cash deposits")


def test_sequence_numbers_run_through_the_whole_file_without_gaps_or_repeats():
    for doc in (fincen.render_sar(body()), fincen.render_ctr(body("CTR", cash_in=27006.0, cash_out=9004.0))):
        seq = [int(e.attrib["SeqNum"]) for e in ET.fromstring(doc.encode()).iter() if "SeqNum" in e.attrib]
        assert seq == list(range(1, len(seq) + 1)) and len(seq) > 15


def test_a_long_narrative_is_split_into_parts_and_an_amendment_names_the_original():
    doc = ET.fromstring(fincen.render_sar(body(narrative="x" * 9000, prior="31000123456789")).encode())
    parts = doc.findall(".//fc2:ActivityNarrativeInformation", FC)
    assert [len(p.findtext("fc2:ActivityNarrativeText", namespaces=FC)) for p in parts] == [4000, 4000, 1000]
    assert [p.findtext("fc2:ActivityNarrativeSequenceNumber", namespaces=FC) for p in parts] == ["1", "2", "3"]
    assert doc.findtext(".//fc2:EFilingPriorDocumentNumber", namespaces=FC) == "31000123456789"
    assert doc.find(".//fc2:CorrectsAmendsPriorReportIndicator", FC).text == "Y" and doc.find(".//fc2:InitialReportIndicator", FC) is None


def test_a_fincen_ctr_totals_cash_in_and_out_and_lists_each_movement():
    root = ET.fromstring(fincen.render_ctr(body("CTR", cash_in=27006.0, cash_out=9004.0, **{"to": "2026-01-01"})).encode())
    assert root.findtext("fc2:FormTypeCode", namespaces=FC) == "CTRX"
    cta = root.find(".//fc2:CurrencyTransactionActivity", FC)
    assert (cta.findtext("fc2:TotalCashInReceiveAmountText", namespaces=FC), cta.findtext("fc2:TotalCashOutAmountText", namespaces=FC)) == ("27006", "9004")
    kinds = [d.findtext("fc2:CurrencyTransactionActivityDetailTypeCode", namespaces=FC) for d in cta.findall("fc2:CurrencyTransactionActivityDetail", FC)]
    amounts = [d.findtext("fc2:DetailTransactionAmountText", namespaces=FC) for d in cta.findall("fc2:CurrencyTransactionActivityDetail", FC)]
    assert (kinds, amounts) == (["55", "56"], ["27006", "9004"])  # deposits and withdrawals, each totalled for the day
    assert [p.findtext("fc2:ActivityPartyTypeCode", namespaces=FC) for p in root.findall(".//fc2:Activity/fc2:Party", FC)][-1] == "50"


def test_a_goaml_str_puts_our_customer_on_the_right_side_of_each_transaction():
    root = ET.fromstring(goaml.render_str(body("STR", LU)).encode())
    assert (root.tag, root.findtext("rentity_id"), root.findtext("report_code"), root.findtext("entity_reference")) == ("report", "4471", "STR", "case-1")
    assert root.findtext("reason").startswith("Cash deposits") and root.findtext("currency_code_local") == "EUR"
    txns = root.findall("transaction")
    assert len(txns) == 4 and txns[0].findtext("amount_local") == "8280.92" and txns[0].findtext("transmode_code") == "A"
    assert txns[0].findtext("t_from/from_foreign_currency/foreign_amount") == "9001.00"  # dollars, converted to the FIU's euros at the profile's rate
    assert txns[0].find("t_to_my_client") is not None and txns[0].find("t_from") is not None  # money in: the customer receives
    assert txns[3].find("t_from_my_client") is not None and txns[3].findtext("t_to/to_entity/name") == "Volkov Maritime Trading"
    assert txns[3].findtext("t_from_my_client/from_account/signatory/t_person/last_name") == "Stone"
    assert [i.text for i in root.findall("report_indicators/indicator")] == ["002TS-TxnSmurfing"]
    assert ET.fromstring(goaml.render_ctr(body("CTR", LU)).encode()).findtext("reason")  # the CRF wants a reason on cash reports too (REJ-REA02)


def test_text_that_looks_like_markup_is_escaped_not_injected():
    nasty = '</fc2:ActivityNarrativeText><fc2:Injected>1</fc2:Injected> & "quotes" <script>'
    for doc, find in ((fincen.render_sar(body(narrative=nasty)), ".//fc2:ActivityNarrativeText"), (goaml.render_str(body("STR", LU, narrative=nasty)), "reason")):
        root = ET.fromstring(doc.encode())
        assert root.find(find, FC).text == nasty and root.find(".//fc2:Injected", FC) is None


@pytest.mark.parametrize("change,problem", [
    ({"institution": {}}, "institution's details are incomplete: name, tax id, address, city, country, contact office, contact phone"),
    ({"institution": US | {"state": ""}}, "needs a state and ZIP code"),
    ({"institution": US | {"tax_id": "12-345"}}, "EIN must have nine digits"),
    ({"narrative": "  "}, "A SAR needs a narrative"),
    ({"narrative": "x" * 20001}, "FinCEN takes at most 20,000"),
])
def test_a_sar_that_fincen_would_refuse_is_caught_first(change, problem):
    assert fincen.check_sar(body()) == []
    out = fincen.check_sar(body(**change))
    assert len(out) == 1 and problem in out[0]


def test_other_checks_before_sending():
    empty = body() | {"transactions": []}
    assert "No transactions are attached to the report" in fincen.check_sar(empty)
    none = body()
    none["activity"]["categories"] = []
    assert fincen.check_sar(none) == ["No category of suspicious activity is recorded"]
    assert "no CTR is owed" in fincen.check_ctr(body("CTR", cash_in=9000.0, cash_out=100.0))[0] and fincen.check_ctr(body("CTR", cash_in=10000.01, cash_out=0)) == []
    assert goaml.check_str(body("STR", LU)) == [] and "reporting entity id" in goaml.check_str(body("STR", LU | {"reporting_entity_id": ""}))[0]
    assert "at most 4,000" in goaml.check_str(body("STR", LU, narrative="x" * 4001))[0] and "reason for suspicion" in goaml.check_str(body("STR", LU, narrative=""))[0]


def test_each_regime_has_a_format_for_each_report_it_takes():
    assert (format_for("fincen", "SAR").id, format_for("fincen", "CTR").id, format_for("goaml", "STR").id, format_for("goaml", "CTR").id) == (
        "fincen_sar_xml", "fincen_ctr_xml", "goaml_str_xml", "goaml_ctr_xml")
    with pytest.raises(KeyError):
        format_for("fincen", "STR")
    assert all(f.extension == "xml" for f in FORMATS.values())


# --- the life of a report ------------------------------------------------------------------------------------------------
def _profile(regime="fincen", institution=US, channel="outbox"):
    registry.runtime(T).settings["filing"] = {"regime": regime, "institution": dict(institution), "channel": channel}


def _filed(s):
    """A case worked to the point the MLRO files it."""
    case = _case(s)
    ana, ivan, maya = (PERSONAS[k] for k in ("ana", "ivan", "maya"))
    complete_stage_tasks(s, case, ana)
    transition(s, case, "investigation", ana)
    complete_stage_tasks(s, case, ivan)
    transition(s, case, "sar_drafting", ivan)
    complete_stage_tasks(s, case, ivan)
    save_narrative(s, case, "Four cash deposits just under the reporting threshold on consecutive days.", ivan)
    transition(s, case, "mlro_review", ivan)
    transition(s, case, "filed", maya)
    s.flush()
    return case, s.scalars(select(m.Filing).where(m.Filing.case_id == case.id)).one()


def _kinds(s, case):
    return [e.kind for e in s.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == case.id).order_by(m.CaseEvent.id)) if e.kind.startswith(("filing.", "sar."))]


def test_filing_a_case_writes_the_report_in_the_clients_format_and_sends_it(s, _storage):
    _profile()
    case, f = _filed(s)
    assert (f.kind, f.format, f.status, f.channel, f.problems, f.filed_by, f.submitted_by, f.party_id) == (
        "SAR", "fincen_sar_xml", "submitted", "outbox", [], "maya", "maya", case.party_id)
    assert f.due == case.sla_due and f.receipt == f"regulatory/outbox/sar/{f.id}.xml"
    sent = (_storage / "lake" / T / f.receipt).read_text()
    assert sent == f.document and filing.detail(s, f.id)["intact"]
    root = ET.fromstring(f.document.encode())
    assert root.findtext(".//fc2:ActivityNarrativeText", namespaces=FC).startswith("Four cash deposits")
    assert root.findtext(".//fc2:TotalSuspiciousAmountText", namespaces=FC) == "37000" and f.payload["activity"]["count"] == 4
    assert _kinds(s, case) == ["filing.prepared", "filing.submitted", "sar.filed"]
    assert [x["what"] for x in f.history] == ["prepared", "submitted"]


def test_the_same_case_at_a_goaml_client_becomes_an_str(s):
    _profile("goaml", LU)
    case, f = _filed(s)
    assert (f.kind, f.format, f.status) == ("STR", "goaml_str_xml", "prepared") and "date of birth" in f.problems[0]  # goAML insists on it
    from datetime import date

    for p in s.scalars(select(m.Party)):
        p.born = date(1980, 2, 3)
    filing.recheck(s, f.id, PERSONAS["maya"])
    f = filing.submit(s, f.id, PERSONAS["maya"])
    assert (f.kind, f.format, f.status) == ("STR", "goaml_str_xml", "submitted") and f.id.startswith("str-")
    assert ET.fromstring(f.document.encode()).findtext("report_code") == "STR"


def test_a_report_with_problems_is_held_until_they_are_fixed(s):
    case, f = _filed(s)  # nobody has told the platform who the filing institution is
    assert f.status == "prepared" and len(f.problems) == 1 and "institution's details are incomplete" in f.problems[0]
    assert case.disposition == "sar_filed" and not filing.locked(s, case.id)  # the decision stands; the report waits
    with pytest.raises(filing.FilingError, match="cannot be sent yet"):
        filing.submit(s, f.id, PERSONAS["maya"])
    _profile()
    with pytest.raises(filing.FilingError, match="cannot be sent yet"):
        filing.submit(s, f.id, PERSONAS["maya"])  # the document still carries the old details
    before = f.sha256
    filing.recheck(s, f.id, PERSONAS["maya"])
    assert f.problems == [] and f.sha256 != before and "Meridian Retail Bank" in f.document
    assert filing.submit(s, f.id, PERSONAS["maya"]).status == "submitted"
    with pytest.raises(filing.FilingError, match="already submitted"):
        filing.submit(s, f.id, PERSONAS["maya"])
    with pytest.raises(filing.FilingError, match="has not been sent"):
        filing.recheck(s, f.id, PERSONAS["maya"])


def test_once_sent_the_narrative_is_locked_and_the_regulators_answer_is_recorded(s):
    _profile(channel="by_hand")
    case, f = _filed(s)
    assert f.status == "submitted" and f.receipt == "downloaded for upload by hand" and filing.locked(s, case.id)
    with pytest.raises(TransitionError, match="already sent to the regulator"):
        save_narrative(s, case, "A quieter version.", PERSONAS["ivan"])
    with pytest.raises(filing.FilingError, match="reference"):
        filing.acknowledge(s, f.id, "  ", PERSONAS["maya"])
    with pytest.raises(filing.FilingError, match="answered"):
        filing.amend(s, f.id, PERSONAS["maya"])
    filing.acknowledge(s, f.id, " 31000123456789 ", PERSONAS["maya"])
    assert (f.status, f.reference) == ("acknowledged", "31000123456789") and f.answered_at is not None and filing.locked(s, case.id)
    row = filing.row(f)
    assert row["continuing_review_due"] is not None and row["subject"] and row["amount"] == 37000.0
    for again in (lambda: filing.acknowledge(s, f.id, "x", PERSONAS["maya"]), lambda: filing.reject(s, f.id, "x", PERSONAS["maya"])):
        with pytest.raises(filing.FilingError, match="has been sent"):
            again()
    assert _kinds(s, case)[-1] == "filing.acknowledged"


def test_a_rejected_report_is_corrected_by_an_amendment_that_keeps_the_original(s):
    _profile()
    case, f = _filed(s)
    with pytest.raises(filing.FilingError, match="what the regulator said"):
        filing.reject(s, f.id, "", PERSONAS["maya"])
    filing.reject(s, f.id, "Narrative does not state the total.", PERSONAS["maya"])
    assert f.status == "rejected" and not filing.locked(s, case.id)
    original = f.document
    new = filing.amend(s, f.id, PERSONAS["maya"], narrative="Four cash deposits totalling $37,000 just under the threshold.")
    assert (f.status, f.document, new.amends_id, new.status, new.kind) == ("superseded", original, f.id, "submitted", "SAR")
    assert "totalling $37,000" in new.document and case.narrative.startswith("Four cash deposits totalling") and case.drafted_by == "maya"
    filing.acknowledge(s, new.id, "31000999", PERSONAS["maya"])
    second = filing.amend(s, new.id, PERSONAS["maya"])  # correcting an accepted report quotes the regulator's number
    assert ET.fromstring(second.document.encode()).findtext(".//fc2:EFilingPriorDocumentNumber", namespaces=FC) == "31000999"
    assert [x.status for x in s.scalars(select(m.Filing).order_by(m.Filing.filed_at, m.Filing.id))].count("superseded") == 2
    assert filing.overview(s, T)["counts"] == {"prepared": 0, "submitted": 1, "acknowledged": 0, "rejected": 0, "superseded": 2}


def test_a_document_changed_after_the_fact_no_longer_matches_its_hash(s):
    _profile()
    _, f = _filed(s)
    f.document = f.document.replace("37000", "3700")
    assert filing.detail(s, f.id)["intact"] is False


def test_cash_over_the_threshold_in_a_day_owes_a_ctr_and_only_one(s):
    _profile()
    s.add(m.Party(id="C9", name="Corner Cafe Ltd", kind="business"))
    s.add(m.Account(id="A9", party_id="C9", product="business"))
    s.flush()
    day = m.now().replace(hour=9, minute=0, second=0, microsecond=0)
    for i, (amount, direction, channel) in enumerate([(6000, "in", "cash"), (4500, "in", "cash"), (2000, "out", "cash"), (50000, "in", "wire")]):
        s.add(m.Transaction(id=f"c{i}", account_id="A9", party_id="C9", ts=day, amount=amount, direction=direction, channel=channel))
    s.add(m.Transaction(id="c9", account_id="A9", party_id="C9", ts=day.replace(day=1) if day.day > 2 else day.replace(day=28), amount=9999, direction="in", channel="cash"))
    s.flush()
    owed = reports.cash_days(s)
    assert [(d["party_id"], d["day"], d["direction"], d["total"], d["count"]) for d in owed] == [("C9", day.date().isoformat(), "in", 10500.0, 2)]
    assert filing.overview(s, T)["cash_days"] == owed
    f = filing.prepare_ctr(s, "C9", owed[0]["day"], PERSONAS["maya"])
    assert (f.kind, f.case_id, f.status, f.problems, f.format) == ("CTR", None, "prepared", [], "fincen_ctr_xml")
    assert (f.payload["activity"]["cash_in"], f.payload["activity"]["cash_out"], f.payload["activity"]["count"]) == (10500.0, 2000.0, 3)
    assert (f.due - day).days in (14, 15) and filing.overview(s, T)["cash_days"] == []
    with pytest.raises(filing.FilingError, match="already exists"):
        filing.prepare_ctr(s, "C9", owed[0]["day"], PERSONAS["maya"])
    with pytest.raises(filing.FilingError, match="No cash transactions"):
        filing.prepare_ctr(s, "C9", "2020-01-01", PERSONAS["maya"])
    assert filing.submit(s, f.id, PERSONAS["maya"]).status == "submitted" and [x["what"] for x in f.history] == ["prepared", "submitted"]


# --- over HTTP, at the demo clients ---------------------------------------------------------------------------------------
def _officer(t):
    return next(p for tt, p in EVERYONE if tt == t and "submit_filings" in _permissions(t, p))


@pytest.mark.parametrize("t", CLIENTS)
def test_each_demo_client_reports_in_its_own_regime_with_reports_at_every_stage(client, t):
    page = client.get("/api/filings", headers=h(t, _officer(t))).json()
    regime = {"meridian": "fincen", "kestrel": "fincen", "alder": "goaml"}[t]
    assert page["profile"]["regime"] == regime and page["profile"]["missing"] == [] and page["can_submit"]
    suspicion = [f for f in page["filings"] if f["kind"] in ("SAR", "STR")]
    assert {f["kind"] for f in suspicion} == {"STR" if regime == "goaml" else "SAR"} and all(f["problems"] == [] for f in page["filings"])
    assert {"acknowledged", "superseded"} <= {f["status"] for f in suspicion}
    assert all(f["reference"].startswith("31000") for f in suspicion if f["status"] == "acknowledged")
    amended = [f for f in suspicion if f["amends_id"]]
    assert len(amended) == 1 and next(f for f in suspicion if f["id"] == amended[0]["amends_id"])["status"] == "superseded"
    one = client.get(f"/api/filings/{suspicion[0]['id']}", headers=h(t, _officer(t))).json()
    assert one["intact"] and one["document"].startswith("<?xml") and one["history"][0]["what"] == "prepared"
    doc = client.get(f"/api/filings/{suspicion[0]['id']}/document", headers=h(t, _officer(t)))
    assert doc.status_code == 200 and doc.text == one["document"] and f'{suspicion[0]["id"]}.xml' in doc.headers["content-disposition"]
    ET.fromstring(doc.content)


@pytest.mark.parametrize("t,who", EVERYONE)
def test_only_those_who_send_reports_or_oversee_the_team_can_read_them_and_only_officers_can_act(client, t, who):
    perms = _permissions(t, who)
    may_read, may_act = bool(perms & {"submit_filings", "view_team"}), "submit_filings" in perms
    page = client.get("/api/filings", headers=h(t, who))
    assert page.status_code == (200 if may_read else 403)
    any_id = client.get("/api/filings", headers=h(t, _officer(t))).json()["filings"][0]["id"]
    assert client.get(f"/api/filings/{any_id}/document", headers=h(t, who)).status_code in ((200, 404) if may_read else (403,))
    for url, payload in ((f"/api/filings/{any_id}/submit", {}), (f"/api/filings/{any_id}/acknowledge", {"reference": "x"}),
                         (f"/api/filings/{any_id}/reject", {"reason": "x"}), (f"/api/filings/{any_id}/amend", {}),
                         (f"/api/filings/{any_id}/recheck", {}), ("/api/filings/ctr", {"party_id": "nobody", "day": "2020-01-01"})):
        code = client.post(url, headers=h(t, who), json=payload).status_code
        assert (code != 403) == may_act, (url, code)
    if may_read:
        assert page.json()["can_submit"] == may_act


def test_another_clients_report_does_not_exist_here(client):
    theirs = client.get("/api/filings", headers=h("kestrel", _officer("kestrel"))).json()["filings"][0]["id"]
    for call in (lambda: client.get(f"/api/filings/{theirs}", headers=h("meridian", _officer("meridian"))),
                 lambda: client.get(f"/api/filings/{theirs}/document", headers=h("meridian", _officer("meridian")))):
        assert call().status_code == 404
    assert client.post(f"/api/filings/{theirs}/acknowledge", headers=h("meridian", _officer("meridian")), json={"reference": "x"}).status_code == 409


def test_a_cash_day_becomes_a_ctr_and_the_regulators_answer_closes_it_over_http(client):
    t, who = "meridian", _officer("meridian")
    page = client.get("/api/filings", headers=h(t, who)).json()
    day = page["cash_days"][0]
    made = client.post("/api/filings/ctr", headers=h(t, who), json={"party_id": day["party_id"], "day": day["day"]})
    assert made.status_code == 200 and made.json()["status"] == "prepared" and made.json()["kind"] == "CTR"
    fid = made.json()["id"]
    assert client.post("/api/filings/ctr", headers=h(t, who), json={"party_id": day["party_id"], "day": day["day"]}).status_code == 409
    assert client.post(f"/api/filings/{fid}/acknowledge", headers=h(t, who), json={"reference": "1"}).status_code == 409  # not sent yet
    assert client.post(f"/api/filings/{fid}/submit", headers=h(t, who)).json()["status"] == "submitted"
    done = client.post(f"/api/filings/{fid}/acknowledge", headers=h(t, who), json={"reference": "31000555"}).json()
    assert (done["status"], done["reference"]) == ("acknowledged", "31000555")
    after = client.get("/api/filings", headers=h(t, who)).json()
    assert day not in after["cash_days"] and after["counts"]["acknowledged"] == page["counts"]["acknowledged"] + 1


def test_the_case_shows_where_its_report_stands(client):
    t, who = "meridian", _officer("meridian")
    f = next(x for x in client.get("/api/filings", headers=h(t, who)).json()["filings"] if x["status"] == "acknowledged" and x["case_id"])
    shown = client.get(f"/api/cases/{f['case_id']}", headers=h(t, who)).json()["filing"]
    assert (shown["id"], shown["status"], shown["reference"], shown["format"]) == (f["id"], "acknowledged", f["reference"], "fincen_sar_xml")
    assert client.post(f"/api/cases/{f['case_id']}/narrative", headers=h(t, who), json={"text": "Changed after filing."}).status_code in (400, 409)


def test_only_the_operator_sets_how_a_client_reports(client):
    good = {"regime": "goaml", "channel": "by_hand", "institution": {"name": " Kestrel EU ", "reporting_entity_id": "77"}}
    assert client.put("/api/platform/tenants/kestrel/filing", headers=h("kestrel", _officer("kestrel")), json=good).status_code in (401, 403)
    for bad in ({"regime": "martian"}, {"channel": "pigeon"}, {"institution": {"name": 5}}):
        assert client.put("/api/platform/tenants/kestrel/filing", headers=OP, json=bad).status_code == 400
    assert client.put("/api/platform/tenants/nobody/filing", headers=OP, json=good).status_code == 400
    before = client.get("/api/filings", headers=h("kestrel", _officer("kestrel"))).json()["profile"]
    out = client.put("/api/platform/tenants/kestrel/filing", headers=OP, json=good)
    assert out.status_code == 200 and out.json()["filing"]["institution"]["name"] == "Kestrel EU"
    now_ = client.get("/api/filings", headers=h("kestrel", _officer("kestrel"))).json()["profile"]
    assert (now_["regime"], now_["channel"]) == ("goaml", "by_hand") and "tax id" in now_["missing"] and [f["id"] for f in now_["formats"]] == ["goaml_str_xml", "goaml_ctr_xml"]
    assert client.get("/api/filings", headers=h("meridian", _officer("meridian"))).json()["profile"]["regime"] == "fincen"
    client.put("/api/platform/tenants/kestrel/filing", headers=OP, json={"regime": before["regime"], "channel": before["channel"], "institution": before["institution"]})
