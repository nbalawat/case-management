from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.agents.investigator import investigate
from app.alerts.hub import ingest_external_alert, route_alerts
from app.cases.service import TransitionError, save_narrative, transition
from app.db import Base, make_engine
from app.detection.engine import run_detection
from app.domain.models import Account, CaseEvent, Filing, Party, Transaction
from app.domain.personas import PERSONAS
from app.ingest.connectors import CsvConnector
from app.ingest.mapping import HeuristicProposer
from app.ingest.service import profile, run_feed

CSV = """TXN_REF,CUST_NO,ACCT_NBR,TXN_DT,TXN_AMT,DR_CR_IND,CHNL,CNTRPRTY_NM,CNTRY_CD
1,C1,A1,20260101100000,9100.00,CR,CSH,,US
2,C1,A1,20260102100000,9200.00,CR,CSH,,US
3,C1,A1,20260103100000,9300.00,CR,CSH,,US
4,C1,A1,20260104100000,9400.00,CR,CSH,,US
5,C1,A1,20260104110000,,CR,CSH,,US
6,C1,A1,20260104120000,50.00,ZZ,CRD,Shop,US
"""


@pytest.fixture
def s():
    eng = make_engine("sqlite://")
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, expire_on_commit=False)() as session:
        yield session


def mapping():
    conn = CsvConnector("t.csv", CSV)
    return {p.target: p.source for p in HeuristicProposer().propose(conn.columns(), list(conn.records()))}


def test_mapping_proposer_finds_legacy_columns():
    m = mapping()
    assert m["amount"] == "TXN_AMT" and m["party_id"] == "CUST_NO" and m["direction"] == "DR_CR_IND"
    assert m["ts"] == "TXN_DT" and m["counterparty_country"] == "CNTRY_CD"
    assert profile(CsvConnector("t.csv", CSV))["columns"][0]["column"] == "TXN_REF"


def test_feed_reconciles_and_rejects_bad_rows(s):
    feed = run_feed(s, CsvConnector("t.csv", CSV), mapping())
    assert feed.stats["read"] == 6 and feed.stats["loaded"] == 4 and feed.stats["rejected"] == 2
    assert feed.stats["reconciled"] is True
    again = run_feed(s, CsvConnector("t.csv", CSV), mapping())
    assert again.stats["duplicates"] == 4 and again.stats["loaded"] == 0


def _case(s):
    run_feed(s, CsvConnector("t.csv", CSV), mapping())
    alerts = run_detection(s)
    assert [a.scenario for a in alerts] == ["structuring"]
    assert run_detection(s) == []  # idempotent
    (case,) = route_alerts(s, alerts)
    return case


def test_alert_becomes_case_and_agent_recommends_with_citations(s):
    case = _case(s)
    run = investigate(s, case)
    assert run.recommendation == "escalate" and case.agent_recommendation == "escalate"
    assert case.state == "triage"  # the agent never moves the case
    assert any(f["cites"] for st in run.steps for f in st["findings"])
    assert case.drafted_by is None and case.narrative


def test_workflow_guards_roles_four_eyes_and_files(s):
    case = _case(s)
    ana, ivan, maya = PERSONAS["ana"], PERSONAS["ivan"], PERSONAS["maya"]
    with pytest.raises(TransitionError):
        transition(s, case, "filed", maya)  # not reachable from triage
    transition(s, case, "investigation", ana)
    with pytest.raises(TransitionError):
        transition(s, case, "sar_drafting", ana)  # wrong role
    transition(s, case, "sar_drafting", ivan)
    with pytest.raises(TransitionError):
        transition(s, case, "mlro_review", ivan)  # narrative required
    save_narrative(s, case, "Narrative text", ivan)
    transition(s, case, "mlro_review", ivan)
    with pytest.raises(TransitionError):
        transition(s, case, "filed", ivan)  # only the MLRO files
    transition(s, case, "filed", maya)
    s.flush()
    assert case.disposition == "sar_filed" and s.query(Filing).count() == 1
    kinds = [e.kind for e in s.query(CaseEvent).order_by(CaseEvent.id)]
    assert kinds[0] == "case.created" and kinds[-1] == "sar.filed"


def test_four_eyes_blocks_drafter(s):
    case = _case(s)
    maya = PERSONAS["maya"]
    case.state = "mlro_review"
    save_narrative(s, case, "x", maya)
    with pytest.raises(TransitionError, match="Four-eyes"):
        transition(s, case, "filed", maya)


def test_benign_pass_through_is_recommended_for_closure(s):
    s.add(Party(id="P1", name="Test Person", occupation="teacher"))
    s.add(Account(id="A1", party_id="P1"))
    t0 = datetime(2026, 3, 1, 10)
    s.add(Transaction(id="a", account_id="A1", party_id="P1", ts=t0, amount=50000, direction="in", channel="wire", counterparty="Halcyon Brokerage Services"))
    s.add(Transaction(id="b", account_id="A1", party_id="P1", ts=t0 + timedelta(hours=10), amount=49000, direction="out", channel="wire", counterparty="First National Title & Escrow"))
    s.flush()
    (case,) = route_alerts(s, run_detection(s))
    assert investigate(s, case).recommendation == "close"


def test_external_alert_is_idempotent_and_joins_open_case(s):
    kw = dict(source="legacy-tm", scenario="structuring", party_id="EXT-1", score=0.8, explanation="Vendor rule 12 fired")
    a1, c1 = ingest_external_alert(s, external_id="A-1", **kw)
    a2, c2 = ingest_external_alert(s, external_id="A-1", **kw)
    a3, c3 = ingest_external_alert(s, external_id="A-2", **kw)
    assert c1 is not None and a2.id == a1.id and c2 is None
    assert c3.id == c1.id and a3.case_id == c1.id
