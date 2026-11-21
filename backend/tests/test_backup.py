"""Backup and restore: whole platform and one client, checksums, refusals, and the drill."""
from __future__ import annotations

import gzip

import pytest

import app.db as appdb
from app import migrations
from app.backup import BackupError, service
from app.db import make_engine
from app.storage import open_store


@pytest.fixture
def store(tmp_path):
    (tmp_path / "backups").mkdir()
    return open_store(f"file://{tmp_path / 'backups'}")


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "docs").mkdir()
    s = open_store(f"file://{tmp_path / 'docs'}")
    s.write("meridian/kyc/passport.pdf", b"%PDF-1.7 meridian")
    s.write("alder/kyc/id.pdf", b"%PDF-1.7 alder")
    return s


def fresh(tmp_path, name: str, to: str = "head"):
    eng = make_engine(f"sqlite:///{tmp_path / name}")
    migrations.upgrade(eng, to)
    return eng


def test_the_drill_restores_every_table_row_for_row(client, store):
    report = service.drill(store=store)
    assert report["ok"] and report["mismatched"] == [] and report["tables"] > 30 and report["rows"] > 1000
    assert report["restore_seconds"] >= 0 and report["source"] == "sqlite"
    assert service.drills(store)[0]["backup"] == report["backup"]
    assert [m.id for m in service.list_backups(store)] == [report["backup"]]


def test_a_whole_backup_restores_into_an_empty_database_with_its_documents(client, store, docs, tmp_path):
    m = service.create(store=store, evidence=docs)
    assert m.scope == "platform" and m.evidence["objects"] == 2 and m.tables["case"].rows > 0
    target = fresh(tmp_path, "restored.db")
    (tmp_path / "docs2").mkdir()
    docs2 = open_store(f"file://{tmp_path / 'docs2'}")
    out = service.restore(m.id, engine=target, store=store, evidence=docs2)
    assert out["rows"] == m.rows and out["documents"] == 2
    assert service.digests(target) == {n: c.digest for n, c in m.tables.items()}
    with docs2.open("meridian/kyc/passport.pdf") as f:
        assert f.read() == b"%PDF-1.7 meridian"


def test_restore_never_overwrites(client, store, tmp_path):
    m = service.create(store=store, with_documents=False)
    with pytest.raises(BackupError, match="already holds data"):
        service.restore(m.id, engine=appdb.engine, store=store)
    one = service.create(scope="meridian", store=store, with_documents=False)
    with pytest.raises(BackupError, match="already exists"):
        service.restore(one.id, engine=appdb.engine, store=store)


def test_restore_refuses_another_schema_revision(client, store, tmp_path):
    m = service.create(store=store, with_documents=False)
    older = fresh(tmp_path, "older.db", "0018")
    with pytest.raises(BackupError, match="schema 0018"):
        service.restore(m.id, engine=older, store=store)


def test_a_damaged_backup_is_caught_before_anything_is_written(client, store, tmp_path):
    m = service.create(store=store, with_documents=False)
    part = m.tables["case"].parts[0].key
    store.write(part, gzip.compress(b'{"id":"forged"}\n'))
    with pytest.raises(BackupError, match="checksum"):
        service.verify(m.id, store)
    target = fresh(tmp_path, "t.db")
    with pytest.raises(BackupError, match="damaged"):
        service.restore(m.id, engine=target, store=store)
    assert service.digests(target)["case"].startswith("0:")


def test_a_backup_without_its_manifest_does_not_exist(client, store):
    store.write("20260101T000000Z-platform/db/case/part-00000.jsonl.gz", gzip.compress(b""))
    assert service.list_backups(store) == []
    with pytest.raises(BackupError, match="no manifest"):
        service.load(store, "20260101T000000Z-platform")


def test_one_client_moves_to_another_database_beside_other_clients(client, store, docs, tmp_path):
    meridian = service.create(scope="meridian", store=store, evidence=docs)
    alder = service.create(scope="alder", store=store, evidence=docs)
    assert meridian.evidence["objects"] == 1 and alder.evidence["objects"] == 1
    assert set(meridian.tables) == set(alder.tables) and "tenant" in meridian.tables and meridian.tables["tenant"].rows == 1
    target = fresh(tmp_path, "stamp.db")
    service.restore(alder.id, engine=target, store=store)
    # Both clients' audit trails were numbered from the same sequence in the source; here they are numbered afresh.
    out = service.restore(meridian.id, engine=target, store=store)
    assert out["rows"] == meridian.rows
    assert service.digests(target, "meridian") == {n: c.digest for n, c in meridian.tables.items()}
    assert service.digests(target, "alder") == {n: c.digest for n, c in alder.tables.items()}
    assert service.digests(target, "kestrel")["case"].startswith("0:")


def test_backing_up_a_client_that_does_not_exist_is_refused(client, store):
    with pytest.raises(BackupError, match="No client"):
        service.create(scope="nobody", store=store)


def test_platform_staff_back_up_verify_and_drill_from_the_console(client, tmp_path):
    from app.config import settings

    (tmp_path / "console").mkdir()
    saved = settings.backup_url
    object.__setattr__(settings, "backup_url", f"file://{tmp_path / 'console'}")
    try:
        _console(client)
    finally:
        object.__setattr__(settings, "backup_url", saved)


def _console(client):
    op = {"X-Operator": "olivia"}
    assert client.get("/api/platform/backups").status_code == 403
    assert client.get("/api/platform/backups", headers={"X-Tenant": "meridian", "X-Persona": "maya"}).status_code == 403
    made = client.post("/api/platform/backups", json={"client": "kestrel", "documents": False}, headers=op).json()
    assert made["scope"] == "kestrel" and made["rows"] > 0
    assert client.post(f"/api/platform/backups/{made['id']}/verify", headers=op).json()["ok"]
    assert client.post("/api/platform/backups", json={"client": "nobody"}, headers=op).status_code == 422
    drill = client.post("/api/platform/backups/drill", headers=op).json()
    assert drill["ok"]
    listed = client.get("/api/platform/backups", headers=op).json()
    assert listed["location"] == "file" and len(listed["backups"]) == 2 and listed["drills"][0]["ok"]


def test_rows_round_trip_with_every_kind_of_time():
    """Dates, times of day and timestamps come back as the types their columns expect, not as text."""
    from datetime import date, datetime, time

    from sqlalchemy import Column, Date, DateTime, Integer, MetaData, String, Table, Time

    from app.backup.codec import decoder, encode

    t = Table("t", MetaData(), Column("id", Integer, primary_key=True), Column("born", Date), Column("opens", Time),
              Column("at", DateTime), Column("name", String))
    row = {"id": 1, "born": date(2002, 8, 26), "opens": time(9, 30), "at": datetime(2026, 11, 21, 19, 40, 5), "name": "x"}
    assert decoder(t)(encode(row)) == row
    assert decoder(t)(encode(row | {"born": None, "opens": None, "at": None})) == row | {"born": None, "opens": None, "at": None}
