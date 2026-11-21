"""Postgres row-level security: the database's own tenant wall, under the one in app/tenancy.py.

app/tenancy.py filters every ORM statement by tenant, so application code cannot cross clients by forgetting a WHERE
clause. That guard lives in this process. Row-level security puts a second wall in the database, which holds even for
a statement that never went through the guard: a raw query on a session's connection, a bug in a loader option, a
missed listener. Two parts:

  * Policies. Every tenant-scoped table gets ROW LEVEL SECURITY, FORCEd so the tables' owner is held too, and one
    policy: a row is visible, and may be written, only when its tenant_id equals the transaction's `caseforge.tenant`
    setting, or the transaction is platform work (`caseforge.platform` = on). With neither set, nothing is visible.
    `apply` creates whatever is missing and is run at every start and after every migration, so a table added by a
    later migration is covered without anyone remembering to.
  * The transaction's identity. When a session begins a transaction, the session's tenant (or platform flag) is
    written into those settings with set_config(..., is_local => true), so it lasts exactly one transaction and a
    pooled connection carries nothing over. With DATABASE_ROLE set, the transaction also runs SET LOCAL ROLE to that
    role, which holds no superuser or BYPASSRLS right: Postgres never applies row-level security to those.

Migrations, health probes and other raw connections are not sessions, so they run as the connecting user and are not
filtered. They hold no client data paths.

SQLite has no row-level security; there the application guard is the only wall, and every function here is a no-op.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from .config import settings
from .tenancy import SCOPED, TENANT_KEY, _ours, is_platform_session

log = logging.getLogger(__name__)

POLICY = "caseforge_tenant"
TENANT_SETTING, PLATFORM_SETTING = "caseforge.tenant", "caseforge.platform"
_RULE = (f"(tenant_id = current_setting('{TENANT_SETTING}', true)"
         f" OR coalesce(current_setting('{PLATFORM_SETTING}', true), '') = 'on')")
_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"'{name}' is not a plain lower-case SQL name")
    return f'"{name}"'


def role() -> str | None:
    r = settings.database_role.strip()
    if r:
        _ident(r)
    return r or None


def tenant_tables() -> list[str]:
    """Every table whose rows belong to one client, from the models: a new tenant-scoped model is covered without a change here."""
    from .db import Base
    from .domain import models  # noqa: F401  (register tables)

    return sorted({mp.local_table.name for mp in Base.registry.mappers if issubclass(mp.class_, SCOPED)})


@dataclass
class Status:
    enabled: bool  # Postgres, and every tenant table that exists is covered
    tables: int = 0
    missing: list[str] = field(default_factory=list)  # tenant tables without the policy, or without FORCE
    role: str | None = None  # what requests run as
    bypass: bool = False  # that role is a superuser or holds BYPASSRLS, so the policies do not bind it

    def describe(self) -> str:
        if not self.enabled and not self.tables:
            return "not available on this database"
        if self.missing:
            return f"{len(self.missing)} of {self.tables} client tables are not covered: {', '.join(self.missing[:5])}"
        if self.bypass:
            return f"policies on {self.tables} client tables, but requests run as {self.role}, which Postgres lets past them. Set DATABASE_ROLE."
        return f"enforced on {self.tables} client tables; requests run as {self.role}"


def _existing(conn: Connection, tables: list[str]) -> dict[str, tuple[bool, bool, bool]]:
    """table -> (row security on, forced, our policy present), for the tenant tables that exist."""
    rows = conn.execute(text(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
        " EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = n.nspname AND p.tablename = c.relname AND p.policyname = :policy)"
        " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') AND c.relname = ANY(:tables)"),
        {"policy": POLICY, "tables": tables}).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def apply(conn: Connection) -> list[str]:
    """Create what is missing: row security, FORCE, the policy, the request role and its grants. Returns the tables
    changed. Safe to run at every start; takes no lock on a table that is already as it should be."""
    if conn.dialect.name != "postgresql":
        return []
    changed = []
    for table, (on, forced, policy) in _existing(conn, tenant_tables()).items():
        t = _ident(table)
        if not on:
            conn.execute(text(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY"))
        if not forced:
            conn.execute(text(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY"))
        if not policy:
            conn.execute(text(f"CREATE POLICY {POLICY} ON {t} USING {_RULE} WITH CHECK {_RULE}"))
        if not (on and forced and policy):
            changed.append(table)
    r = role()
    if r:
        _grant(conn, r)
    if changed:
        log.info("row-level security applied to %d table(s): %s", len(changed), ", ".join(changed))
    return changed


def _grant(conn: Connection, name: str) -> None:
    """The request role: may read and write rows (as the policies allow), and nothing else. It cannot log in, so it is
    reachable only by SET ROLE from the application's own connection."""
    r = _ident(name)
    found = conn.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": name}).first()
    if found is None:
        conn.execute(text(f"CREATE ROLE {r} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"))
    elif found[0]:
        raise RuntimeError(f"DATABASE_ROLE {name} is a superuser or may bypass row-level security, so it would not be held by it")
    me = conn.execute(text("SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)")).first()
    if not me[1]:  # a superuser may SET ROLE to anyone; anyone else must be a member
        conn.execute(text(f'GRANT {r} TO "{me[0]}"'))
    conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {r}"))
    conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}"))
    conn.execute(text(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {r}"))
    conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}"))
    conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {r}"))


def status(conn: Connection) -> Status:
    if conn.dialect.name != "postgresql":
        return Status(enabled=False)
    tables = tenant_tables()
    found = _existing(conn, tables)
    missing = sorted(t for t, flags in found.items() if not all(flags))
    who = role() or conn.execute(text("SELECT current_user")).scalar()
    bypass = conn.execute(text("SELECT coalesce((SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = :r), false)"), {"r": who}).scalar()
    return Status(enabled=not missing and not bypass, tables=len(found), missing=missing, role=who, bypass=bool(bypass))


@event.listens_for(Session, "after_begin")
def _identify(session: Session, _transaction, connection: Connection) -> None:
    """Tell Postgres whose transaction this is, for the policies. Transaction-local, so nothing leaks through the pool."""
    if connection.dialect.name != "postgresql" or not _ours(session):
        return
    tenant = session.info.get(TENANT_KEY) or ""
    platform = "on" if tenant == "" and is_platform_session(session) else "off"
    connection.execute(text("SELECT set_config(:ts, :t, true), set_config(:ps, :p, true)"),
                       {"ts": TENANT_SETTING, "t": tenant, "ps": PLATFORM_SETTING, "p": platform})
    r = role()
    if r:
        connection.execute(text(f"SET LOCAL ROLE {_ident(r)}"))


__all__ = ["POLICY", "Status", "apply", "role", "status", "tenant_tables"]
