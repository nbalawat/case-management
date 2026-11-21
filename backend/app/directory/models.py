"""The directory's tables: people and groups as the client's identity provider sends them, and the SCIM tokens it uses."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..domain.models import now
from ..tenancy import TenantScoped


class DirectoryUser(TenantScoped, Base):
    """One person at the client, as provisioned by its identity provider. `user_name` is who their token says they are."""

    __tablename__ = "directory_user"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_name: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (Index("ux_directory_user_name", "tenant_id", "user_name", unique=True),)


class DirectoryGroup(TenantScoped, Base):
    __tablename__ = "directory_group"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (Index("ux_directory_group_name", "tenant_id", "display_name", unique=True),)


class DirectoryMember(TenantScoped, Base):
    __tablename__ = "directory_member"
    group_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    __table_args__ = (Index("ix_directory_member_user", "tenant_id", "user_id"),)


class ScimToken(TenantScoped, Base):
    """A bearer token the client's identity provider presents to provision people. Only its HMAC is kept."""

    __tablename__ = "scim_token"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    digest: Mapped[str] = mapped_column(String(128))
    hint: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(120), default="")
    created_by: Mapped[str] = mapped_column(String(60))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
