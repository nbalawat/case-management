"""Tables of the public API. All belong to one client, like every business table."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..domain.models import now
from ..tenancy import TenantScoped


class ApiClient(TenantScoped, Base):
    """An application allowed to call the API for one client.

    It acts under roles from the client's own access policy, so it sees exactly what a person holding those roles
    would see, and scopes narrow that further. It never holds a person's identity: decisions that the workflow
    reserves for people need a person's token.
    """

    __tablename__ = "api_client"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # the public client_id, cfc_...
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    roles: Mapped[list] = mapped_column(JSON, default=list)
    queues: Mapped[list] = mapped_column(JSON, default=list)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    allowed_ips: Mapped[list] = mapped_column(JSON, default=list)  # CIDR blocks; empty means any address
    rate_limit: Mapped[int] = mapped_column(Integer, default=0)  # requests per minute; 0 means the platform default
    token_ttl: Mapped[int] = mapped_column(Integer, default=0)  # seconds; 0 means the platform default
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | suspended | revoked
    created_by: Mapped[str] = mapped_column(String(60))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)  # who changed what and when


class ApiClientSecret(TenantScoped, Base):
    """One secret of an API client. Two may be live during a rotation; only a keyed hash is kept."""

    __tablename__ = "api_client_secret"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(40), index=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    hint: Mapped[str] = mapped_column(String(8))  # the last characters, so people can tell secrets apart
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApiToken(TenantScoped, Base):
    """A short-lived access token issued to an API client. Only a keyed hash is kept, so the table cannot be replayed."""

    __tablename__ = "api_token"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    client_id: Mapped[str] = mapped_column(String(40), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    issued: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str] = mapped_column(String(45), default="")


class ApiIdempotency(TenantScoped, Base):
    """The stored answer to a write made with an Idempotency-Key, so a retry gets the same answer and changes nothing."""

    __tablename__ = "api_idempotency"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caller: Mapped[str] = mapped_column(String(60))
    key: Mapped[str] = mapped_column(String(120))
    fingerprint: Mapped[str] = mapped_column(String(64))  # method, path and body of the first request
    status: Mapped[int] = mapped_column(Integer, default=0)  # 0 while the first request is still running
    body: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(80), default="application/json")
    created: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    __table_args__ = (UniqueConstraint("tenant_id", "caller", "key"),)


class ApiCall(TenantScoped, Base):
    """One request to the public API: who, what, the answer and how long it took. Kept API_CALL_KEEP_DAYS."""

    __tablename__ = "api_call"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(40))
    caller: Mapped[str] = mapped_column(String(60), index=True)  # app:<client id> or a person's id
    caller_kind: Mapped[str] = mapped_column(String(8))  # app | person
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    method: Mapped[str] = mapped_column(String(8))
    route: Mapped[str] = mapped_column(String(160))  # the route template, never ids or query strings
    status: Mapped[int] = mapped_column(Integer)
    ms: Mapped[int] = mapped_column(Integer, default=0)
    ip: Mapped[str] = mapped_column(String(45), default="")
    __table_args__ = (Index("ix_api_call_tenant_ts", "tenant_id", "ts"),)


class WebhookEndpoint(TenantScoped, Base):
    """Where to send events, which ones, and as whom: an endpoint sees only what its API client may see."""

    __tablename__ = "webhook_endpoint"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(40), index=True)
    url: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    event_types: Mapped[list] = mapped_column(JSON, default=list)  # exact types or prefixes ending in *; empty means all
    secret: Mapped[str] = mapped_column(Text)  # the signing secret, encrypted
    previous_secret: Mapped[str] = mapped_column(Text, default="")  # still signed with during a rotation's grace period
    previous_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | paused | disabled (too many failures)
    cursor: Mapped[int] = mapped_column(Integer, default=0)  # the last outbox row considered
    failures: Mapped[int] = mapped_column(Integer, default=0)  # deliveries in a row that gave up
    created_by: Mapped[str] = mapped_column(String(60))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    disabled_reason: Mapped[str] = mapped_column(String(300), default="")


class WebhookDelivery(TenantScoped, Base):
    __tablename__ = "webhook_delivery"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # also the webhook-id header: receivers drop repeats by it
    endpoint_id: Mapped[str] = mapped_column(String(40), index=True)
    event_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | delivered | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    delivered: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "endpoint_id", "event_id"),)
