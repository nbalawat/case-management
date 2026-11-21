"""What a starting application does to a database made by another version of itself."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

import app.db as appdb
from app.config import settings
from app.db import SCHEMA_VERSION, SchemaMismatch, ensure_schema, make_engine


@pytest.fixture
def old(tmp_path):
    """A database one schema version behind, holding one row that matters."""
    eng = make_engine(f"sqlite:///{tmp_path}/old.db")
    ensure_schema(eng)
    with eng.begin() as c:
        c.execute(text("UPDATE schema_version SET version = :v"), {"v": SCHEMA_VERSION - 1})
        cols = [col for col in inspect(c).get_columns("tenant") if not col["nullable"] or col["name"] == "id"]
        values = {col["name"]: "kept" if col["name"] == "id" else "2026-01-01 00:00:00" if "DATE" in str(col["type"]).upper() else
                  "{}" if "JSON" in str(col["type"]).upper() else 1 if any(t in str(col["type"]).upper() for t in ("INT", "BOOL", "FLOAT")) else "x" for col in cols}
        c.execute(text(f"INSERT INTO tenant ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"), values)
    yield eng
    object.__setattr__(settings, "schema_on_mismatch", "rebuild")
    eng.dispose()


def tenants(eng):
    with eng.connect() as c:
        return [r[0] for r in c.execute(text("SELECT id FROM tenant"))]


def test_told_to_refuse_it_stops_and_changes_nothing(old):
    object.__setattr__(settings, "schema_on_mismatch", "refuse")
    with pytest.raises(SchemaMismatch, match=f"version {SCHEMA_VERSION - 1} and the application expects {SCHEMA_VERSION}. Nothing was changed"):
        ensure_schema(old)
    assert tenants(old) == ["kept"]
    with old.connect() as c:
        assert c.execute(text("SELECT version FROM schema_version")).scalar() == SCHEMA_VERSION - 1


def test_anything_but_the_word_rebuild_refuses(old):
    for word in ("", "REBUILD", "yes", "refuse"):
        object.__setattr__(settings, "schema_on_mismatch", word)
        with pytest.raises(SchemaMismatch):
            ensure_schema(old)
    assert tenants(old) == ["kept"]


def test_the_demo_default_rebuilds(old):
    assert settings.schema_on_mismatch == "rebuild"
    ensure_schema(old)
    assert tenants(old) == []
    with old.connect() as c:
        assert c.execute(text("SELECT version FROM schema_version")).scalar() == SCHEMA_VERSION


def test_a_database_at_the_right_version_or_an_empty_one_starts_either_way(tmp_path):
    object.__setattr__(settings, "schema_on_mismatch", "refuse")
    try:
        eng = make_engine(f"sqlite:///{tmp_path}/new.db")
        ensure_schema(eng)  # empty: created
        ensure_schema(eng)  # current: left alone
        assert "tenant" in inspect(eng).get_table_names()
    finally:
        object.__setattr__(settings, "schema_on_mismatch", "rebuild")
        eng.dispose()
    assert appdb.SCHEMA_VERSION == SCHEMA_VERSION
