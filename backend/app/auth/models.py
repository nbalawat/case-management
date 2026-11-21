"""Browser sign-in sessions this server holds on a user's behalf (the backend-for-frontend pattern)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..domain.models import now


class AuthSession(Base):
    """One signed-in browser. The browser holds only a random cookie; this row holds its tokens, sealed.

    A platform table: a session exists before anyone knows which client it is for, and platform staff have one too.
    `id` is the SHA-256 of the cookie, so the database never holds a value that would let anyone sign in.
    """

    __tablename__ = "auth_session"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    realm: Mapped[str] = mapped_column(String(40))  # the client's id, or "platform" for the provider's staff
    subject: Mapped[str] = mapped_column(String(200))
    access_sealed: Mapped[str] = mapped_column(Text)
    refresh_sealed: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_token_sealed: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_expires: Mapped[datetime] = mapped_column(DateTime)
    token_endpoint: Mapped[str] = mapped_column(String(500))
    end_session_endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    revocation_endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_id: Mapped[str] = mapped_column(String(200))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now)
    refreshed: Mapped[int] = mapped_column(default=0)
    ended: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_why: Mapped[str] = mapped_column(String(60), default="")
    __table_args__ = (Index("ix_auth_session_subject", "realm", "subject"),)
