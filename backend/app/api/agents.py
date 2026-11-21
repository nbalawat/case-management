"""The agent layer for one client: the roster and its dial, the kill switch, evaluations, oversight and the ledger."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..agents import batch, copilot, evals, ledger, oversight, policy as policies, triage
from ..agents.catalog import LEVEL_HELP, LEVEL_LABELS, catalog
from ..agents.investigator import investigate
from ..domain.models import Case
from ..domain.personas import Persona, policy_of
from ..tenants.registry import TenantRuntime
from .deps import get_case_or_404, get_session, persona, require, tenant

router = APIRouter(prefix="/api")
MANAGE = "manage_agents"


def viewer(p: Persona = Depends(persona)) -> Persona:
    """Someone who may read the agent record. Read-only roles such as auditors are welcome here."""
    if not p.can("view_team"):
        raise HTTPException(403, f"{p.title} cannot see this. It needs the 'view team' permission.")
    return p


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except (policies.PolicyError, batch.BatchError, triage.TriageError) as e:
        raise HTTPException(422, str(e)) from None


def _job(j) -> dict:
    return {"id": j.id, "kind": j.kind, "params": j.params, "result": j.result, "started_by": j.started_by, "started": j.started.isoformat()}


@router.get("/agents")
def get_agents(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(persona)):
    """Everything the Agents page shows. Anyone at the client may read it; changing anything needs the manage agents permission."""
    inv = oversight.inventory(s)
    people = policy_of(rt.id).people
    closed = triage.closed_by_agents(s)
    return inv | {
        "can_manage": p.can(MANAGE), "can_review": p.can("view_team") and not p.read_only,
        "manager": ", ".join(x.name for x in people.values() if x.can(MANAGE)) or "nobody",
        "level_help": LEVEL_HELP, "policy": policies.current(s).body, "history": policies.history(s)[:15],
        "evaluation": evals.latest(s, inv["fingerprint"]), "challenger_report": evals.challenger(s),
        "agreement": oversight.agreement(s),
        "closed_by_agents": closed, "awaiting_review": sum(1 for a in closed if a["qa_sample"] and not a["qa_result"]),
        "jobs": batch.jobs(s, limit=12),
    }


class PolicyIn(BaseModel):
    policy: dict
    note: str = ""


@router.post("/agents/policy/check")
def check_policy(body: PolicyIn, s: Session = Depends(get_session), _: Persona = Depends(require(MANAGE))):
    try:
        return {"ok": True} | policies.check(s, body.policy)
    except policies.PolicyError as e:
        return {"ok": False, "error": str(e)}


@router.post("/agents/policy/publish")
def publish_policy(body: PolicyIn, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    pol = _guard(policies.publish, s, body.policy, p.id, body.note)
    return {"ok": True, "version": pol.version}


class EvalIn(BaseModel):
    policy: dict | None = None  # a draft to evaluate; omit to evaluate the settings in force


@router.post("/agents/evaluations")
def run_evaluation(body: EvalIn, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    return _job(_guard(evals.run, s, p.id, body.policy))


class SwitchIn(BaseModel):
    on: bool
    reason: str = ""


@router.post("/agents/{agent_id}/switch")
def switch(agent_id: str, body: SwitchIn, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    if agent_id not in catalog():
        raise HTTPException(404, "Unknown agent")
    pol = _guard(policies.switch, s, agent_id, body.on, p.id, body.reason)
    return {"ok": True, "version": pol.version, "enabled": pol.enabled(agent_id)}


class LookbackIn(BaseModel):
    days: int = 90
    case_type: str | None = None


@router.post("/agents/lookback")
def run_lookback(body: LookbackIn, s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    return _job(_guard(batch.lookback, s, p.id, body.days, body.case_type))


@router.post("/agents/tuning")
def run_tuning(s: Session = Depends(get_session), p: Persona = Depends(require(MANAGE))):
    return _job(_guard(batch.tuning, s, p.id))


class ReviewIn(BaseModel):
    confirm: bool


@router.post("/agents/closed-alerts/{alert_id}/review")
def review_closed(alert_id: str, body: ReviewIn, s: Session = Depends(get_session), p: Persona = Depends(require("view_team"))):
    """A person checks an alert an agent closed: agree, or reopen it into a case."""
    if p.read_only:
        raise HTTPException(403, "Read-only access")
    a = _guard(triage.review, s, alert_id, p, body.confirm)
    if a.case_id and not body.confirm:
        case = s.get(Case, a.case_id)
        if case is not None:
            investigate(s, case)
    return {"ok": True, "result": a.qa_result, "case_id": a.case_id}


@router.get("/agents/ledger")
def get_ledger(case_id: str | None = None, agent: str | None = None, limit: int = 100, s: Session = Depends(get_session),
               _: Persona = Depends(viewer)):
    return {"entries": ledger.entries(s, case_id=case_id, agent=agent, limit=max(1, min(limit, 500))), "chain": ledger.verify(s)}


@router.get("/agents/inventory.md", response_class=PlainTextResponse)
def inventory_pack(s: Session = Depends(get_session), _: Persona = Depends(viewer)):
    """The documentation pack for model risk review, as a document."""
    return oversight.inventory_markdown(oversight.inventory(s))


class QuestionIn(BaseModel):
    question: str


@router.post("/cases/{case_id}/copilot")
def ask_copilot(case_id: str, body: QuestionIn, s: Session = Depends(get_session), p: Persona = Depends(persona)):
    case = get_case_or_404(s, case_id)
    try:
        return copilot.ask(s, case, p, body.question)
    except copilot.CopilotOff as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/agents/levels")
def levels():
    return [{"id": k, "label": v, "help": LEVEL_HELP[k]} for k, v in LEVEL_LABELS.items()]
