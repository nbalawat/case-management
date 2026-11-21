"""Canonical data model shared by every workload (KYC, monitoring, fraud, sanctions).

Every business table carries `tenant_id` through TenantScoped. Customer, account and transaction ids come
from the client's own systems, so they are only unique within a client: their keys include the tenant.
"""
from __future__ import annotations

import contextlib
import contextvars
import random
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..tenancy import TenantKeyed, TenantScoped


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_id_source: contextvars.ContextVar[random.Random | None] = contextvars.ContextVar("id_source", default=None)


def new_id(prefix: str) -> str:
    source = _id_source.get()
    return f"{prefix}-{source.getrandbits(40):010x}" if source is not None else f"{prefix}-{uuid.uuid4().hex[:10]}"


@contextlib.contextmanager
def repeatable_ids(seed: str):
    """Inside this block ids come from a seeded stream, so synthetic data is identical on every run. Never used for live work."""
    token = _id_source.set(random.Random(seed))
    try:
        yield
    finally:
        _id_source.reset(token)


@contextlib.contextmanager
def side_ids(seed: str):
    """A separate seeded stream for a side step of synthetic data, so adding the step does not shift every id drawn after it.

    Outside synthetic data it does nothing: live ids stay random."""
    if _id_source.get() is None:
        yield
        return
    with repeatable_ids(seed):
        yield


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
    wake_by: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # while hibernated: when it closes if nothing else arrives
    policy_note: Mapped[str] = mapped_column(String(300), default="")  # what a rule did with it, in words
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


class CodeList(TenantKeyed, Base):
    """A client's own version of a reference list, such as its reasons for closing a case. Absent means the library's list applies."""

    __tablename__ = "code_list"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    codes: Mapped[list] = mapped_column(JSON, default=list)
    updated_by: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Filing(TenantScoped, Base):
    """A report to a regulator: the document as rendered, where it is in its life, and what the regulator said back."""

    __tablename__ = "filing"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)  # none for reports no case led to, such as a CTR
    party_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    kind: Mapped[str] = mapped_column(String(20))  # SAR | STR | CTR
    format: Mapped[str] = mapped_column(String(30), default="record")  # which renderer wrote the document
    status: Mapped[str] = mapped_column(String(20), default="prepared", index=True)  # prepared | submitted | acknowledged | rejected | superseded
    filed_by: Mapped[str] = mapped_column(String(40))
    filed_at: Mapped[datetime] = mapped_column(DateTime, default=now)  # when it was prepared
    due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))  # of the document, so a later change cannot go unnoticed
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # the report in neutral form, before any format
    document: Mapped[str] = mapped_column(Text, default="")
    problems: Mapped[list] = mapped_column(JSON, default=list)  # what stops it being sent
    submitted_by: Mapped[str] = mapped_column(String(40), default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    channel: Mapped[str] = mapped_column(String(30), default="")
    receipt: Mapped[str] = mapped_column(String(200), default="")  # what the channel handed back, such as a file path or tracking id
    reference: Mapped[str] = mapped_column(String(60), default="")  # the regulator's own number once it accepts
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection: Mapped[str] = mapped_column(Text, default="")
    amends_id: Mapped[str] = mapped_column(String(40), default="")  # the earlier filing this one corrects
    history: Mapped[list] = mapped_column(JSON, default=list)  # who did what to it, for reports no case holds


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


class Entity(TenantKeyed, Base):
    """One real-world party as the platform understands it: a customer, or a counterparty seen in their payments."""

    __tablename__ = "entity"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # customer | counterparty
    name: Mapped[str] = mapped_column(String(200))  # the spelling seen most
    key: Mapped[str] = mapped_column(String(200), index=True)  # normalised name
    party_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)  # set for a customer
    countries: Mapped[list] = mapped_column(JSON, default=list)
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # [{name, count, how, score}]
    flags: Mapped[list] = mapped_column(JSON, default=list)  # watchlist | high_risk_country | common
    customers: Mapped[int] = mapped_column(Integer, default=0)  # how many customers transact with it
    txns: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[float] = mapped_column(Float, default=0.0)


class EntityEdge(TenantScoped, Base):
    """Money between a customer and a counterparty, summed. The network view is drawn from these."""

    __tablename__ = "entity_edge"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(String(60), index=True)
    txns: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[float] = mapped_column(Float, default=0.0)
    received: Mapped[float] = mapped_column(Float, default=0.0)
    first: Mapped[datetime] = mapped_column(DateTime)
    last: Mapped[datetime] = mapped_column(DateTime)
    channels: Mapped[list] = mapped_column(JSON, default=list)


class MatchCandidate(TenantKeyed, Base):
    """Two records that may be the same party. A person decides; the decision survives every later run."""

    __tablename__ = "match_candidate"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)  # counterparty_variant | duplicate_customer | customer_as_counterparty
    left_key: Mapped[str] = mapped_column(String(220))  # stable keys, not entity ids, so decisions outlive a rebuild
    right_key: Mapped[str] = mapped_column(String(220))
    left_name: Mapped[str] = mapped_column(String(200))
    right_name: Mapped[str] = mapped_column(String(200))
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open | same | different
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("tenant_id", "left_key", "right_key"),)


class OwnershipStructure(TenantScoped, Base):
    """Who owns and controls a customer, as recorded at one moment. A change is a new version; nothing is overwritten."""

    __tablename__ = "ownership_structure"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    nodes: Mapped[list] = mapped_column(JSON, default=list)  # [{key, name, kind, country, pep, verified}]
    links: Mapped[list] = mapped_column(JSON, default=list)  # [{owner, owned, percent, control}], owned "customer" is the customer itself
    source: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str] = mapped_column(String(40))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RiskAssessment(TenantScoped, Base):
    """One scoring of a customer under the client's risk model, with any override and who approved it."""

    __tablename__ = "risk_assessment"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    by: Mapped[str] = mapped_column(String(40))
    score: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(10))  # what the model says
    final_band: Mapped[str] = mapped_column(String(10))  # what stands, after any override
    factors: Mapped[list] = mapped_column(JSON, default=list)  # [{code, label, points, applies, evidence}]
    model: Mapped[str] = mapped_column(String(16), default="")  # fingerprint of the factor and band lists used
    override: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {band, reason_code, note, by, at, status, decided_by, decided_at, decision_note}


class Evidence(TenantScoped, Base):
    """A document held for a customer. The bytes live in object storage; this row holds the fingerprint and the chain of custody."""

    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(300))  # where the original is, relative to the client's evidence store
    status: Mapped[str] = mapped_column(String(12), default="current", index=True)  # current | superseded | withdrawn
    expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(40))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    verified_by: Mapped[str | None] = mapped_column(String(40), nullable=True)  # a person confirmed it against the original
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    redacted_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    redacted_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redactions: Mapped[list] = mapped_column(JSON, default=list)  # [{kind, count}]
    custody: Mapped[list] = mapped_column(JSON, default=list)  # [{at, by, action, detail, prev, hash}], each entry sealing the one before


class KycTrigger(TenantScoped, Base):
    """Something that happened to a customer between reviews and what was done about it."""

    __tablename__ = "kyc_trigger"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(200))  # the same event never fires twice
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    outcome: Mapped[str] = mapped_column(String(16))  # opened_review | joined_review | noted
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Watchlist(TenantScoped, Base):
    """A list the client screens against: a sanctions list it subscribes to, or its own list of names it will not deal with."""

    __tablename__ = "watchlist"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12), default="sanctions")  # sanctions | internal
    source: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[int] = mapped_column(Integer, default=0)  # goes up with every load or edit
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{version, at, by, added, changed, removed, note}]


class WatchlistEntry(TenantScoped, Base):
    """One name on a list. An entry taken off a list is kept, marked with the version that removed it."""

    __tablename__ = "watchlist_entry"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    list_id: Mapped[str] = mapped_column(String(40), index=True)
    ref: Mapped[str] = mapped_column(String(60))  # the publisher's own id for the entry
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(12), default="entity")  # entity | person | vessel
    countries: Mapped[list] = mapped_column(JSON, default=list)
    programs: Mapped[list] = mapped_column(JSON, default=list)
    identifiers: Mapped[list] = mapped_column(JSON, default=list)  # [{type, value}]
    added_version: Mapped[int] = mapped_column(Integer, default=1)
    removed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ScreeningExemption(TenantScoped, Base):
    """A decision that one name is not one list entry, so the same false match is not raised again. Two people, and an end date."""

    __tablename__ = "screening_exemption"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200))  # the name as screened
    subject_key: Mapped[str] = mapped_column(String(200), index=True)
    entry_id: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending | active | refused | revoked
    proposed_by: Mapped[str] = mapped_column(String(40))
    proposed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    entry_version: Mapped[str] = mapped_column(String(64), default="")  # fingerprint of the entry as it was; a changed entry voids the exemption


class Hold(TenantScoped, Base):
    """Money or access stopped while a case is decided: a held payment, a frozen account, a blocked card."""

    __tablename__ = "hold"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(16))  # payment | account | card | online_access
    target_id: Mapped[str] = mapped_column(String(60), default="")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    reason_code: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)  # active | released | blocked | rejected
    placed_by: Mapped[str] = mapped_column(String(40))
    placed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    review_by: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # a hold nobody has looked at by then is escalated
    overdue_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    challenge: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # step-up: {method, status, requested_by, requested_at, completed_at, note}
    proposal: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {outcome, note, by, at} waiting for a second person
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{at, by, what, note}]


class Dispute(TenantScoped, Base):
    """A customer's claim that payments were not theirs, followed through credit, recovery from the other bank, and the final loss."""

    __tablename__ = "dispute"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    party_id: Mapped[str] = mapped_column(String(40), index=True)
    txn_ids: Mapped[list] = mapped_column(JSON, default=list)
    amount: Mapped[float] = mapped_column(Float)
    rail: Mapped[str] = mapped_column(String(10))  # card | ach | wire | p2p
    reason_code: Mapped[str] = mapped_column(String(40))
    rule: Mapped[str] = mapped_column(String(20), default="")  # the dispute_rules code whose clocks apply
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open | credited | recovery | closed
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)  # recovered | partly_recovered | written_off | customer_liable | withdrawn
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=now)  # when the customer told the bank
    opened_by: Mapped[str] = mapped_column(String(40))
    credit_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    credit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    credit_amount: Mapped[float] = mapped_column(Float, default=0.0)
    credit_final: Mapped[bool] = mapped_column(Boolean, default=False)
    resolve_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recovery_ref: Mapped[str] = mapped_column(String(60), default="")  # chargeback or recall reference
    recovery_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recovery_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # the last day the network accepts one
    recovered: Mapped[float] = mapped_column(Float, default=0.0)
    customer_liability: Mapped[float] = mapped_column(Float, default=0.0)
    loss: Mapped[float] = mapped_column(Float, default=0.0)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{at, by, what, note, amount}]


class InfoShare(TenantScoped, Base):
    """A request from law enforcement, through the regulator, to search for named subjects (in the US, section 314(a))."""

    __tablename__ = "info_share"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    reference: Mapped[str] = mapped_column(String(60))  # the tracking number on the request
    __table_args__ = (UniqueConstraint("tenant_id", "reference"),)
    received: Mapped[datetime] = mapped_column(DateTime, default=now)
    due: Mapped[datetime] = mapped_column(DateTime)
    loaded_by: Mapped[str] = mapped_column(String(40))
    subjects: Mapped[list] = mapped_column(JSON, default=list)  # [{no, name, kind, aliases, dob, identifiers, country}]
    status: Mapped[str] = mapped_column(String(14), default="received", index=True)  # received | searched | responded | no_match
    searched_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    searched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)  # what was searched: counts and look-back periods
    matches: Mapped[list] = mapped_column(JSON, default=list)  # [{id, subject_no, where, party_id, name, score, basis, decision, decided_by, decided_at, note}]
    responded_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ScenarioVersion(TenantScoped, Base):
    """A client's own settings for one detection scenario. Proposed by one person with a backtest, put live by another."""

    __tablename__ = "scenario_version"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(60), index=True)
    number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3 per scenario, in the order they were proposed
    __table_args__ = (UniqueConstraint("tenant_id", "scenario", "number"),)
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # the complete settings, not a difference
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | live | refused | withdrawn | superseded
    reason: Mapped[str] = mapped_column(Text, default="")
    backtest: Mapped[dict] = mapped_column(JSON, default=dict)  # what the change would have done to past alerts, as shown to the approver
    proposed_by: Mapped[str] = mapped_column(String(40))
    proposed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")


class StreamJobRun(TenantScoped, Base):
    """One start or stop of a client's stream job from the product: who, when, and the settings the job was given."""

    __tablename__ = "stream_job_run"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job: Mapped[str] = mapped_column(String(60), index=True)
    action: Mapped[str] = mapped_column(String(12))  # started | stopped
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # the scenario settings written into the job (started only)
    engine_job_id: Mapped[str] = mapped_column(String(64), default="")  # the job's id on the cluster
    by: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    note: Mapped[str] = mapped_column(Text, default="")


class Lookback(TenantScoped, Base):
    """A lookback project: a past period run again through one scenario, usually with different settings, and worked to a conclusion."""

    __tablename__ = "lookback"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(Text, default="")  # why it is being done: a regulator's finding, a defect, a new typology
    scenario: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # the settings the period is run with
    period_from: Mapped[datetime] = mapped_column(DateTime)
    period_to: Mapped[datetime] = mapped_column(DateTime)
    segment: Mapped[dict] = mapped_column(JSON, default=dict)  # which customers: {risk_ratings: [...]} or everyone
    status: Mapped[str] = mapped_column(String(10), default="planned", index=True)  # planned | working | closed | cancelled
    due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(40))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    preview: Mapped[dict] = mapped_column(JSON, default=dict)  # what a run would raise, counted before anything is raised
    run_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # the figures as they stood when it closed
    history: Mapped[list] = mapped_column(JSON, default=list)


class AccessReview(TenantScoped, Base):
    """A recertification of access: everyone's access as it stood, and a manager's decision on each person."""

    __tablename__ = "access_review"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open | closed
    policy_version: Mapped[str] = mapped_column(String(20), default="")  # the access policy the snapshot was taken from
    started_by: Mapped[str] = mapped_column(String(40))
    started: Mapped[datetime] = mapped_column(DateTime, default=now)
    due: Mapped[datetime] = mapped_column(DateTime)
    # [{person, name, title, roles, permissions, queues, read_only, last_active, open_cases, flags, decision, note, decided_by, decided_at}]
    items: Mapped[list] = mapped_column(JSON, default=list)
    closed_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OutboxEvent(TenantScoped, Base):
    """A domain event waiting to be published, written in the same transaction as the change it describes.

    The row holds the finished message, so whichever publisher moves it (the built-in relay, or Debezium reading the
    database log) sends the same bytes. `id` gives the order rows were written within one database.
    """

    __tablename__ = "outbox_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True)  # consumers drop a redelivery by this
    topic: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(60))  # everything about one case or one customer stays in order
    type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[str] = mapped_column(Text)  # a CloudEvents 1.0 JSON envelope
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class StreamStat(TenantScoped, Base):
    """Running totals for one inbound stream at one client: what arrived, what was loaded and what was set aside."""

    __tablename__ = "stream_stat"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream: Mapped[str] = mapped_column(String(40))
    received: Mapped[int] = mapped_column(Integer, default=0)
    loaded: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    alerts: Mapped[int] = mapped_column(Integer, default=0)
    last_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reject: Mapped[str] = mapped_column(String(300), default="")
    __table_args__ = (UniqueConstraint("tenant_id", "stream"),)


class ProviderStaff(Base):
    """A member of the platform provider's own staff. Not client data, so not tenant-scoped: it is the provider's directory.

    `console` staff run the operator console. Anyone active can be given an engagement at a client that allows it.
    """

    __tablename__ = "provider_staff"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    console: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)


class Engagement(TenantScoped, Base):
    """A place at one client for one member of the provider's staff: the roles and queues they hold, and until when.

    Proposed by the provider, approved by the client (unless the client's policy waives approval), and ended by either
    side or by the calendar. It belongs to the client, so the client's own isolation applies to it.
    """

    __tablename__ = "engagement"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    staff_id: Mapped[str] = mapped_column(String(40), index=True)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    queues: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="proposed", index=True)  # proposed | active | rejected | ended
    starts: Mapped[datetime] = mapped_column(DateTime, default=now)
    ends: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(Text, default="")
    proposed_by: Mapped[str] = mapped_column(String(60))
    proposed: Mapped[datetime] = mapped_column(DateTime, default=now)
    decided_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    decided: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ended: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_reason: Mapped[str] = mapped_column(String(300), default="")


class AccessEvent(TenantScoped, Base):
    """Append-only record of access worth a second look: provider staff at work, engagements changing hands, and later
    break-glass openings. The client reads its own; the provider cannot edit it."""

    __tablename__ = "access_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    actor: Mapped[str] = mapped_column(String(60))
    org: Mapped[str] = mapped_column(String(10))  # client | provider | system
    kind: Mapped[str] = mapped_column(String(60), index=True)
    subject: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


from ..backbone import outbox as _outbox  # noqa: E402,F401  registers the hook that fills the outbox
