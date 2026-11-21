"""Lineage: OpenLineage events for pipeline runs, stream consumers, the lake writer, backfills and backups; where they
are sent; and the graph the UI draws from them."""
import json
import uuid

import pytest
from sqlalchemy import select

from app.backbone import topics
from app.backbone.streams import StreamConsumer
from app.domain import models as m
from app.ingest import pipeline
from app.lake.iceberg import IcebergStore
from app.lake.sink import LakeSink
from app.lineage import jobs
from app.lineage.events import Dataset, build, files, lake_table, record, table
from app.lineage.graph import event, events, graph
from app.lineage.tally import Tally
from app.lineage.transport import send
from support import T
from test_backbone import customer, txn, world  # noqa: F401  (world is a fixture)
from test_backbone import send as publish
from test_pipelines import Lake, txns
from test_pipelines import txn as file_txn


def kept(s, job=None):
    q = select(m.LineageEvent).order_by(m.LineageEvent.id)
    return [e for e in s.scalars(q) if job is None or e.job == job]


# ---- the events ---------------------------------------------------------------------------------------------------

def test_an_event_has_the_openlineage_shape():
    ev = build(T, "pipeline.x", inputs=[files("s3://bucket/landing/", "transactions")], outputs=[table(T, "txn", rows=12)], error=None,
               extra={"pipelineRun": "run-1"})
    assert ev["eventType"] == "COMPLETE" and ev["schemaURL"].endswith("/2-0-2/OpenLineage.json#/$defs/RunEvent")
    uuid.UUID(ev["run"]["runId"])
    assert ev["eventTime"].endswith("Z") and ev["job"] == ev["job"] | {"namespace": f"caseforge.{T}", "name": "pipeline.x"}
    assert ev["job"]["facets"]["jobType"]["processingType"] == "BATCH"
    assert ev["inputs"] == [{"namespace": "s3://bucket", "name": "landing/transactions", "facets": {}}]
    out = ev["outputs"][0]
    assert (out["namespace"], out["name"], out["outputFacets"]["outputStatistics"]["rowCount"]) == (f"caseforge://{T}", "txn", 12)
    assert ev["run"]["facets"]["caseforge"]["pipelineRun"] == "run-1"
    failed = build(T, "pipeline.x", inputs=[], outputs=[], state="FAIL", error="the bucket is gone")
    assert failed["run"]["facets"]["errorMessage"]["message"] == "the bucket is gone"


@pytest.mark.parametrize("url, key, namespace, name", [
    ("s3://AKIA:secret@bucket/landing/", "tx", "s3://bucket", "landing/tx"),
    ("abfss://raw@acct.dfs.core.windows.net/in/", "", "abfss://raw@acct.dfs.core.windows.net", "in"),
    ("gs://b/", "x/y.csv", "gs://b", "x/y.csv"),
    ("file:///home/someone/lake/meridian/", "", "file://", "home/someone/lake/meridian"),
])
def test_object_store_datasets_never_carry_credentials(url, key, namespace, name):
    d = files(url, key)
    assert (d.namespace, d.name) == (namespace, name) and "secret" not in d.namespace + d.name


def test_events_go_to_a_file_and_failures_never_raise(tmp_path, monkeypatch):
    ev = build(T, "job", inputs=[], outputs=[])
    path = tmp_path / "events.jsonl"
    assert send(ev, f"file://{path}") and send(ev, f"file://{path}")
    assert [json.loads(line)["job"]["name"] for line in path.read_text().splitlines()] == ["job", "job"]
    assert send(ev, "http://127.0.0.1:9/api/v1/lineage") is False  # nothing listens there
    assert send(ev, "ftp://x/") is False and send(ev, "") is False
    monkeypatch.setenv("LINEAGE_URL", f"file://{path}")
    assert send(ev)


def test_local_directories_are_named_from_the_platforms_own_folders():
    from app.config import ROOT, settings

    d = files(f"file://{settings.lake_dir}/meridian/", "core-banking/customers")
    assert (d.namespace, d.name) == ("file://caseforge-lake", "meridian/core-banking/customers")
    assert files(f"file://{ROOT}/data/backups/").namespace == "file://caseforge-data"


# ---- the jobs that record them --------------------------------------------------------------------------------------

@pytest.fixture
def lake(db):
    return Lake(db, T)


def test_each_pipeline_run_records_what_it_read_and_wrote(lake):
    with lake.db.tenant(T) as s:
        sources = lake.standard(s)
        lake.land("transactions/part-0.csv", txns(file_txn("T1"), file_txn("T2", "C2")))
        pipeline.run_many(s, sources, "omar")
        evs = kept(s)
        assert [e.job for e in evs] == ["pipeline.customers source", "pipeline.accounts source", "pipeline.transactions source"]
        last = evs[-1]
        assert last.state == "COMPLETE" and last.inputs[0]["name"] == f"{T}/transactions" and last.inputs[0]["namespace"].startswith("memory://")
        assert {o["name"]: o["rows"] for o in last.outputs} == {"txn": 2}
        assert last.event["run"]["facets"]["caseforge"]["status"] == "succeeded"
        assert pipeline.run_source(s, sources[2], "omar").status == "nothing_new" and len(kept(s)) == 3  # nothing ran, nothing recorded


def test_a_failed_run_is_recorded_as_failed(lake, monkeypatch):
    with lake.db.tenant(T) as s:
        src = lake.source(s, "customers")
        monkeypatch.setattr(pipeline, "discover", lambda *a: (_ for _ in ()).throw(pipeline.StorageError("access denied")))
        assert pipeline.run_source(s, src, "omar").status == "failed"
        (ev,) = kept(s)
        assert ev.state == "FAIL" and ev.event["run"]["facets"]["errorMessage"]["message"] == "access denied"


def test_stream_consumers_tally_rows_and_record_once_per_flush(world):
    customer(world)
    publish(world, topics.TRANSACTIONS, [txn(i, amount=9100 + i) for i in range(4)])
    c = StreamConsumer(world.bus, topics.TRANSACTIONS)
    out = c.step()[T]
    with world.tenant(T) as s:
        assert kept(s) == []  # a minute has not passed
    assert c.lineage.flush() == 1 and c.lineage.flush() == 0
    with world.tenant(T) as s:
        (ev,) = kept(s)
        assert ev.job == "stream.transactions" and ev.event["job"]["facets"]["jobType"]["processingType"] == "STREAMING"
        assert ev.inputs == [{"namespace": "kafka://local", "name": topics.topic(T, topics.TRANSACTIONS), "kind": "topic"}]
        assert {o["name"]: o["rows"] for o in ev.outputs} == {"txn": 4, "alert": out.alerts}


def test_the_lake_writer_tallies_per_client_and_table(world, tmp_path):
    store = IcebergStore({"type": "sql", "uri": f"sqlite:///{tmp_path / 'c.db'}", "warehouse": f"file://{tmp_path / 'w'}"})
    publish(world, topics.TRANSACTIONS, [txn(i) for i in range(3)])
    sink = LakeSink(world.bus, store)
    sink.step()
    assert sink.lineage.flush() == 1
    with world.tenant(T) as s:
        (ev,) = kept(s)
        assert ev.job == "lake.writer" and ev.outputs == [{"namespace": lake_table(T, "x").namespace, "name": "transactions", "kind": "lake", "rows": 3}]


def test_a_tally_is_due_after_its_interval():
    t = Tally("j", every_seconds=0)
    assert t.due()
    assert not Tally("j", every_seconds=3600).due()


def test_lineage_failures_never_stop_the_job(lake, monkeypatch):
    import app.lineage.jobs as j

    monkeypatch.setattr(j, "record", lambda *a, **kw: 1 / 0)
    with lake.db.tenant(T) as s:
        lake.land("customers/c.csv", "CUST_NO,CUST_NM,RISK_CD,PEP_FLAG,RES_CNTRY,OPEN_DT\nC1,Ada,H,Y,GB,20200101\n")
        assert pipeline.run_source(s, lake.source(s, "customers"), "omar").status == "succeeded"


def test_backfills_and_backups_are_recorded(db):
    db.onboard(T)
    with db.tenant(T) as s:
        jobs.lake_backfill(s, T, 120)
        jobs.backup(s, T, {"case": 3, "alert": 5}, files("s3://backups/caseforge/"), "20261121T000000Z-" + T)
        s.flush()
        b, k = kept(s)
        assert (b.job, b.outputs[0]["rows"]) == ("lake.backfill", 120)
        assert k.job == "backup.create" and k.outputs[0] == {"namespace": "s3://backups", "name": "caseforge", "kind": "backup", "rows": 8}


# ---- the graph -------------------------------------------------------------------------------------------------------

def test_the_graph_lays_out_sources_jobs_and_ends(db):
    db.onboard(T)
    land, txn_t, topic_d, lake_t = files("s3://bucket/in/"), table(T, "txn"), Dataset("kafka://k", "t", "topic"), lake_table(T, "transactions")
    with db.tenant(T) as s:
        record(s, "pipeline.txns", inputs=[land], outputs=[table(T, "txn", rows=5)])
        record(s, "pipeline.txns", inputs=[land], outputs=[table(T, "txn", rows=7)])
        record(s, "stream.transactions", inputs=[topic_d], outputs=[table(T, "txn", rows=2)])
        record(s, "lake.writer", inputs=[topic_d], outputs=[lake_table(T, "transactions", rows=9)])
        record(s, "lake.backfill", inputs=[txn_t], outputs=[lake_table(T, "transactions", rows=12)])
        record(s, "pipeline.txns", inputs=[land], outputs=[], state="FAIL", error="x")
        s.flush()
        g = graph(s)
        by = {n["id"]: n for n in g["nodes"]}
        assert by["job:pipeline.txns"] | {} == by["job:pipeline.txns"] and (by["job:pipeline.txns"]["runs"], by["job:pipeline.txns"]["failed"]) == (3, 1)
        assert by[f"ds:caseforge://{T}|txn"]["rows_written"] == 14
        assert by[f"ds:{lake_t.namespace}|transactions"]["rows_written"] == 21
        layer = {n["id"]: n["layer"] for n in g["nodes"]}
        assert layer["ds:s3://bucket|in"] == 0 and layer["job:pipeline.txns"] == 1 and layer[f"ds:caseforge://{T}|txn"] == 2
        assert layer["job:lake.backfill"] == 3 and layer[f"ds:{lake_t.namespace}|transactions"] == 4
        edge = next(e for e in g["edges"] if e["from"] == "job:pipeline.txns")
        assert (edge["rows"], edge["runs"]) == (12, 2)
        listed = events(s, job="lake.writer")
        assert len(listed) == 1 and event(s, listed[0]["id"])["job"]["name"] == "lake.writer" and event(s, 10**9) is None


def test_a_job_that_reads_and_writes_the_same_table_does_not_loop(db):
    db.onboard(T)
    with db.tenant(T) as s:
        record(s, "dedupe", inputs=[table(T, "party")], outputs=[table(T, "party", rows=1)])
        s.flush()
        assert {n["layer"] for n in graph(s)["nodes"]} <= {0, 1}


def test_api(client):
    h = {"X-Tenant": "meridian", "X-Persona": "maya"}
    assert client.get("/api/lineage/graph", headers={"X-Tenant": "meridian", "X-Persona": "ivan"}).status_code == 403
    g = client.get("/api/lineage/graph", headers=h)
    assert g.status_code == 200 and {"nodes", "edges", "days", "sent_to"} <= set(g.json())
    names = {n["name"] for n in g.json()["nodes"]}
    assert {"pipeline.Core banking transactions", "stream.transactions", "lake.writer", "backup.create", "txn", "transactions"} <= names
    evs = client.get("/api/lineage/events?job=pipeline.Core banking transactions", headers=h).json()["events"]
    assert any(e["state"] == "FAIL" for e in evs)
    one = client.get(f"/api/lineage/events/{evs[0]['id']}", headers=h).json()
    assert one["run"]["facets"]["caseforge"]["demo"] is True
    assert client.get("/api/lineage/events/999999", headers=h).status_code == 404
