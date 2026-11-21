"""Canonical data model shared by every workload (KYC, monitoring, fraud, sanctions).

Every business table carries `tenant_id` through TenantScoped. Customer, account and transaction ids come
from the client's own systems, so they are only unique within a client: their keys include the tenant.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..tenancy import TenantKeyed, TenantScoped


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class Tenant(Base):
    """A client of the platform. The only business table that is not tenant-scoped."""

    __tablename__ = "tenant"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # short slug, used in URLs and headers
    name: Mapped[str] = mapped_column(String(120))
    template: Mapped[str] = mapped_column(String(40))
    region: Mapped[str] = mapped_column(String(40), default="us-east")
    deployment: Mapped[str] = mapped_column(String(20), default="pooled")  # pooled | dedicated | client-cloud
    status: Mapped[str] = mapped_column(String(20), default="live")  # onboarding | live | suspended
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    case_types: Mapped[list] = mapped_column(JSON, default=list)  # which library case types this client uses
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class Party(TenantKeyed, Base):
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


def party_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", "party_id"], ["party.tenant_id", "party.id"])


class Account(TenantKeyed, Base):
    __tablename__ = "account"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    __table_args__ = (party_fk(),)
    product: Mapped[str] = mapped_column(String(40), default="checking")
    status: Mapped[str] = mapped_column(String(20), default="open")
    party: Mapped[Party] = relationship(back_populates="accounts")


class Transaction(TenantKeyed, Base):
    __tablename__ = "txn"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(40))
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
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "account_id"], ["account.tenant_id", "account.id"]),
                      Index("ix_txn_party_ts", "tenant_id", "party_id", "ts"))


class Alert(TenantScoped, Base):
    __tablename__ = "alert"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(40), default="caseforge-detection")
    scenario: Mapped[str] = mapped_column(String(60), index=True)
    scenario_version: Mapped[str] = mapped_column(String(20))
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    score: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)
    txn_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="new")
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    # The triage agent's reading of the alert: score, reasons, linked alerts and what it did or proposed.
    triage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Set when an agent closed the alert on its own (status auto_closed). qa_sample marks the ones a person must check.
    closed_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    qa_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    qa_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    qa_result: Mapped[str | None] = mapped_column(String(20), nullable=True)  # confirmed | reopened
    party: Mapped[Party] = relationship()
    __table_args__ = (party_fk(), UniqueConstraint("tenant_id", "dedupe_key"))


class Case(TenantScoped, Base):
    __tablename__ = "case"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_type: Mapped[str] = mapped_column(String(60), index=True)
    type_version: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(40), index=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    owner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    closed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sla_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(40), nullable=True)
    narrative: Mapped[str] = mapped_column(Text, default="")
    drafted_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent_recommendation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    queue: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    state_entered: Mapped[datetime] = mapped_column(DateTime, default=now)
    parent_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # A restricted case is visible only to people holding the view_restricted permission (insiders, sensitive subjects,
    # law-enforcement requests). Enforced in the data layer, like the client boundary.
    restricted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    restricted_reason: Mapped[str] = mapped_column(Text, default="")
    # Workflow working data: completed tasks, escalation markers. Replace the dict to persist a change.
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    party: Mapped[Party] = relationship()
    __table_args__ = (party_fk(),)


class CaseEvent(TenantScoped, Base):
    """Append-only audit trail. Rows are never updated or deleted by the application."""

    __tablename__ = "case_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    actor: Mapped[str] = mapped_column(String(60))
    actor_kind: Mapped[str] = mapped_column(String(10))  # human | agent | system
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentRun(TenantScoped, Base):
    __tablename__ = "agent_run"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    started: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider: Mapped[str] = mapped_column(String(40), default="mock")
    recommendation: Mapped[str] = mapped_column(String(40), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    policy_version: Mapped[int] = mapped_column(Integer, default=0)  # the client's agent policy in force for this run
    requested_by: Mapped[str | None] = mapped_column(String(40), nullable=True)  # a person, when run on request
    # What the challenger profile would have recommended. Recorded for comparison, never shown on the case.
    shadow: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Feed(TenantScoped, Base):
    __tablename__ = "feed"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_system: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="mapped")
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class Filing(TenantScoped, Base):
    __tablename__ = "filing"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    filed_by: Mapped[str] = mapped_column(String(40))
    filed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class InfoRequest(TenantScoped, Base):
    """A request for information sent outside the investigating team (customer, branch, relationship manager)."""

    __tablename__ = "info_request"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    recipient: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | answered
    raised_by: Mapped[str] = mapped_column(String(40))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    due: Mapped[datetime] = mapped_column(DateTime)
    response: Mapped[str] = mapped_column(Text, default="")
    answered: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String(40), nullable=True)


class WorkflowVersion(TenantScoped, Base):
    """A case type definition published from the workflow editor. Files on disk are the shipped baseline."""

    __tablename__ = "workflow_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_type: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(Text)
    published_by: Mapped[str] = mapped_column(String(40))
    published: Mapped[datetime] = mapped_column(DateTime, default=now)
    note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (Index("ux_workflow_version", "tenant_id", "case_type", "version", unique=True),)


class AccessPolicyVersion(TenantScoped, Base):
    """A client's access policy as published. The newest row is the one in force."""

    __tablename__ = "access_policy_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(Text)
    published_by: Mapped[str] = mapped_column(String(40))
    published: Mapped[datetime] = mapped_column(DateTime, default=now)
    note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (Index("ux_access_policy_version", "tenant_id", "version", unique=True),)


class AskLog(TenantScoped, Base):
    """One question put to "ask anything": who asked, what was looked up, what was answered. Never updated."""

    __tablename__ = "ask_log"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    asked_by: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(40))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="answered")  # answered | refused | incomplete | failed
    provider: Mapped[str] = mapped_column(String(80), default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)  # each lookup: tool, inputs, outcome
    cited: Mapped[list] = mapped_column(JSON, default=list)
    unverified: Mapped[list] = mapped_column(JSON, default=list)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class DataSource(TenantScoped, Base):
    """A place in object storage that a client's data arrives in, and how to read it."""

    __tablename__ = "data_source"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    dataset: Mapped[str] = mapped_column(String(30))  # customers | accounts | transactions
    root: Mapped[str] = mapped_column(String(40))  # one of the storage locations registered for the client
    prefix: Mapped[str] = mapped_column(String(300), default="")
    pattern: Mapped[str] = mapped_column(String(200), default="**")
    format: Mapped[str] = mapped_column(String(12), default="auto")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    mapping_version: Mapped[int] = mapped_column(Integer, default=1)
    columns: Mapped[list] = mapped_column(JSON, default=list)  # the columns seen when the mapping was confirmed
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[str] = mapped_column(String(40))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # last observed: newest object, last status
    __table_args__ = (Index("ux_data_source_name", "tenant_id", "name", unique=True),)


class PipelineRun(TenantScoped, Base):
    """One run of one source: what was found, what was loaded, what was held back, and why."""

    __tablename__ = "pipeline_run"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(40), index=True)
    batch_id: Mapped[str] = mapped_column(String(40), index=True)  # runs started together share a batch
    trigger: Mapped[str] = mapped_column(String(12), default="manual")  # manual | schedule
    started_by: Mapped[str] = mapped_column(String(40))
    started: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    mapping_version: Mapped[int] = mapped_column(Integer, default=1)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class IngestedObject(TenantScoped, Base):
    """One file a pipeline has dealt with. The fingerprint is what makes a re-run skip it and a changed file count as new."""

    __tablename__ = "ingested_object"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(500))
    fingerprint: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))  # loaded | quarantined
    reason: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (Index("ux_ingested_object", "tenant_id", "source_id", "key", "fingerprint", unique=True),)


class AgentPolicyVersion(TenantScoped, Base):
    """A client's agent policy as published: which agents run, at what autonomy, inside which limits. The newest row is in force."""

    __tablename__ = "agent_policy_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    published_by: Mapped[str] = mapped_column(String(40))
    published: Mapped[datetime] = mapped_column(DateTime, default=now)
    note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (Index("ux_agent_policy_version", "tenant_id", "version", unique=True),)


class AgentStep(TenantScoped, Base):
    """The agent ledger: one row for everything an agent did. Append-only, and each row carries the hash of the one before."""

    __tablename__ = "agent_step"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    alert_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(40), index=True)
    agent_version: Mapped[str] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(4))
    policy_version: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(80), default="")
    prompt_sha: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(20), default="done")  # done | skipped | refused | acted
    summary: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list] = mapped_column(JSON, default=list)
    findings: Mapped[list] = mapped_column(JSON, default=list)
    rejected: Mapped[list] = mapped_column(JSON, default=list)  # claims dropped because they cited nothing
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


class AgentJob(TenantScoped, Base):
    """A piece of agent work over many items: an evaluation, a lookback, a tuning review."""

    __tablename__ = "agent_job"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # evaluation | lookback | tuning
    status: Mapped[str] = mapped_column(String(20), default="done")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    started_by: Mapped[str] = mapped_column(String(40))
    started: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MediaItem(TenantScoped, Base):
    """An article or registry entry about a customer, as fetched. Outside content: agents read it only through the extractor."""

    __tablename__ = "media_item"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(String(80))
    published: Mapped[datetime] = mapped_column(DateTime, default=now)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    __table_args__ = (party_fk(),)
