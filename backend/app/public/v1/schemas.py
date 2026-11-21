"""The public shapes of v1. These are a contract: fields are added, never renamed or removed, within a version."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CaseSummary(Out):
    id: str
    case_type: str = Field(description="The case type id, see /case-types")
    type_title: str
    domain: str
    state: str
    state_label: str
    state_kind: str = Field(description="open, closed, or a kind the case type defines")
    open: bool
    priority: str = Field(description="critical, high, medium or low")
    owner: str | None
    queue: str | None
    party_id: str
    party_name: str
    created: datetime
    sla_due: datetime | None
    stage_due: datetime | None
    stage_breached: bool
    parent_id: str | None
    disposition: str | None
    restricted: bool
    agent_recommendation: str | None


class Transition(Out):
    to: str
    label: str
    to_label: str
    needs_note: bool
    blocked: str | None = Field(description="Why the caller cannot take this transition now, if they cannot")
    four_eyes: bool
    reasons: list[dict] = []


class Task(Out):
    id: str
    label: str
    done: bool
    required: bool = True


class CaseDetail(CaseSummary):
    narrative: str
    type_version: str
    tasks: list[dict]
    transitions: list[Transition]
    facts: dict


class CaseEventOut(Out):
    id: int
    case_id: str
    ts: datetime
    actor: str
    actor_kind: str = Field(description="human, agent or system. API clients appear as human actors named app:<client_id>")
    kind: str
    payload: dict


class AlertOut(Out):
    id: str
    source: str
    scenario: str
    scenario_version: str
    party_id: str
    score: float
    explanation: str
    txn_ids: list[str]
    status: str
    case_id: str | None
    created: datetime


class AlertIn(BaseModel):
    source: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$", description="The sending engine, for example 'actimize'")
    external_id: str = Field(min_length=1, max_length=70, description="The engine's own alert id. Sending the same id again changes nothing.")
    scenario: str = Field(min_length=1, max_length=60)
    party_id: str = Field(min_length=1, max_length=40)
    party_name: str | None = Field(None, max_length=200)
    score: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=4000)
    txn_ids: list[str] = Field(default_factory=list, max_length=500)


class AlertAccepted(Out):
    alert_id: str
    status: str
    duplicate: bool
    case_id: str | None = Field(description="The case the hub put the alert in, when the caller may see it")


class AlertBatchIn(BaseModel):
    alerts: list[AlertIn] = Field(min_length=1, max_length=500)


class AlertBatchOut(Out):
    accepted: list[AlertAccepted]


class Account(Out):
    id: str
    product: str
    status: str


class Customer(Out):
    id: str
    kind: str
    name: str
    country: str
    occupation: str
    risk_rating: str
    pep: bool
    onboarded: datetime


class TransactionOut(Out):
    id: str
    account_id: str
    party_id: str
    ts: datetime
    amount: float
    currency: str
    direction: str
    channel: str
    counterparty: str
    counterparty_country: str


class TransitionIn(BaseModel):
    to: str = Field(min_length=1, max_length=40, description="The target state; see `transitions` on the case")
    note: str = Field("", max_length=8000)
    reason: str = Field("", max_length=60, description="A reason code, when the outcome asks for one")


class NarrativeIn(BaseModel):
    text: str = Field(max_length=100_000)


class AssignIn(BaseModel):
    owner: str = Field(min_length=1, max_length=40, description="A member of the case's queue")
    reason: str = Field("", max_length=400)


class TaskIn(BaseModel):
    done: bool = True


class InfoRequestIn(BaseModel):
    recipient: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=4000)
    due_days: int = Field(5, ge=1, le=60)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(None, max_length=40)


class ScreenIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=100, description="Names to screen against the client's lists in force")


class Ok(Out):
    ok: bool = True
