"""Tenant isolation in the data layer.

A session is opened for one tenant. From then on:
  * every ORM query against a tenant-scoped table is filtered to that tenant, without the caller asking;
  * every new row is stamped with the tenant, and a row carrying another tenant's id is refused;
  * bulk UPDATE and DELETE statements are filtered the same way;
  * statements that cannot be filtered or stamped (raw SQL, bulk INSERT) are refused outright.

Application code therefore cannot read or write across clients by forgetting a WHERE clause. A session with
no tenant is for platform operations only (start-up, the operator console) and must be asked for by name.

In the cloud build, Postgres row-level security adds a second, independent layer under this one.
"""
from __future__ import annotations

from sqlalchemy import String, event
from sqlalchemy.orm import Mapped, Session, mapped_column, with_loader_criteria

TENANT_KEY = "tenant_id"


class TenantError(RuntimeError):
    """Code tried to cross a tenant boundary."""


class TenantScoped:
    """Mixin for tables that belong to one client."""

    tenant_id: Mapped[str] = mapped_column(String(40), index=True)


class TenantKeyed:
    """Mixin for tables whose ids come from the client's systems: the tenant is part of the primary key."""

    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True)


SCOPED = (TenantScoped, TenantKeyed)


def bind_tenant(session: Session, tenant_id: str) -> Session:
    if not tenant_id:
        raise TenantError("A tenant id is required")
    session.info[TENANT_KEY] = tenant_id
    return session


def tenant_of(session: Session) -> str:
    tenant = session.info.get(TENANT_KEY)
    if not tenant:
        raise TenantError("This operation needs a tenant session")
    return tenant


def is_platform_session(session: Session) -> bool:
    return session.info.get("platform") is True


@event.listens_for(Session, "do_orm_execute")
def _filter_by_tenant(state) -> None:
    session = state.session
    tenant = session.info.get(TENANT_KEY)
    if tenant is None:
        if is_platform_session(session):
            return
        raise TenantError("Query on a session with no tenant. Use a tenant session, or ask for a platform session explicitly.")
    if not state.is_orm_statement:
        raise TenantError("Raw SQL cannot be filtered by tenant, so a tenant session refuses it. Use the ORM.")
    if state.is_insert:
        raise TenantError("Bulk inserts skip tenant stamping, so a tenant session refuses them. Add objects to the session.")
    if state.is_select or state.is_update or state.is_delete:
        for base in SCOPED:
            state.statement = state.statement.options(
                with_loader_criteria(base, lambda cls: cls.tenant_id == tenant, include_aliases=True, track_closure_variables=True))


@event.listens_for(Session, "before_flush")
def _stamp_tenant(session: Session, _context, _instances) -> None:
    tenant = session.info.get(TENANT_KEY)
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, SCOPED):
            continue
        if obj.tenant_id is None:
            if tenant is None:
                raise TenantError(f"{type(obj).__name__} written without a tenant")
            obj.tenant_id = tenant
        elif tenant is not None and obj.tenant_id != tenant:
            raise TenantError(f"{type(obj).__name__} belongs to tenant '{obj.tenant_id}', not '{tenant}'")
