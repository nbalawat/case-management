"""Offboarding: a leaving client is handed everything it has here, checked, and then erased everywhere, and nobody else is touched.

One client is onboarded with sample data, given a document, a lake history and event topics, and then taken through
the three steps over HTTP. The checks read the stores and the database directly, not through the code under test.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from datetime import datetime

import pytest
from sqlalchemy import func, select

import app.db as appdb
from app import keys
from app.backbone import bus as busmod
from app.config import settings
from app.domain import models as m
from app.storage import open_store
from app.tenants import offboarding
from test_client_api import CLIENTS, OP, h

LEAVER, NAME = "harbor", "Harbor Mutual"
SECRET = b"Passport 553201197, 4 Quay Street"


def _rows(tenant: str) -> dict[str, int]:
    """Row counts per client table, read with a platform session so nothing filters by client."""
    out = {}
    with appdb.session_scope(platform=True) as s:
        for model in offboarding.client_models():
            out[model.__table__.name] = s.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant)) or 0
    return out


def _objects(url: str) -> list[str]:
    return [o.key for o in open_store(url).list()]


@pytest.fixture(scope="module")
def leaver(client, tmp_path_factory):
    """A client with something in every place the platform keeps data, and the lake and bus switched on for the test."""
    lake = tmp_path_factory.mktemp("lake-catalog")
    saved = settings.lake_catalog, settings.event_bus_url
    object.__setattr__(settings, "lake_catalog", json.dumps({"type": "sql", "uri": f"sqlite:///{lake / 'catalog.db'}", "warehouse": f"file://{lake / 'wh'}"}))
    object.__setattr__(settings, "event_bus_url", "memory://")
    busmod.reset_memory()
    r = client.post("/api/platform/tenants", json={"id": LEAVER, "name": NAME, "template": "fintech_payments", "sample_data": True}, headers=OP)
    assert r.status_code == 201, r.text
    with appdb.session_scope(LEAVER) as s:
        party = s.scalars(select(m.Party.id).order_by(m.Party.id)).first()
        worker = "nia"
    up = client.post(f"/api/customers/{party}/evidence", headers=h(LEAVER, worker), json={
        "kind": "proof_of_address", "title": "Passport", "filename": "passport.txt", "media_type": "text/plain", "content_base64": base64.b64encode(SECRET).decode()})
    assert up.status_code in (200, 201), up.text
    (settings.lake_dir / LEAVER / "incoming").mkdir(parents=True, exist_ok=True)
    (settings.lake_dir / LEAVER / "incoming" / "customers.csv").write_text("CUST_NO,CUST_NM\nC1,Harbor customer\n")
    from app.lake.iceberg import connect

    for tenant in (LEAVER, "meridian"):
        connect().append(tenant, "case_events", [{"event_id": f"e-{tenant}", "type": "case.opened", "case_id": "C1", "actor": "x", "actor_kind": "human",
                                                  "ts": datetime(2026, 1, 5, 9), "payload": "{}"}], {})
    bus = busmod.connect()
    from app.backup import service as backups

    for scope in (LEAVER, "meridian"):  # a backup of this client alone, and one of another client
        backups.create(scope=scope)
    bus.ensure_topics([f"{settings.event_topic_prefix}.{LEAVER}.case-events", f"{settings.event_topic_prefix}.meridian.case-events"])
    yield {"party": party, "worker": worker}
    object.__setattr__(settings, "lake_catalog", saved[0])
    object.__setattr__(settings, "event_bus_url", saved[1])
    busmod.reset_memory()


def test_only_the_operator_offboards(client, leaver):
    for path in (f"/api/platform/offboarding/{LEAVER}/export", f"/api/platform/offboarding/{LEAVER}/erase"):
        assert client.post(path, json={"confirm": NAME}, headers=h("meridian", "omar")).status_code == 403
        assert client.post(path, json={"confirm": NAME}).status_code == 403
    assert client.get("/api/platform/offboarding", headers=h(LEAVER, leaver["worker"])).status_code == 403


def test_a_live_client_cannot_be_exported_or_erased(client, leaver):
    r = client.post(f"/api/platform/offboarding/{LEAVER}/export", headers=OP)
    assert r.status_code == 422 and "Suspend" in r.json()["detail"]
    r = client.post(f"/api/platform/offboarding/{LEAVER}/erase", json={"confirm": NAME}, headers=OP)
    assert r.status_code == 422 and "not suspended" in r.json()["detail"]


def test_the_export_holds_every_row_and_document_and_is_sealed_at_rest(client, leaver):
    assert client.post(f"/api/platform/tenants/{LEAVER}/status", json={"status": "suspended"}, headers=OP).status_code == 200
    r = client.post(f"/api/platform/offboarding/{LEAVER}/export", headers=OP)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "exported" and rec["export_bytes"] > 0
    # Before the hand-over, erasing is refused.
    refused = client.post(f"/api/platform/offboarding/{LEAVER}/erase", json={"confirm": NAME}, headers=OP)
    assert refused.status_code == 422 and "download the bundle first" in refused.json()["detail"]
    stored = open_store(f"{settings.exports_url}/{LEAVER}/")
    with stored.open(f"{rec['id']}.zip") as f:
        raw = f.read()
    assert keys.is_sealed(raw) and SECRET not in raw  # sealed under the client's key, like its documents
    got = client.get(f"/api/platform/offboarding/exports/{rec['id']}/download", headers=OP)
    assert got.status_code == 200 and got.headers["content-type"] == "application/zip"
    manifest, z = offboarding.read_bundle(got.content)  # every file matches its SHA-256 in the manifest
    want = _rows(LEAVER)
    assert manifest["tables"] == {k: v for k, v in want.items() if k != "access_event"} | {"access_event": manifest["tables"]["access_event"]}
    for table, n in want.items():
        if table != "access_event":
            assert len(z.read(f"tables/{table}.jsonl").splitlines()) == n, table
    parties = [json.loads(line) for line in z.read("tables/party.jsonl").splitlines()]
    assert leaver["party"] in {p["id"] for p in parties} and {p["tenant_id"] for p in parties} == {LEAVER}
    docs = [n for n in z.namelist() if n.startswith("documents/")]
    assert len(docs) == 1 and z.read(docs[0]) == SECRET  # decrypted for the client
    assert z.read("landing/incoming/customers.csv").startswith(b"CUST_NO")
    assert manifest["lake"] == {"case_events": 1} and b"e-harbor" in z.read("lake/case_events.jsonl") and b"e-meridian" not in z.read("lake/case_events.jsonl")
    assert manifest["client"]["name"] == NAME and "README.txt" in z.namelist()
    for t in CLIENTS:  # nothing of anyone else
        assert all(f'"tenant_id":"{t}"' not in z.read(n).decode() for n in z.namelist() if n.startswith("tables/"))
    listed = client.get("/api/platform/offboarding", headers=OP, params={"client": LEAVER}).json()["offboardings"]
    assert listed[0]["status"] == "handed_over" and listed[0]["downloaded_by"]


def test_erasing_needs_the_clients_name_and_an_unchanged_export(client, leaver):
    r = client.post(f"/api/platform/offboarding/{LEAVER}/erase", json={"confirm": "harbor"}, headers=OP)
    assert r.status_code == 422 and NAME in r.json()["detail"]
    with appdb.session_scope(LEAVER) as s:  # something written after the export (a pipeline, say)
        s.add(m.Party(id="LATE1", name="Late arrival", country="US"))
    r = client.post(f"/api/platform/offboarding/{LEAVER}/erase", json={"confirm": NAME}, headers=OP)
    assert r.status_code == 422 and "party" in r.json()["detail"] and "Export again" in r.json()["detail"]
    with appdb.session_scope(LEAVER) as s:
        s.delete(s.scalars(select(m.Party).where(m.Party.id == "LATE1")).one())
    assert _rows(LEAVER)["party"] > 0


def test_a_failure_outside_the_database_stops_before_anything_irreversible(client, leaver, monkeypatch):
    def broken(_tenant):
        raise RuntimeError("search cluster unreachable")

    monkeypatch.setattr(offboarding, "OUTSIDE", offboarding.OUTSIDE[:2] + (offboarding.Eraser("search", "Search index", broken),) + offboarding.OUTSIDE[3:])
    before = _rows(LEAVER)
    r = client.post(f"/api/platform/offboarding/{LEAVER}/erase", json={"confirm": NAME}, headers=OP)
    assert r.status_code == 422 and "search index" in r.json()["detail"] and "Nothing in the database was removed" in r.json()["detail"]
    assert _rows(LEAVER) == before and not keys.provider().info(LEAVER).destroyed
    assert _objects(f"{settings.evidence_url}/{LEAVER}/")  # the documents are still there
    rec = client.get("/api/platform/offboarding", headers=OP, params={"client": LEAVER}).json()["offboardings"][0]
    assert rec["status"] == "handed_over" and "unreachable" in rec["error"]
    assert rec["erased"]["events"] == {"topics": 1}  # what ran before the failure is recorded, and is safe to run again


def test_erasing_removes_the_client_everywhere_and_leaves_everyone_else(client, leaver):
    others = {t: _rows(t) for t in CLIENTS}
    other_docs = {t: _objects(f"{settings.evidence_url}/{t}/") for t in CLIENTS}
    r = client.post(f"/api/platform/offboarding/{LEAVER}/erase", json={"confirm": NAME}, headers=OP)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "completed" and rec["completed_by"] and rec["error"] == ""
    # The database: no row of this client is left in any client table, and the client itself is gone.
    assert set(_rows(LEAVER).values()) == {0}
    assert rec["erased"]["database"]["party"] > 0 and rec["erased"]["database"]["case"] > 0
    with appdb.session_scope(platform=True) as s:
        assert s.get(m.Tenant, LEAVER) is None
        assert s.get(m.Offboarding, rec["id"]).export_sha256 == rec["export_sha256"]  # the provider's proof stays
    # Every store: documents, landing zone, quarantine, the bundle itself.
    for url in (settings.evidence_url, settings.quarantine_url, settings.exports_url, f"file://{settings.lake_dir}"):
        assert _objects(f"{url}/{LEAVER}/") == [], url
        if url.startswith("file://"):
            assert not (Path(url.removeprefix("file://")) / LEAVER).exists(), url  # not even an empty folder named for it
    # The key is destroyed, so any copy of a document anywhere is unreadable.
    assert keys.provider().info(LEAVER).destroyed and rec["erased"]["key"]["destroyed"]
    # The lake and the bus: this client's history and topics, and nobody else's.
    from app.lake.iceberg import connect

    assert connect().tables(LEAVER) == [] and [t.name for t in connect().tables("meridian")] == ["case_events"]
    topics = busmod.connect().topics()
    assert not any(f".{LEAVER}." in t for t in topics) and f"{settings.event_topic_prefix}.meridian.case-events" in topics
    # The topics went in the stopped run above (the bus comes before search), and running again is harmless.
    assert rec["erased"]["lake"] == {"tables": 1} and rec["erased"]["events"] == {"topics": 0}
    if settings.temporal_url:  # the suite on Temporal: the client's workflow history went with it
        assert rec["erased"]["workflows"]["namespace"] and rec["erased"]["workflows"]["deleted_namespace"] in (True, False)
    else:
        assert rec["erased"]["workflows"] == {"configured": False}
    # What could not be removed is said, not hidden.
    assert any(x["what"] == "Whole-platform backups" for x in rec["remains"])
    assert rec["erased"]["backups"] == {"backups": 1}
    from app.backup import service as backups

    scopes = [b.scope for b in backups.list_backups()]
    assert LEAVER not in scopes and "meridian" in scopes
    # The client is refused like any unknown one, and the other clients are exactly as they were.
    assert client.get("/api/cases", headers=h(LEAVER, leaver["worker"])).status_code == 404
    assert {t: _rows(t) for t in CLIENTS} == others
    assert {t: _objects(f"{settings.evidence_url}/{t}/") for t in CLIENTS} == other_docs
    assert all(not keys.provider().info(t).destroyed for t in CLIENTS)
    assert LEAVER not in {c["tenant"] for c in client.get("/api/platform/usage", headers=OP).json()["clients"]}


def test_a_finished_offboarding_cannot_be_downloaded_or_run_again(client, leaver):
    rec = client.get("/api/platform/offboarding", headers=OP, params={"client": LEAVER}).json()["offboardings"][0]
    assert client.get(f"/api/platform/offboarding/exports/{rec['id']}/download", headers=OP).status_code == 422
    assert client.post(f"/api/platform/offboarding/{LEAVER}/erase", json={"confirm": NAME}, headers=OP).status_code == 422
    # A client id that has been offboarded cannot quietly come back with its old key: the keyring refuses it.
    with pytest.raises(keys.KeyServiceError):
        keys.provider().create(LEAVER)


def test_the_memory_bus_deletes_only_the_topics_named():
    b = busmod.MemoryBus()
    b.ensure_topics(["a.x.one", "a.y.one"])
    assert b.delete_topics(["a.x.one", "a.z.none"]) == 1 and b.topics() == ["a.y.one"]
