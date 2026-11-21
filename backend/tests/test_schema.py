"""Versioned migrations: what a starting application does to an empty database, a current one, an old one."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from app import migrate, migrations
from app.config import settings
from app.db import SCHEMA_VERSION, Base, SchemaMismatch, ensure_schema, make_engine
from app.domain import models  # noqa: F401


@pytest.fixture
def eng(tmp_path):
    e = make_engine(f"sqlite:///{tmp_path}/schema.db")
    yield e
    object.__setattr__(settings, "schema_on_mismatch", "rebuild")
    object.__setattr__(settings, "migrate_on_start", True)
    e.dispose()


def keep_a_row(eng):
    """One row that matters, built from whatever columns the table has."""
    with eng.begin() as c:
        cols = [col for col in inspect(c).get_columns("tenant") if not col["nullable"] or col["name"] == "id"]
        values = {col["name"]: "kept" if col["name"] == "id" else "2026-01-01 00:00:00" if "DATE" in str(col["type"]).upper() else
                  "{}" if "JSON" in str(col["type"]).upper() else 1 if any(t in str(col["type"]).upper() for t in ("INT", "BOOL", "FLOAT")) else "x" for col in cols}
        c.execute(text(f"INSERT INTO tenant ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"), values)


def legacy(eng, version):
    """A database as the application made them before migrations: the baseline's tables, and a version number."""
    migrations.upgrade(eng, migrations.BASELINE)
    with eng.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))
        c.execute(text("CREATE TABLE schema_version (version INTEGER NOT NULL)"))
        c.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": version})
    keep_a_row(eng)


def tenants(eng):
    with eng.connect() as c:
        return [r[0] for r in c.execute(text("SELECT id FROM tenant"))]


def at(eng):
    with eng.connect() as c:
        return migrations.current(c)


# ---- the guard: nobody changes a model without writing a migration

def test_the_models_match_the_newest_migration():
    scratch = make_engine("sqlite://")
    migrations.upgrade(scratch)
    assert migrations.drift(scratch) == [], "a model changed without a migration: run `python -m app.migrate revision \"what changed\"`"


def test_migrations_build_exactly_the_tables_the_models_describe(eng):
    ensure_schema(eng)
    built = set(inspect(eng).get_table_names()) - {"alembic_version"}
    assert built == set(Base.metadata.tables) and at(eng) == migrations.head()
    with make_engine("sqlite://").connect() as c:
        Base.metadata.create_all(c)
        direct = {t: {col["name"] for col in inspect(c).get_columns(t)} for t in Base.metadata.tables}
    assert {t: {col["name"] for col in inspect(eng).get_columns(t)} for t in built} == direct


def test_the_migrations_form_one_line_from_the_baseline():
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(migrations.config())
    line = [r.revision for r in scripts.walk_revisions()][::-1]
    assert line[0] == migrations.BASELINE == f"{SCHEMA_VERSION:04d}" and len(scripts.get_heads()) == 1
    assert line == sorted(line) and all(len(r) == 4 and r.isdigit() for r in line)


# ---- starting up

def test_an_empty_database_is_built_and_a_current_one_is_left_alone(eng):
    ensure_schema(eng)
    keep_a_row(eng)
    ensure_schema(eng)
    ensure_schema(eng)
    assert tenants(eng) == ["kept"] and at(eng) == migrations.head()


def test_starting_never_needs_the_rebuild_permission_when_nothing_is_wrong(eng):
    object.__setattr__(settings, "schema_on_mismatch", "refuse")
    object.__setattr__(settings, "migrate_on_start", False)
    ensure_schema(eng)  # empty: built, whatever the settings
    ensure_schema(eng)
    assert at(eng) == migrations.head()


def test_a_database_from_just_before_migrations_is_adopted_with_its_data(eng):
    legacy(eng, SCHEMA_VERSION)
    object.__setattr__(settings, "schema_on_mismatch", "refuse")
    ensure_schema(eng)
    assert tenants(eng) == ["kept"] and at(eng) == migrations.head() and migrations.drift(eng) == []


def test_an_older_database_is_refused_and_nothing_is_changed(eng):
    legacy(eng, SCHEMA_VERSION - 1)
    object.__setattr__(settings, "schema_on_mismatch", "refuse")
    with pytest.raises(SchemaMismatch, match=f"holds schema version {SCHEMA_VERSION - 1}.*Nothing was changed"):
        ensure_schema(eng)
    assert tenants(eng) == ["kept"] and "alembic_version" not in inspect(eng).get_table_names()


def test_anything_but_the_word_rebuild_refuses(eng):
    legacy(eng, SCHEMA_VERSION - 1)
    for word in ("", "REBUILD", "yes", "refuse"):
        object.__setattr__(settings, "schema_on_mismatch", word)
        with pytest.raises(SchemaMismatch):
            ensure_schema(eng)
    assert tenants(eng) == ["kept"]


def test_the_demo_default_rebuilds_an_older_database(eng):
    legacy(eng, SCHEMA_VERSION - 1)
    assert settings.schema_on_mismatch == "rebuild"
    ensure_schema(eng)
    assert tenants(eng) == [] and at(eng) == migrations.head() and "schema_version" not in inspect(eng).get_table_names()


def test_a_database_that_is_behind_is_migrated_at_start_or_refused_when_told_to_wait(eng, monkeypatch):
    ensure_schema(eng)
    keep_a_row(eng)
    ran, here = [], at(eng)
    monkeypatch.setattr(migrations, "head", lambda: "0099")  # as if this code were newer than the database
    monkeypatch.setattr(migrations, "upgrade", lambda e, to="head": ran.append(to))
    object.__setattr__(settings, "migrate_on_start", False)
    with pytest.raises(SchemaMismatch, match=f"is at migration {here} and the application expects 0099. Nothing was changed. Run `python -m app.migrate`"):
        ensure_schema(eng)
    assert ran == [] and tenants(eng) == ["kept"]
    object.__setattr__(settings, "migrate_on_start", True)
    ensure_schema(eng)
    assert ran == ["head"] and tenants(eng) == ["kept"]


def test_tables_another_tool_keeps_in_the_database_do_not_count_as_ours(eng):
    with eng.begin() as c:
        c.execute(text("CREATE TABLE iceberg_tables (catalog_name TEXT)"))  # an Iceberg SQL catalog sharing the database
    ensure_schema(eng)
    assert at(eng) == migrations.head() and migrations.drift(eng) == [] and "iceberg_tables" in inspect(eng).get_table_names()


# ---- the command

def test_the_command_checks_the_models_and_says_so(capsys):
    assert migrate.main(["check"]) == 0 and "match the newest migration" in capsys.readouterr().out


def test_the_command_writes_nothing_when_nothing_changed(capsys):
    before = sorted(p.name for p in (migrations.HERE / "versions").glob("*.py"))
    assert migrate.main(["revision", "nothing really"]) == 0 and "Nothing to write" in capsys.readouterr().out
    assert sorted(p.name for p in (migrations.HERE / "versions").glob("*.py")) == before


@pytest.mark.parametrize("argv", [["nonsense"], ["revision"], ["revision", "  "]])
def test_the_command_explains_itself(argv, capsys):
    assert migrate.main(argv) == 2 and "python -m app.migrate" in capsys.readouterr().out
