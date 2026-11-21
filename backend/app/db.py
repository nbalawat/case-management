"""Database engine and session handling. SQLite by default, Postgres via DATABASE_URL."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or settings.database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)  # fresh checkout has no data/
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(conn, _):  # pragma: no cover - driver hook
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None) -> None:
    from .domain import models  # noqa: F401  (register tables)

    Base.metadata.create_all(eng or engine)


SCHEMA_VERSION = 2


def ensure_schema(eng=None) -> None:
    """Demo-grade migration: if the stored schema is older than the code, rebuild it. The seed then refills it.

    A real deployment uses versioned migrations; a demo database holds nothing worth migrating.
    """
    from sqlalchemy import inspect, text

    eng = eng or engine
    with eng.begin() as conn:
        tables = inspect(conn).get_table_names()
        current = conn.execute(text("SELECT version FROM schema_version")).scalar() if "schema_version" in tables else None
        if tables and current != SCHEMA_VERSION:
            from .domain import models  # noqa: F401

            Base.metadata.drop_all(conn)
            conn.execute(text("DROP TABLE IF EXISTS schema_version"))
            tables = []
        if "schema_version" not in tables:
            conn.execute(text("CREATE TABLE schema_version (version INTEGER NOT NULL)"))
            conn.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": SCHEMA_VERSION})
    init_db(eng)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed on success."""
    with session_scope() as s:
        yield s


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
