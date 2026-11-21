"""Database engine and session handling. SQLite by default, Postgres via DATABASE_URL."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or settings.database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)  # fresh checkout has no data/
    if url in ("sqlite://", "sqlite:///:memory:"):
        kwargs["poolclass"] = StaticPool  # one shared in-memory database, for tests
    eng = create_engine(url, **kwargs)
    eng.caseforge = True  # the tenancy guard applies to sessions on this engine (see tenancy._ours)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(conn, _):  # pragma: no cover - driver hook
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys unless told; Postgres never does
            cur.close()
    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None) -> None:
    from .domain import models  # noqa: F401  (register tables)

    Base.metadata.create_all(eng or engine)


SCHEMA_VERSION = 16  # the last schema from before versioned migrations. It never changes again: write a migration.


class SchemaMismatch(RuntimeError):
    """The database was made by a different version of the application, and rebuilding it was not allowed."""


def ensure_schema(eng=None) -> None:
    """Make the database hold the schema this code expects, by versioned migrations (app/migrations).

    - An empty database is built by running every migration.
    - A database that migrations already manage is brought to the newest revision when MIGRATE_ON_START=1 (the
      default). With MIGRATE_ON_START=0 the application stops instead if it is behind, and something else, such as
      the Helm chart's migration job, runs `python -m app.migrate`.
    - A database from before migrations existed is adopted if it holds the last pre-migration schema, version 16.
      Anything older cannot be migrated: with SCHEMA_ON_MISMATCH=rebuild (demos) every table is dropped and built
      again, otherwise the application stops and changes nothing.
    """
    from sqlalchemy import inspect, text

    from . import migrations

    eng = eng or engine
    with eng.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        managed = "alembic_version" in tables
        at = migrations.current(conn) if managed else None
        legacy = conn.execute(text("SELECT version FROM schema_version")).scalar() if "schema_version" in tables and not managed else None
        empty = not migrations.has_tables(conn)
    head = migrations.head()
    if managed and at == head:
        return
    if managed:
        if not settings.migrate_on_start:
            raise SchemaMismatch(f"This database is at migration {at} and the application expects {head}. Nothing was changed. Run `python -m app.migrate`.")
    elif empty:
        pass
    elif legacy == SCHEMA_VERSION:
        migrations.stamp(eng, migrations.BASELINE)  # the tables are exactly the baseline's; record that and carry on
    else:
        if settings.schema_on_mismatch != "rebuild":
            raise SchemaMismatch(
                f"This database holds schema version {legacy}, from before versioned migrations, and only version {SCHEMA_VERSION} can be adopted. "
                "Nothing was changed. Move the data, or set SCHEMA_ON_MISMATCH=rebuild to drop every table (demos only).")
        from .domain import models  # noqa: F401

        with eng.begin() as conn:
            Base.metadata.drop_all(conn)
            conn.execute(text("DROP TABLE IF EXISTS schema_version"))
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    migrations.upgrade(eng)


@contextmanager
def session_scope(tenant_id: str | None = None, *, platform: bool = False) -> Iterator[Session]:
    """One unit of work. Pass a tenant for client data; `platform=True` for cross-client platform operations."""
    from .tenancy import TenantError, bind_tenant

    if (tenant_id is None) == (not platform):
        raise TenantError("Open a session for exactly one tenant, or as a platform session")
    s = SessionLocal()
    if tenant_id:
        bind_tenant(s, tenant_id)
    else:
        s.info["platform"] = True
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
