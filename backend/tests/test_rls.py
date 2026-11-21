"""Postgres row-level security: the database's own tenant wall, tested by going around the application's.

Each test reaches the database the way a bug would: a raw statement on a tenant session's connection, which the ORM
guard in app/tenancy.py never sees. The policies must still keep every client to its own rows.

Runs when TEST_POSTGRES_URL names a disposable Postgres database (see test_postgres.py). This file uses a database of
its own next to it, named <that database>_rls, created and dropped here, so the two can run at the same time.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app import migrations, rls
from app.config import settings
from app.db import Base, make_engine, secure
from app.domain import models as m
from app.tenancy import bind_tenant

URL = os.getenv("TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("postgresql"), reason="set TEST_POSTGRES_URL to a disposable Postgres database to run")
ROLE = "caseforge_rls_test_app"


@pytest.fixture(scope="module")
def eng():
    base = make_url(URL)
    name = f"{base.database}_rls"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    saved = settings.database_role
    object.__setattr__(settings, "database_role", ROLE)
    e = make_engine(base.set(database=name).render_as_string(hide_password=False))
    migrations.upgrade(e)  # builds every table, then applies the policies and the request role
    factory = sessionmaker(bind=e, expire_on_commit=False)
    with factory() as s:
        s.info["platform"] = True
        s.add_all([m.Party(id="P1", tenant_id="alpha", name="Alpha's customer"), m.Party(id="P1", tenant_id="beta", name="Beta's customer"),
                   m.Party(id="P2", tenant_id="beta", name="Another of Beta's")])
        s.commit()
    e.factory = factory
    yield e
    object.__setattr__(settings, "database_role", saved)
    e.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'DROP ROLE IF EXISTS "{ROLE}"'))
    admin.dispose()


def _raw(s, sql, **kw):
    """A statement on the session's own connection: the ORM guard does not see it, only the database does."""
    return s.connection().execute(text(sql), kw)


def test_every_client_table_has_a_forced_policy_and_requests_run_as_a_role_it_binds(eng):
    with eng.connect() as c:
        st = rls.status(c)
    assert st.enabled and not st.missing and not st.bypass and st.role == ROLE
    assert st.tables == len(rls.tenant_tables()) and "enforced on" in st.describe()


def test_a_tenant_session_cannot_read_another_clients_rows_even_with_raw_sql(eng):
    with eng.factory() as s:
        bind_tenant(s, "alpha")
        assert [r[0] for r in _raw(s, "SELECT tenant_id FROM party")] == ["alpha"]
        assert _raw(s, "SELECT count(*) FROM party WHERE tenant_id = 'beta'").scalar() == 0
        assert _raw(s, "SELECT current_user").scalar() == ROLE
    with eng.factory() as s:
        bind_tenant(s, "beta")
        assert sorted(r[0] for r in _raw(s, "SELECT id FROM party")) == ["P1", "P2"]


def test_a_tenant_session_cannot_write_or_change_another_clients_rows(eng):
    with eng.factory() as s:
        bind_tenant(s, "alpha")
        with pytest.raises(DBAPIError, match="row-level security"):
            _raw(s, "INSERT INTO party (id, tenant_id, kind, name, country, occupation, risk_rating, pep, onboarded)"
                    " VALUES ('X', 'beta', 'person', 'Planted', 'US', '', 'low', false, now())")
        s.rollback()
        bind_tenant(s, "alpha")
        assert _raw(s, "UPDATE party SET name = 'Changed' WHERE tenant_id = 'beta'").rowcount == 0
        assert _raw(s, "DELETE FROM party WHERE tenant_id = 'beta'").rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            _raw(s, "UPDATE party SET tenant_id = 'beta' WHERE id = 'P1'")  # moving a row out of the client is a write to another
        s.rollback()
    with eng.factory() as s:
        s.info["platform"] = True
        assert s.scalar(select(m.Party.name).where(m.Party.tenant_id == "beta", m.Party.id == "P1")) == "Beta's customer"


def test_platform_work_sees_every_client(eng):
    with eng.factory() as s:
        s.info["platform"] = True
        assert sorted(r[0] for r in _raw(s, "SELECT tenant_id FROM party")) == ["alpha", "beta", "beta"]


def test_a_transaction_that_says_nothing_sees_nothing_and_nothing_carries_over_in_the_pool(eng):
    with eng.factory() as s:
        bind_tenant(s, "beta")
        assert _raw(s, "SELECT count(*) FROM party").scalar() == 2
        s.commit()
    with eng.connect() as c:  # the same pooled connection, a new transaction with no session in it
        c.execute(text(f'SET ROLE "{ROLE}"'))
        assert c.execute(text(f"SELECT current_setting('{rls.TENANT_SETTING}', true)")).scalar() in (None, "")
        assert c.execute(text("SELECT count(*) FROM party")).scalar() == 0
        c.execute(text("RESET ROLE"))
        c.rollback()


def test_the_orm_and_the_database_agree(eng):
    with eng.factory() as s:
        bind_tenant(s, "beta")
        assert sorted(s.scalars(select(m.Party.id))) == ["P1", "P2"]  # both walls let the client's own rows through


def test_starting_again_changes_nothing(eng):
    assert secure(eng) == []


def test_a_table_that_lost_its_policy_is_reported_and_restored_at_the_next_start(eng):
    with eng.begin() as c:
        c.execute(text(f"DROP POLICY {rls.POLICY} ON party"))
        c.execute(text("ALTER TABLE party NO FORCE ROW LEVEL SECURITY"))
    with eng.connect() as c:
        st = rls.status(c)
    assert not st.enabled and st.missing == ["party"] and "not covered: party" in st.describe()
    assert secure(eng) == ["party"]
    with eng.connect() as c:
        assert rls.status(c).enabled


def test_without_a_request_role_a_superuser_connection_is_reported_as_let_through(eng):
    object.__setattr__(settings, "database_role", "")
    try:
        with eng.connect() as c:
            st = rls.status(c)
            superuser = c.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
        if superuser:
            assert st.bypass and not st.enabled and "Set DATABASE_ROLE" in st.describe()
    finally:
        object.__setattr__(settings, "database_role", ROLE)


def test_a_request_role_that_could_bypass_the_policies_is_refused(eng):
    with eng.begin() as c:
        c.execute(text('CREATE ROLE "caseforge_rls_test_bypass" NOLOGIN BYPASSRLS'))
    object.__setattr__(settings, "database_role", "caseforge_rls_test_bypass")
    try:
        with pytest.raises(RuntimeError, match="bypass row-level security"):
            secure(eng)
    finally:
        object.__setattr__(settings, "database_role", ROLE)
        with eng.begin() as c:
            c.execute(text('DROP ROLE "caseforge_rls_test_bypass"'))


@pytest.mark.parametrize("name", ['app"; DROP TABLE party; --', "App", "1app", ""])
def test_a_role_name_is_a_plain_sql_name_or_nothing(name):
    saved = settings.database_role
    object.__setattr__(settings, "database_role", name)
    try:
        if name:
            with pytest.raises(ValueError):
                rls.role()
        else:
            assert rls.role() is None
    finally:
        object.__setattr__(settings, "database_role", saved)


def test_on_sqlite_the_application_guard_is_the_only_wall_and_says_so():
    e = make_engine("sqlite://")
    Base.metadata.create_all(e)
    assert secure(e) == []
    with e.connect() as c:
        assert rls.status(c).describe() == "not available on this database"
