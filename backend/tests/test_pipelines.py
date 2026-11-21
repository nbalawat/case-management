"""Pipelines from object storage: formats, datasets, discovery, quality gates, ordering, replays and client isolation."""
import gzip
import io
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.domain import models as m
from app.ingest import locations, pipeline
from app.ingest.datasets import DATASETS, in_dependency_order
from app.ingest.formats import FormatError, detect, read_records
from app.storage import StorageError
from app.tenants import onboarding
from app.tenants.onboarding import OnboardingError
from app.tenancy import TenantError
from support import T

OTHER = "kestrel"
CUSTOMERS = "CUST_NO,CUST_NM,RISK_CD,PEP_FLAG,RES_CNTRY,OPEN_DT\nC1,Ada Lovelace,H,Y,GB,20200101\nC2,Bob Stone,L,N,US,20210315\n"
ACCOUNTS = "ACCT_NBR,CUST_NO,PROD_CD,ACCT_STAT\nA1,C1,checking,O\nA2,C2,savings,O\n"
TXN_HEAD = "TXN_REF,ACCT_NBR,CUST_NO,TXN_DT,TXN_AMT,DR_CR_IND,CHNL,CNTRPRTY_NM,CNTRY_CD\n"
MAPS = {
    "customers": {"party_id": "CUST_NO", "name": "CUST_NM", "risk_rating": "RISK_CD", "pep": "PEP_FLAG", "country": "RES_CNTRY", "onboarded": "OPEN_DT"},
    "accounts": {"account_id": "ACCT_NBR", "party_id": "CUST_NO", "product": "PROD_CD", "status": "ACCT_STAT"},
    "transactions": {"txn_id": "TXN_REF", "account_id": "ACCT_NBR", "party_id": "CUST_NO", "ts": "TXN_DT", "amount": "TXN_AMT",
                     "direction": "DR_CR_IND", "channel": "CHNL", "counterparty": "CNTRPRTY_NM", "counterparty_country": "CNTRY_CD"},
}


def txn(ref, cust="C1", acct=None, amount="100.00", when="20260105120000", side="CR", channel="ACH"):
    return f"{ref},{acct or 'A' + cust[1:]},{cust},{when},{amount},{side},{channel},Acme Ltd,US\n"


def txns(*rows):
    return (TXN_HEAD + "".join(rows)).encode()


class Lake:
    """One client with a landing zone in memory and a session, plus helpers to land files and define sources."""

    def __init__(self, db, tenant, template="full_service_bank"):
        self.db, self.tenant = db, tenant
        db.onboard(tenant, template=template)
        self.url = f"memory://{uuid.uuid4().hex}/{tenant}/"
        with db.platform() as p:
            onboarding.set_storage(p, tenant, [{"id": "landing", "label": "Landing", "url": self.url}])
            p.commit()
        self.store = locations.store_for(tenant, "landing")

    def land(self, key, data):
        return self.store.write(key, data if isinstance(data, bytes) else data.encode())

    def source(self, s, dataset, prefix=None, **kw):
        spec = {"name": kw.pop("name", f"{dataset} source"), "dataset": dataset, "root": "landing", "prefix": dataset if prefix is None else prefix,
                "mapping": MAPS[dataset]} | kw
        return pipeline.save_source(s, spec, "omar")

    def standard(self, s):
        self.land("customers/c.csv", CUSTOMERS)
        self.land("accounts/a.csv", ACCOUNTS)
        return [self.source(s, d) for d in ("customers", "accounts", "transactions")]


@pytest.fixture
def lake(db):
    return Lake(db, T)


@pytest.fixture
def s(lake):
    with lake.db.tenant(T) as session:
        yield session


def count(s, model):
    return s.scalar(select(func.count()).select_from(model))


# --- formats ------------------------------------------------------------------------
def test_format_is_read_from_the_file_name():
    assert detect("a/b/part-0.csv") == ("csv", False) and detect("x.CSV.GZ") == ("csv", True) and detect("x.jsonl.gz") == ("jsonl", True)
    assert detect("x.ndjson") == ("jsonl", False) and detect("x.parquet") == ("parquet", False) and detect("part-0000") == (None, False)


def _parquet(rows):
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    return buf.getvalue()


def test_every_format_yields_the_same_text_records():
    from datetime import datetime

    want = [{"id": "T1", "amount": "10.5", "when": "2026-01-05 12:00:00", "flag": "true", "note": ""},
            {"id": "T2", "amount": "7", "when": "2026-01-06 08:30:00", "flag": "false", "note": "x"}]
    csv_bytes = b"id,amount,when,flag,note\nT1,10.5,2026-01-05 12:00:00,true,\nT2,7,2026-01-06 08:30:00,false,x\n"
    jsonl = b'{"id":"T1","amount":10.5,"when":"2026-01-05 12:00:00","flag":true,"note":null}\n\n{"id":"T2","amount":7,"when":"2026-01-06 08:30:00","flag":false,"note":"x"}\n'
    parquet = _parquet([{"id": "T1", "amount": 10.5, "when": datetime(2026, 1, 5, 12), "flag": True, "note": None},
                        {"id": "T2", "amount": 7.0, "when": datetime(2026, 1, 6, 8, 30), "flag": False, "note": "x"}])
    assert list(read_records(io.BytesIO(csv_bytes), "csv")) == want
    assert list(read_records(io.BytesIO(gzip.compress(csv_bytes)), "csv", gz=True)) == want
    assert list(read_records(io.BytesIO(jsonl), "jsonl")) == want
    assert list(read_records(io.BytesIO(parquet), "parquet")) == want
    assert list(read_records(io.BytesIO(b"id|amount\nT1|3\n"), "csv", delimiter="|")) == [{"id": "T1", "amount": "3"}]
    assert list(read_records(io.BytesIO(b""), "csv")) == []


@pytest.mark.parametrize("data,fmt,gz", [
    (b"not gzip at all", "csv", True), (b"{broken json\n", "jsonl", False), (b"[1,2,3]\n", "jsonl", False), (b"PAR1 nope", "parquet", False),
    (b"a,b\n1,2,3,4\n", "csv", False), (b"\xff\xfe\x00bad", "csv", False), (b"a,b\n", "xml", False),
])
def test_unreadable_content_is_a_format_error(data, fmt, gz):
    with pytest.raises(FormatError):
        list(read_records(io.BytesIO(data), fmt, gz=gz))


# --- datasets -----------------------------------------------------------------------
def test_dependency_order():
    assert in_dependency_order(["transactions", "customers", "accounts"]) == ["customers", "accounts", "transactions"]
    assert in_dependency_order(["transactions"]) == ["transactions"] and in_dependency_order([]) == []
    for d in DATASETS.values():
        assert any(f["required"] for f in d.fields.values()) and set(d.depends_on) <= set(DATASETS)


def test_customer_and_account_records_are_translated():
    c = DATASETS["customers"].transform({"CUST_NO": " C1 ", "CUST_NM": "Ada", "RISK_CD": "h", "PEP_FLAG": "Y", "RES_CNTRY": "gb", "OPEN_DT": "2020-01-01"}, MAPS["customers"])
    assert (c["party_id"], c["risk_rating"], c["pep"], c["country"], c["onboarded"].year) == ("C1", "high", True, "GB", 2020)
    assert DATASETS["accounts"].transform({"ACCT_NBR": "A1", "CUST_NO": "C1", "ACCT_STAT": "X"}, MAPS["accounts"])["status"] == "closed"
    for bad in ({"CUST_NO": "", "CUST_NM": "x"}, {"CUST_NO": "C1", "CUST_NM": ""}, {"CUST_NO": "C1", "CUST_NM": "x", "RISK_CD": "?"},
                {"CUST_NO": "C1", "CUST_NM": "x", "RES_CNTRY": "GBR"}, {"CUST_NO": "C1", "CUST_NM": "x", "OPEN_DT": "soon"},
                {"CUST_NO": "C1", "CUST_NM": "x", "PEP_FLAG": "maybe"}):
        with pytest.raises(ValueError):
            DATASETS["customers"].transform(bad, MAPS["customers"])


# --- source definitions -------------------------------------------------------------
BAD_SOURCES = [
    ({"name": "x"}, "name"), ({"dataset": "payments"}, "Dataset"), ({"root": "elsewhere"}, "not a storage location"),
    ({"prefix": "../other-client"}, "outside"), ({"prefix": "/etc"}, "outside"), ({"pattern": "../*"}, "pattern"), ({"format": "xml"}, "Format"),
    ({"mapping": {"txn_id": "TXN_REF"}}, "required"), ({"mapping": MAPS["transactions"] | {"colour": "X"}}, "does not have"),
    ({"rules": {"max_reject_rate": 5}}, "between 0 and 1"), ({"rules": {"max_reject_rate": "lots"}}, "numbers"), ({"rules": {"speed": 1}}, "Unknown quality rule"),
    ({"rules": {"unknown_customer": "ignore"}}, "reject"), ({"schedule_minutes": 1}, "schedule"), ({"schedule_minutes": True}, "schedule"),
    ({"options": {"delimiter": "||"}}, "delimiter"), ({"options": {"endpoint_url": "http://evil"}}, "Options"),
]


@pytest.mark.parametrize("change,says", BAD_SOURCES, ids=[str(i) for i in range(len(BAD_SOURCES))])
def test_bad_source_definitions_are_refused(lake, s, change, says):
    spec = {"name": "Transactions feed", "dataset": "transactions", "root": "landing", "prefix": "transactions", "mapping": MAPS["transactions"]}
    with pytest.raises(pipeline.PipelineError) as e:
        pipeline.save_source(s, spec | change, "omar")
    assert says in str(e.value) and count(s, m.DataSource) == 0


def test_source_names_are_unique_and_mapping_changes_are_versioned(lake, s):
    src = lake.source(s, "transactions", name="Feed A")
    with pytest.raises(pipeline.PipelineError):
        lake.source(s, "transactions", name="Feed A")
    spec = {"name": "Feed A", "dataset": "transactions", "root": "landing", "prefix": "transactions", "mapping": MAPS["transactions"]}
    assert pipeline.save_source(s, spec | {"schedule_minutes": 30}, "omar", src).mapping_version == 1
    assert pipeline.save_source(s, spec | {"mapping": MAPS["transactions"] | {"currency": "CCY"}}, "omar", src).mapping_version == 2
    with pytest.raises(pipeline.PipelineError):
        pipeline.save_source(s, spec | {"dataset": "customers", "mapping": MAPS["customers"]}, "omar", src)


def test_preview_lists_files_and_proposes_a_mapping(lake):
    lake.land("customers/2026/c1.csv", CUSTOMERS)
    lake.land("customers/2026/_SUCCESS", "")
    lake.land("customers/.hidden.csv", CUSTOMERS)
    lake.land("customers/_tmp/half.csv", CUSTOMERS)
    seen = pipeline.preview(T, {"dataset": "customers", "root": "landing", "prefix": "customers"})
    assert seen["matching"] == 1 and seen["sampled"] == "customers/2026/c1.csv" and seen["problem"] is None
    proposed = {p["target"]: p["source"] for p in seen["proposals"]}
    assert proposed["party_id"] == "CUST_NO" and proposed["name"] == "CUST_NM" and proposed["risk_rating"] == "RISK_CD"
    assert [c["column"] for c in seen["columns"]][:2] == ["CUST_NO", "CUST_NM"]
    assert pipeline.preview(T, {"dataset": "customers", "root": "landing", "prefix": "nothing-here"})["matching"] == 0
    lake.land("broken/x.parquet", b"not parquet")
    assert "not a readable Parquet" in pipeline.preview(T, {"dataset": "customers", "root": "landing", "prefix": "broken"})["problem"]
    for bad in ({"root": "nope"}, {"prefix": "../x"}, {"dataset": "nope"}):
        with pytest.raises(pipeline.PipelineError):
            pipeline.preview(T, {"dataset": "customers", "root": "landing", "prefix": "customers"} | bad)


@pytest.mark.parametrize("pattern,expected", [
    ("**", {"dt=1/part-0.csv", "dt=2/part-0.jsonl", "notes.txt"}), ("**/part-*", {"dt=1/part-0.csv", "dt=2/part-0.jsonl"}),
    ("*.csv", {"dt=1/part-0.csv"}), ("dt=2/*", {"dt=2/part-0.jsonl"}), ("*.parquet", set()), ("notes.txt", {"notes.txt"}),
])
def test_file_patterns(lake, pattern, expected):
    for key in ("dt=1/part-0.csv", "dt=2/part-0.jsonl", "notes.txt", "dt=2/_SUCCESS"):
        lake.land(f"feed/{key}", "x")
    assert {o.key[len("feed/"):] for o in pipeline.matching_objects(lake.store, "feed", pattern)} == expected


# --- running ------------------------------------------------------------------------
def test_a_batch_loads_in_dependency_order_and_reconciles(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/dt=2026-01-05/part-0.csv", txns(txn("T1", amount="100.00"), txn("T2", "C2", amount="50.50", side="DR", channel="CRD")))
    runs = pipeline.run_many(s, list(reversed(sources)), "omar")  # handed over in the wrong order on purpose
    assert [s.get(m.DataSource, r.source_id).dataset for r in runs] == ["customers", "accounts", "transactions"]
    assert [r.status for r in runs] == ["succeeded"] * 3 and len({r.batch_id for r in runs}) == 1
    assert (count(s, m.Party), count(s, m.Account), count(s, m.Transaction)) == (2, 2, 2)
    ada = s.get(m.Party, {"tenant_id": T, "id": "C1"})
    assert (ada.name, ada.risk_rating, ada.pep, ada.country) == ("Ada Lovelace", "high", True, "GB")
    st = runs[2].stats
    assert (st["read"], st["loaded"], st["rejected"], st["duplicates"], st["source_amount"], st["loaded_amount"], st["reconciled"]) == (2, 2, 0, 0, 150.5, 150.5, True)
    assert [x["step"] for x in runs[2].steps] == ["discover", "validate", "load", "reconcile", "detect"] and all(x["status"] == "done" for x in runs[2].steps)
    t2 = s.get(m.Transaction, {"tenant_id": T, "id": "T2"})
    assert (t2.direction, t2.channel, t2.amount, t2.party_id) == ("out", "card", 50.5, "C2")


def test_running_again_loads_nothing_twice(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/part-0.csv", txns(txn("T1"), txn("T2")))
    pipeline.run_many(s, sources, "omar")
    before = (count(s, m.Party), count(s, m.Account), count(s, m.Transaction), count(s, m.IngestedObject))
    again = pipeline.run_many(s, sources, "omar")
    assert [r.status for r in again] == ["nothing_new"] * 3 and all(r.stats["objects_new"] == 0 for r in again)
    assert (count(s, m.Party), count(s, m.Account), count(s, m.Transaction), count(s, m.IngestedObject)) == before


def test_a_new_file_is_picked_up_and_overlapping_records_are_not_duplicated(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/part-0.csv", txns(txn("T1"), txn("T2")))
    pipeline.run_many(s, sources, "omar")
    lake.land("transactions/part-1.csv", txns(txn("T2"), txn("T3")))  # the source system re-sent T2
    run = pipeline.run_source(s, sources[2], "omar")
    assert run.status == "succeeded" and (run.stats["objects_new"], run.stats["loaded"], run.stats["duplicates"], run.stats["reconciled"]) == (1, 1, 1, True)
    assert count(s, m.Transaction) == 3


def test_a_corrected_file_counts_as_new(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/part-0.csv", txns(txn("T1", amount=""), txn("T2", amount="")))
    first = pipeline.run_source(s, sources[2], "omar")
    assert first.status == "nothing_new" or first.status == "quarantined"
    pipeline.run_many(s, sources[:2], "omar")
    lake.land("transactions/part-0.csv", txns(txn("T1"), txn("T2")))  # same name, fixed content
    fixed = pipeline.run_source(s, sources[2], "omar")
    assert fixed.status == "succeeded" and fixed.stats["loaded"] == 2
    history = list(s.scalars(select(m.IngestedObject).where(m.IngestedObject.source_id == sources[2].id).order_by(m.IngestedObject.at)))
    assert [o.status for o in history] == ["quarantined", "loaded"] and history[0].fingerprint != history[1].fingerprint


def test_customer_changes_update_in_place(lake, s):
    src = lake.source(s, "customers")
    lake.land("customers/day1.csv", CUSTOMERS)
    pipeline.run_source(s, src, "omar")
    lake.land("customers/day2.csv", CUSTOMERS.replace("C2,Bob Stone,L", "C2,Bob Stone,H") + "C3,Cy Young,M,N,US,20220101\n")
    run = pipeline.run_source(s, src, "omar")
    assert (run.stats["loaded"], run.stats["updated"], run.stats["duplicates"]) == (1, 1, 1) and count(s, m.Party) == 3
    assert s.get(m.Party, {"tenant_id": T, "id": "C2"}).risk_rating == "high"


def test_a_file_over_the_reject_limit_is_held_whole_and_the_rest_load(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/good.csv", txns(*[txn(f"G{i}") for i in range(20)]))
    lake.land("transactions/bad.csv", txns(txn("B1"), txn("B2", amount=""), txn("B3", side="XX"), txn("B4", channel="???")))
    runs = pipeline.run_many(s, sources, "omar")
    run = runs[2]
    assert run.status == "partial" and (run.stats["objects_loaded"], run.stats["objects_quarantined"], run.stats["loaded"]) == (1, 1, 20)
    assert count(s, m.Transaction) == 20 and s.get(m.Transaction, {"tenant_id": T, "id": "B1"}) is None  # nothing from the bad file
    bad = s.scalars(select(m.IngestedObject).where(m.IngestedObject.status == "quarantined")).one()
    assert bad.key == "transactions/bad.csv" and "3 of 4 records (75%)" in bad.reason
    assert {r["reason"] for r in bad.stats["reject_samples"]} == {"missing amount", "unknown direction code 'XX'", "unknown channel code '???'"}
    assert [x["status"] for x in run.steps if x["step"] == "validate"] == ["held"]
    kept = locations.quarantine_store(T)
    path = bad.stats["rejects_at"][len(kept.url):]
    with kept.open(path) as f:
        lines = [json.loads(x) for x in f.read().decode().splitlines()]
    assert [x["row"] for x in lines] == [2, 3, 4] and lines[0]["record"]["TXN_REF"] == "B2"


def test_a_few_bad_records_under_the_limit_are_rejected_and_the_file_loads(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/part-0.csv", txns(*[txn(f"T{i}") for i in range(39)], txn("BAD", amount="")))
    run = pipeline.run_many(s, sources, "omar")[2]
    assert run.status == "succeeded_with_rejects" and (run.stats["read"], run.stats["loaded"], run.stats["rejected"], run.stats["reconciled"]) == (40, 39, 1, True)


@pytest.mark.parametrize("rule,value,files,status", [
    ("max_reject_rate", 0.0, [txns(*[txn(f"T{i}") for i in range(99)], txn("BAD", amount=""))], "quarantined"),
    ("max_reject_rate", 1.0, [txns(txn("T1", amount=""), txn("T2", amount=""))], "succeeded_with_rejects"),
    ("min_rows", 3, [txns(txn("T1"), txn("T2"))], "quarantined"),
    ("min_rows", 0, [TXN_HEAD.encode()], "succeeded"),
])
def test_quality_rules_decide(lake, s, rule, value, files, status):
    lake.standard(s)
    for src in s.scalars(select(m.DataSource).where(m.DataSource.dataset != "transactions")):
        pipeline.run_source(s, src, "omar")
    tx = s.scalars(select(m.DataSource).where(m.DataSource.dataset == "transactions")).one()
    tx.rules = {rule: value}
    for i, data in enumerate(files):
        lake.land(f"transactions/f{i}.csv", data)
    assert pipeline.run_source(s, tx, "omar").status == status


def test_empty_file_is_held_by_default(lake, s):
    src = lake.source(s, "customers")
    lake.land("customers/empty.csv", "CUST_NO,CUST_NM\n")
    run = pipeline.run_source(s, src, "omar")
    assert run.status == "quarantined" and "0 record(s)" in s.scalars(select(m.IngestedObject)).one().reason


def test_schema_drift_holds_the_file_and_new_columns_only_warn(lake, s):
    src = lake.source(s, "customers", columns=["CUST_NO", "CUST_NM", "RISK_CD", "PEP_FLAG", "RES_CNTRY", "OPEN_DT"])
    lake.land("customers/renamed.csv", CUSTOMERS.replace("CUST_NM", "FULL_NAME"))
    lake.land("customers/wider.csv", CUSTOMERS.replace("OPEN_DT\n", "OPEN_DT,SEGMENT\n").replace("0101\n", "0101,retail\n").replace("0315\n", "0315,retail\n"))
    lake.land("customers/narrower.csv", "CUST_NO,CUST_NM\nC9,Nine\n")
    run = pipeline.run_source(s, src, "omar")
    by_key = {o.key.rsplit("/", 1)[1]: o for o in s.scalars(select(m.IngestedObject))}
    assert run.status == "partial" and by_key["renamed.csv"].status == "quarantined" and "CUST_NM" in by_key["renamed.csv"].reason
    assert by_key["wider.csv"].status == "loaded" and "SEGMENT" in by_key["wider.csv"].stats["warnings"][0]
    assert by_key["narrower.csv"].status == "loaded" and "Optional mapped column(s) missing" in by_key["narrower.csv"].stats["warnings"][0]
    assert count(s, m.Party) == 3


@pytest.mark.parametrize("key,data", [("x.parquet", b"definitely not parquet"), ("x.csv.gz", b"not gzip"), ("x.jsonl", b"{oops"), ("part-0000", b"a,b\n1,2\n")])
def test_unreadable_files_are_held_with_the_reason(lake, s, key, data):
    src = lake.source(s, "customers")
    lake.land(f"customers/{key}", data)
    lake.land("customers/ok.csv", CUSTOMERS)
    run = pipeline.run_source(s, src, "omar")
    held = s.scalars(select(m.IngestedObject).where(m.IngestedObject.status == "quarantined")).one()
    assert run.status == "partial" and held.key.endswith(key) and "could not be read" in held.reason and count(s, m.Party) == 2


def test_stated_format_overrides_the_file_name(lake, s):
    src = lake.source(s, "customers", format="csv", options={"delimiter": "|"})
    lake.land("customers/part-0000", CUSTOMERS.replace(",", "|"))
    assert pipeline.run_source(s, src, "omar").status == "succeeded" and count(s, m.Party) == 2


def test_records_for_unknown_customers_are_rejected_or_given_a_placeholder(lake, s):
    lake.land("transactions/part-0.csv", txns(*[txn(f"T{i}", "C7") for i in range(3)]))
    strict = lake.source(s, "transactions", rules={"max_reject_rate": 1.0})
    run = pipeline.run_source(s, strict, "omar")
    assert run.stats["rejected"] == 3 and count(s, m.Transaction) == 0 and count(s, m.Party) == 0
    assert "customer C7 is not known" in s.scalars(select(m.IngestedObject)).one().stats["reject_samples"][0]["reason"]
    lake.land("transactions/part-1.csv", txns(*[txn(f"U{i}", "C7") for i in range(3)]))
    strict.rules = {"unknown_customer": "create"}
    run = pipeline.run_source(s, strict, "omar")
    assert run.status == "succeeded" and count(s, m.Transaction) == 3 and s.get(m.Party, {"tenant_id": T, "id": "C7"}).name == "Customer C7"


def test_a_transaction_on_someone_elses_account_is_rejected(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/part-0.csv", txns(*[txn(f"T{i}") for i in range(30)], txn("X1", cust="C2", acct="A1")))
    run = pipeline.run_many(s, sources, "omar")[2]
    assert run.stats["rejected"] == 1 and run.stats["loaded"] == 30 and s.get(m.Transaction, {"tenant_id": T, "id": "X1"}) is None


def test_a_failed_file_load_leaves_no_partial_rows(lake, s, monkeypatch):
    """A file that is held at load time (too many unknown customers) is rolled back entirely, earlier files stay."""
    sources = lake.standard(s)
    lake.land("transactions/a-good.csv", txns(*[txn(f"G{i}") for i in range(5)]))
    lake.land("transactions/b-mixed.csv", txns(txn("M1"), txn("M2"), txn("M3", "C9"), txn("M4", "C9")))
    run = pipeline.run_many(s, sources, "omar")[2]
    assert run.status == "partial" and count(s, m.Transaction) == 5
    assert s.get(m.Transaction, {"tenant_id": T, "id": "M1"}) is None and s.get(m.Party, {"tenant_id": T, "id": "C9"}) is None


def test_detection_runs_on_what_was_loaded_and_opens_cases(lake, s):
    sources = lake.standard(s)
    now = m.now()
    cash = [txn(f"S{i}", amount="9400.00", when=(now - timedelta(days=3) + timedelta(hours=20 * i)).strftime("%Y%m%d%H%M%S"), channel="CSH") for i in range(5)]
    lake.land("transactions/part-0.csv", txns(*cash))
    run = pipeline.run_many(s, sources, "omar")[2]
    assert run.stats["alerts"] >= 1 and run.stats["cases"] >= 1 and run.steps[-1]["step"] == "detect"
    case = s.scalars(select(m.Case)).first()
    assert case.party_id == "C1" and s.scalars(select(m.Alert)).first().scenario == "structuring"
    assert pipeline.run_source(s, sources[2], "omar").status == "nothing_new" and count(s, m.Case) == run.stats["cases"]


def test_lineage_goes_from_a_transaction_back_to_its_file(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/dt=2026-01-05/part-0.csv", txns(txn("T1")))
    run = pipeline.run_many(s, sources, "omar")[2]
    t = s.get(m.Transaction, {"tenant_id": T, "id": "T1"})
    origin = pipeline.lineage(s, t.feed_id)
    assert origin["object"] == lake.url + "transactions/dt=2026-01-05/part-0.csv" and origin["run_id"] == run.id
    assert origin["source"] == "transactions source" and origin["mapping_version"] == 1 and origin["fingerprint"]
    assert pipeline.lineage(s, "seed") is None


def test_a_missing_upstream_source_fails_and_downstream_is_skipped(lake, s):
    sources = lake.standard(s)
    lake.land("transactions/part-0.csv", txns(txn("T1")))
    with lake.db.platform() as p:  # the location stops being readable: the bucket was renamed, access was revoked
        row = p.get(m.Tenant, T)
        row.settings = dict(row.settings) | {"storage": [{"id": "landing", "label": "Landing", "url": "s3://", "options": {}}]}
        p.commit()
    from app.tenants.registry import runtime
    saved = runtime(T).settings
    runtime(T).settings = {"storage": [{"id": "landing", "label": "Landing", "url": "ftp://gone/", "options": {}}]}
    try:
        runs = pipeline.run_many(s, sources, "omar")
    finally:
        runtime(T).settings = saved
    assert [r.status for r in runs] == ["failed", "skipped", "skipped"] and "Unsupported" in runs[0].error
    assert "customers" in runs[1].error and count(s, m.Transaction) == 0


def test_schedules_and_staleness(lake, s):
    hourly = lake.source(s, "customers", schedule_minutes=60, rules={"max_age_hours": 24})
    manual = lake.source(s, "accounts")
    off = lake.source(s, "transactions", schedule_minutes=60, enabled=False)
    assert pipeline.health(hourly)["due"] and not pipeline.health(manual)["due"] and not pipeline.health(off)["due"]
    lake.land("customers/c.csv", CUSTOMERS)
    runs = pipeline.run_due(s)
    assert [r.source_id for r in runs] == [hourly.id] and runs[0].trigger == "schedule" and runs[0].started_by == "scheduler"
    assert not pipeline.health(hourly)["due"] and pipeline.run_due(s) == []
    hourly.last_run_at = m.now() - timedelta(minutes=61)
    assert pipeline.health(hourly)["due"] and not pipeline.health(hourly)["stale"]
    hourly.state = dict(hourly.state) | {"newest_object_at": (m.now() - timedelta(hours=25)).isoformat()}
    assert pipeline.health(hourly)["stale"]


# --- clients are kept apart ---------------------------------------------------------
def test_a_clients_pipeline_reads_only_its_own_location(db):
    a, b = Lake(db, T), Lake(db, OTHER, "fintech_payments")
    a.land("customers/c.csv", CUSTOMERS)
    b.land("customers/c.csv", CUSTOMERS.replace("Ada Lovelace", "Zed Quill"))
    for lake, expect in ((a, "Ada Lovelace"), (b, "Zed Quill")):
        with db.tenant(lake.tenant) as s:
            run = pipeline.run_source(s, lake.source(s, "customers"), "x")
            assert run.status == "succeeded" and s.get(m.Party, {"tenant_id": lake.tenant, "id": "C1"}).name == expect
            assert count(s, m.Party) == 2 and count(s, m.DataSource) == 1 and count(s, m.PipelineRun) == 1 and count(s, m.IngestedObject) == 1
            s.commit()
    with db.platform() as p:
        assert {r.tenant_id for r in p.scalars(select(m.IngestedObject))} == {T, OTHER}


def test_a_source_cannot_name_another_clients_location_or_climb_out_of_its_own(db):
    a, b = Lake(db, T), Lake(db, OTHER, "fintech_payments")
    b.land("customers/secret.csv", CUSTOMERS)
    with db.platform() as p:
        onboarding.set_storage(p, OTHER, [{"id": "landing", "url": b.url}, {"id": "vault", "url": b.url + "vault/"}])
        p.commit()
    with db.tenant(T) as s:
        for spec in ({"root": "vault"}, {"prefix": "../" + OTHER + "/customers"}, {"prefix": "customers/../../" + OTHER}):
            with pytest.raises(pipeline.PipelineError):
                a.source(s, "customers", **spec)
        assert pipeline.preview(T, {"dataset": "customers", "root": "landing", "prefix": "customers"})["matching"] == 0
        with pytest.raises(StorageError):
            locations.store_for(T, "vault")


def test_only_the_operator_registers_locations_and_directories_stay_inside_the_clients_folder(db, s=None):
    from app.config import settings

    db.onboard(T)
    home = (settings.lake_dir / T).resolve()
    with db.tenant(T) as tenant_session:
        with pytest.raises(TenantError):
            onboarding.set_storage(tenant_session, T, [{"id": "landing", "url": f"file://{home}/"}])  # not a platform session
    with db.platform() as p:
        for url, says in [("file:///etc/", "inside"), (f"file://{home}/../{OTHER}/", "inside"), (f"file://{settings.lake_dir.resolve()}/", "inside"),
                          ("ftp://host/x", "Unsupported"), ("s3://bucket/x?", None)]:
            if says is None:
                continue
            with pytest.raises(OnboardingError) as e:
                onboarding.set_storage(p, T, [{"id": "landing", "url": url}])
            assert says in str(e.value)
        with pytest.raises(OnboardingError):
            onboarding.set_storage(p, T, [{"id": "Bad Id", "url": f"file://{home}/"}])
        with pytest.raises(OnboardingError):
            onboarding.set_storage(p, T, [{"id": "landing", "url": "s3://bucket/x", "options": {"secret": "x"}}])
        with pytest.raises(OnboardingError):
            onboarding.set_storage(p, T, [{"id": "a1", "url": f"file://{home}/"}, {"id": "a1", "url": f"file://{home}/x/"}])
        rt = onboarding.set_storage(p, T, [{"id": "landing", "url": f"file://{home}/"}, {"id": "archive", "label": "Archive", "url": "s3://client-archive/meridian"}])
        assert [r["id"] for r in rt.settings["storage"]] == ["landing", "archive"] and rt.settings["storage"][1]["provider"].startswith("Amazon S3")
        p.commit()
    with db.tenant(T) as tenant_session:
        pipeline.save_source(tenant_session, {"name": "Archive feed", "dataset": "customers", "root": "archive", "mapping": MAPS["customers"]}, "omar")
        tenant_session.commit()
    with db.platform() as p:
        with pytest.raises(OnboardingError) as e:
            onboarding.set_storage(p, T, [{"id": "landing", "url": f"file://{home}/"}])
        assert "still read from: archive" in str(e.value)


def test_every_new_client_starts_with_its_own_landing_zone(db):
    from app.config import settings

    for tid in (T, OTHER):
        db.onboard(tid, template="fintech_payments" if tid == OTHER else "full_service_bank")
    roots = {tid: locations.roots(tid) for tid in (T, OTHER)}
    assert roots[T][0]["url"] == f"file://{(settings.lake_dir / T).resolve()}/" and roots[OTHER][0]["url"].endswith(f"/{OTHER}/")
    assert roots[T][0]["url"] != roots[OTHER][0]["url"]
