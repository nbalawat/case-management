"""Versioned migrations (Alembic), run through the application rather than a separate config file.

    python -m app.migrate            bring the configured database to the newest schema
    python -m app.migrate current    say which revision it holds
    python -m app.migrate check      exit 1 if the models have changed without a migration
    python -m app.migrate revision "what changed"    write the next migration from the difference

`versions/0016_baseline.py` is the schema as it stood when migrations began (the old "schema version 16"). Every
change since is a file after it. Never edit a migration that has been released: write the next one.

Parallel work: new revisions are named by the time they were written (`20261121174700_what_changed.py`), not by
the next number, so two branches never claim the same id. Two branches that each add a migration still leave two
heads once both land; `python -m app.migrate check` (run in CI) fails on that, and on a repeated id, and says
which file to re-point. The fix is one line: set the newer file's `down_revision` to the other head.
"""
from __future__ import annotations

import re
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

HERE = Path(__file__).parent
BASELINE = "0016"
LOCK = 0x_CA5E_F026  # one Postgres advisory lock, so pods starting together migrate one at a time


def config(conn: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    if conn is not None:
        cfg.attributes["connection"] = conn
    return cfg


def scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(config())


def head() -> str:
    return scripts().get_current_head()


_REVISION = re.compile(r"^revision\s*(?::\s*str\s*)?=\s*['\"]([^'\"]+)['\"]", re.M)


def problems() -> list[str]:
    """What stops the migrations forming one line: a revision id used twice, or more than one head. Empty when sound.

    Alembic itself only warns on a repeated id and then silently keeps one of the files, so look at the files."""
    seen: dict[str, list[str]] = {}
    for f in sorted((HERE / "versions").glob("*.py")):
        if m := _REVISION.search(f.read_text()):
            seen.setdefault(m.group(1), []).append(f.name)
    found = [f"revision id {rev!r} is used by {len(files)} files: {', '.join(files)}. Give all but one a new id "
             f"(`python -m app.migrate revision` names them by time) and chain them with down_revision."
             for rev, files in seen.items() if len(files) > 1]
    heads = sorted(scripts().get_heads()) if not found else []
    if len(heads) > 1:
        files = {h: scripts().get_revision(h).path.rsplit("/", 1)[-1] for h in heads}
        found.append(f"{len(heads)} migration heads: {', '.join(f'{h} ({files[h]})' for h in heads)}. Two branches each "
                     f"added a migration. In the one your branch added, set down_revision to the other head.")
    return found


def current(conn: Connection) -> str | None:
    """The revision a database holds, or None if migrations have never run on it."""
    return MigrationContext.configure(conn).get_current_revision()


def _lock(conn: Connection) -> None:
    if conn.dialect.name == "postgresql":
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK})


def upgrade(eng: Engine, to: str = "head") -> str:
    """Bring a database up to a revision, in one transaction where the database allows it. Returns where it ended."""
    with eng.begin() as conn:
        _lock(conn)
        command.upgrade(config(conn), to)
        return current(conn)


def stamp(eng: Engine, revision: str) -> None:
    """Record that a database already holds a revision's schema, without running anything."""
    with eng.begin() as conn:
        _lock(conn)
        command.stamp(config(conn), revision)


def drift(eng: Engine) -> list:
    """Differences between the models and what the database holds. Empty when every model change has a migration."""
    from ..db import Base
    from ..domain import models  # noqa: F401

    with eng.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "render_as_batch": True})
        return [d for d in compare_metadata(ctx, Base.metadata) if not _ours_to_ignore(d)]


def _ours_to_ignore(diff) -> bool:
    """Tables that are not the models' business: the old version marker, and anything another tool keeps here."""
    op = diff[0] if isinstance(diff, tuple) else None
    return op == "remove_table" and diff[1].name in {"schema_version"} | set(_foreign_tables())


def _foreign_tables() -> tuple[str, ...]:
    return ("iceberg_tables", "iceberg_namespace_properties")  # an Iceberg SQL catalog may share the database


def has_tables(conn: Connection) -> bool:
    return bool(set(inspect(conn).get_table_names()) - set(_foreign_tables()))
