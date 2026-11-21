"""HTTP API. Everything the UI does goes through these endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.investigator import investigate
from ..alerts.hub import ingest_external_alert, route_alerts
from ..cases.case_types import load_case_types
from ..cases.service import TransitionError, allowed_transitions, record, save_narrative, transition
from ..config import settings
from ..db import get_session
from ..detection.engine import run_detection
from ..detection.scenarios import REGISTRY
from ..domain.models import AgentRun, Alert, Case, CaseEvent, Feed, Filing, Party, Transaction
from ..domain.personas import PERSONAS, Persona, get_persona
from ..ingest.connectors import CONNECTOR_CATALOGUE, CsvConnector
from ..ingest.service import profile, run_feed
from ..reporting.metrics import dashboard
from ..scale import benchmark

router = APIRouter(prefix="/api")


def persona(x_persona: str | None = Header(default=None)) -> Persona:
    return get_persona(x_persona)


def _case_row(c: Case, types: dict) -> dict:
    ct = types[c.case_type]
    return {"id": c.id, "case_type": c.case_type, "type_title": ct["title"], "state": c.state,
            "state_label": ct["states"][c.state]["label"], "open": c.closed is None, "priority": c.priority, "owner": c.owner,
            "party_id": c.party_id, "party_name": c.party.name, "created": c.created.isoformat(),
            "sla_due": c.sla_due.isoformat() if c.sla_due else None, "sla_clock": ct.get("sla", {}).get("clock"),
            "disposition": c.disposition, "agent_recommendation": c.agent_recommendation}


def _txn(t: Transaction, alerted: set[str]) -> dict:
    return {"id": t.id, "ts": t.ts.isoformat(), "amount": t.amount, "direction": t.direction, "channel": t.channel,
            "counterparty": t.counterparty, "country": t.counterparty_country, "alerted": t.id in alerted}


@router.get("/meta")
def meta():
    return {"tenant": settings.tenant, "personas": [p.__dict__ for p in PERSONAS.values()],
            "sample_data": True, "llm_provider": settings.llm_provider}


@router.get("/dashboard")
def get_dashboard(s: Session = Depends(get_session)):
    return dashboard(s)


@router.get("/cases")
def list_cases(status: str = "open", s: Session = Depends(get_session)):
    types = load_case_types()
    q = select(Case).order_by(Case.created.desc())
    q = q.where(Case.closed.is_(None)) if status == "open" else q.where(Case.closed.is_not(None)) if status == "closed" else q
    cases = list(s.scalars(q))
    n_alerts: dict[str, int] = {}
    for cid in s.scalars(select(Alert.case_id).where(Alert.case_id.is_not(None))):
        n_alerts[cid] = n_alerts.get(cid, 0) + 1
    return [_case_row(c, types) | {"alerts": n_alerts.get(c.id, 0)} for c in cases]


def _get_case(s: Session, case_id: str) -> Case:
    c = s.get(Case, case_id)
    if c is None:
        raise HTTPException(404, "Case not found")
    return c


@router.get("/cases/{case_id}")
def get_case(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    c = _get_case(s, case_id)
    types = load_case_types()
    alerts = list(s.scalars(select(Alert).where(Alert.case_id == c.id).order_by(Alert.created)))
    alerted = {i for a in alerts for i in a.txn_ids}
    txns = list(s.scalars(select(Transaction).where(Transaction.party_id == c.party_id).order_by(Transaction.ts.desc()).limit(400)))
    shown = [t for t in txns if t.id in alerted] + [t for t in txns if t.id not in alerted][:60]
    run = s.scalars(select(AgentRun).where(AgentRun.case_id == c.id).order_by(AgentRun.started.desc())).first()
    filing = s.scalars(select(Filing).where(Filing.case_id == c.id)).first()
    others = [x for x in s.scalars(select(Case).where(Case.party_id == c.party_id, Case.id != c.id))]
    ct = types[c.case_type]
    return {
        "case": _case_row(c, types) | {"narrative": c.narrative, "drafted_by": c.drafted_by, "type_version": c.type_version},
        "party": {"id": c.party.id, "name": c.party.name, "kind": c.party.kind, "occupation": c.party.occupation,
                  "country": c.party.country, "risk_rating": c.party.risk_rating, "pep": c.party.pep,
                  "onboarded": c.party.onboarded.date().isoformat(), "accounts": [a.id for a in c.party.accounts]},
        "alerts": [{"id": a.id, "scenario": a.scenario, "title": REGISTRY[a.scenario]["title"], "version": a.scenario_version,
                    "typology": REGISTRY[a.scenario]["typology"], "score": a.score, "explanation": a.explanation,
                    "created": a.created.isoformat(), "source": a.source, "txn_ids": a.txn_ids} for a in alerts],
        "transactions": [_txn(t, alerted) for t in sorted(shown, key=lambda t: t.ts, reverse=True)],
        "agent_run": None if run is None else {"id": run.id, "provider": run.provider, "recommendation": run.recommendation,
                                               "confidence": run.confidence, "steps": run.steps,
                                               "finished": run.finished.isoformat() if run.finished else None},
        "events": [{"id": e.id, "ts": e.ts.isoformat(), "actor": e.actor, "actor_kind": e.actor_kind, "kind": e.kind, "payload": e.payload}
                   for e in s.scalars(select(CaseEvent).where(CaseEvent.case_id == c.id).order_by(CaseEvent.ts, CaseEvent.id))],
        "transitions": allowed_transitions(c, p),
        "workflow": {"states": [{"id": k, "label": v["label"], "kind": v.get("kind")} for k, v in ct["states"].items()],
                     "autonomy": ct.get("agents", {}).get("autonomy")},
        "filing": None if filing is None else {"id": filing.id, "kind": filing.kind, "filed_by": filing.filed_by,
                                               "filed_at": filing.filed_at.isoformat(), "sha256": filing.sha256},
        "other_cases": [_case_row(x, types) for x in others],
    }


class TransitionIn(BaseModel):
    to: str
    note: str = ""


@router.post("/cases/{case_id}/transition")
def post_transition(case_id: str, body: TransitionIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    c = _get_case(s, case_id)
    try:
        transition(s, c, body.to, p, body.note)
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e
    if c.owner is None:
        c.owner = p.id
    return {"ok": True, "state": c.state}


class NarrativeIn(BaseModel):
    text: str


@router.post("/cases/{case_id}/narrative")
def post_narrative(case_id: str, body: NarrativeIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    save_narrative(s, _get_case(s, case_id), body.text, p)
    return {"ok": True}


@router.post("/cases/{case_id}/assign")
def post_assign(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    c = _get_case(s, case_id)
    c.owner = p.id
    record(s, c.id, p.id, "human", "case.assigned", {"owner": p.id})
    return {"ok": True}


@router.post("/cases/{case_id}/agents/run")
def post_agent_run(case_id: str, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    c = _get_case(s, case_id)
    record(s, c.id, p.id, "human", "agent.requested", {})
    run = investigate(s, c)
    return {"ok": True, "run_id": run.id}


@router.get("/case-types")
def get_case_types():
    return [{k: v for k, v in ct.items() if k != "source"} | {"yaml": ct["source"]} for ct in load_case_types().values()]


@router.get("/scenarios")
def get_scenarios():
    return [{k: v for k, v in sc.items() if k != "fn"} for sc in REGISTRY.values()]


# --- data onboarding --------------------------------------------------------
class FeedIn(BaseModel):
    sample: str | None = None
    name: str | None = None
    text: str | None = None
    mapping: dict[str, str | None] | None = None


def _connector(body: FeedIn) -> CsvConnector:
    if body.text:
        return CsvConnector(body.name or "uploaded.csv", body.text)
    path = (settings.samples_dir / (body.sample or "legacy_core_transactions.csv")).resolve()
    if settings.samples_dir.resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "Sample not found")
    return CsvConnector.from_path(path)


@router.get("/feeds")
def list_feeds(s: Session = Depends(get_session)):
    return {"feeds": [{"id": f.id, "name": f.name, "source_system": f.source_system, "status": f.status,
                       "created": f.created.isoformat(), "stats": f.stats} for f in s.scalars(select(Feed).order_by(Feed.created.desc()))],
            "samples": sorted(p.name for p in settings.samples_dir.glob("*.csv"))}


@router.post("/feeds/profile")
def post_profile(body: FeedIn):
    return profile(_connector(body))


@router.post("/feeds/run")
def post_run(body: FeedIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    if not body.mapping:
        raise HTTPException(422, "mapping is required")
    before = set(s.scalars(select(Party.id)))
    feed = run_feed(s, _connector(body), body.mapping)
    touched = set(s.scalars(select(Transaction.party_id).where(Transaction.feed_id == feed.id).distinct()))
    alerts = run_detection(s, touched)
    cases = route_alerts(s, alerts)
    for c in cases:
        investigate(s, c)
    return {"feed": {"id": feed.id, "name": feed.name, "status": feed.status, "stats": feed.stats},
            "new_parties": len(set(s.scalars(select(Party.id))) - before), "alerts": len(alerts),
            "cases": [{"id": c.id, "party_name": c.party.name, "priority": c.priority} for c in cases], "run_by": p.id}


class AlertIn(BaseModel):
    source: str = Field(min_length=1, max_length=40, description="Sending engine, for example 'actimize' or 'in-house-tm'")
    external_id: str = Field(min_length=1, max_length=70, description="The engine's own alert id; replays are ignored")
    scenario: str = Field(max_length=60)
    party_id: str = Field(min_length=1, max_length=40)
    party_name: str | None = None
    score: float = Field(ge=0, le=1)
    explanation: str
    txn_ids: list[str] = []


@router.post("/alerts", status_code=201)
def post_alert(body: AlertIn, s: Session = Depends(get_session)):
    """Overlay mode: an incumbent monitoring engine pushes its alerts here; the hub groups them into cases."""
    alert, case = ingest_external_alert(s, **body.model_dump())
    if case is not None:
        investigate(s, case)
    return {"alert_id": alert.id, "case_id": alert.case_id, "duplicate": case is None}


@router.get("/integrations")
def integrations():
    return {
        "connectors": CONNECTOR_CATALOGUE,
        "interfaces": [
            {"name": "REST API (OpenAPI)", "status": "live", "note": "Interactive docs at /docs"},
            {"name": "Audit event stream", "status": "live", "note": "GET /api/events; Kafka CloudEvents in milestone 2"},
            {"name": "MCP tool server for agents", "status": "planned", "note": "Milestone 2"},
            {"name": "SSO via OIDC (Keycloak)", "status": "planned", "note": "Milestone 3; demo uses a persona switch"},
            {"name": "SAR e-filing (FinCEN BSA XML, goAML)", "status": "planned", "note": "Milestone 3; demo files a hashed JSON record"},
        ],
    }


@router.get("/events")
def events(limit: int = 50, s: Session = Depends(get_session)):
    return [{"id": e.id, "ts": e.ts.isoformat(), "case_id": e.case_id, "actor": e.actor, "actor_kind": e.actor_kind,
             "kind": e.kind, "payload": e.payload} for e in s.scalars(select(CaseEvent).order_by(CaseEvent.ts.desc(), CaseEvent.id.desc()).limit(min(limit, 500)))]


class BenchIn(BaseModel):
    events: int = 200_000


@router.post("/scale/benchmark")
def post_benchmark(body: BenchIn):
    return benchmark(max(10_000, min(body.events, 1_000_000)))
