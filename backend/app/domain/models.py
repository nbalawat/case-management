"""Canonical data model shared by every workload (KYC, monitoring, fraud, sanctions)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class Party(Base):
    __tablename__ = "party"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="person")
    name: Mapped[str] = mapped_column(String(200), index=True)
    country: Mapped[str] = mapped_column(String(2), default="US")
    occupation: Mapped[str] = mapped_column(String(80), default="")
    risk_rating: Mapped[str] = mapped_column(String(10), default="low")
    pep: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarded: Mapped[datetime] = mapped_column(DateTime, default=now)
    accounts: Mapped[list["Account"]] = relationship(back_populates="party")


class Account(Base):
    __tablename__ = "account"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    party_id: Mapped[str] = mapped_column(ForeignKey("party.id"), index=True)
    product: Mapped[str] = mapped_column(String(40), default="checking")
    status: Mapped[str] = mapped_column(String(20), default="open")
    party: Mapped[Party] = relationship(back_populates="accounts")


class Transaction(Base):
    __tablename__ = "txn"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"))
    party_id: Mapped[str] = mapped_column(String(40))
    ts: Mapped[datetime] = mapped_column(DateTime)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    channel: Mapped[str] = mapped_column(String(20))  # cash | wire | ach | card | p2p
    counterparty: Mapped[str] = mapped_column(String(200), default="")
    counterparty_country: Mapped[str] = mapped_column(String(2), default="US")
    feed_id: Mapped[str] = mapped_column(String(40), default="seed")
    # Relationships also tell the unit of work to insert parents before children.
    account: Mapped[Account] = relationship()
    __table_args__ = (Index("ix_txn_party_ts", "party_id", "ts"),)


class Alert(Base):
    __tablename__ = "alert"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True)
    source: Mapped[str] = mapped_column(String(40), default="caseforge-detection")
    scenario: Mapped[str] = mapped_column(String(60), index=True)
    scenario_version: Mapped[str] = mapped_column(String(20))
    party_id: Mapped[str] = mapped_column(ForeignKey("party.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)
    txn_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="new")
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    party: Mapped[Party] = relationship()


class Case(Base):
    __tablename__ = "case"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_type: Mapped[str] = mapped_column(String(60), index=True)
    type_version: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(40), index=True)
    party_id: Mapped[str] = mapped_column(ForeignKey("party.id"), index=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    owner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    closed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sla_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(40), nullable=True)
    narrative: Mapped[str] = mapped_column(Text, default="")
    drafted_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent_recommendation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    party: Mapped[Party] = relationship()


class CaseEvent(Base):
    """Append-only audit trail. Rows are never updated or deleted by the application."""

    __tablename__ = "case_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    actor: Mapped[str] = mapped_column(String(60))
    actor_kind: Mapped[str] = mapped_column(String(10))  # human | agent | system
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentRun(Base):
    __tablename__ = "agent_run"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    started: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider: Mapped[str] = mapped_column(String(40), default="mock")
    recommendation: Mapped[str] = mapped_column(String(40), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    steps: Mapped[list] = mapped_column(JSON, default=list)


class Feed(Base):
    __tablename__ = "feed"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_system: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="mapped")
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class Filing(Base):
    __tablename__ = "filing"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    filed_by: Mapped[str] = mapped_column(String(40))
    filed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
