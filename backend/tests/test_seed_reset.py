"""`make seed` starts again from nothing: the tables are dropped and the migrations build them again."""
from sqlalchemy import create_engine, inspect, text

from app.db import ensure_schema
from app.seed import rebuild_schema


def test_rebuilding_drops_the_migration_marker_and_builds_every_table(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'reset.db'}")
    ensure_schema(eng)
    before = set(inspect(eng).get_table_names())
    assert "tenant" in before and "alembic_version" in before
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO tenant (id, name, template, region, deployment, status, created, case_types, settings) "
                          "VALUES ('t1', 'T', 'x', 'r', 'shared', 'live', '2026-01-01', '[]', '{}')"))

    rebuild_schema(eng)

    assert set(inspect(eng).get_table_names()) == before  # every table is back, not only the version marker
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM tenant")).scalar() == 0
