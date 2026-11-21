"""The tools an answering agent may call. Every one is read-only and runs in the asker's own client session.

A tool is plain data (name, description, JSON schema) plus a handler, so any model provider can be handed the
same set. The permission on a tool is checked here, not in the prompt: a model cannot talk its way past it.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import AgentRun, Alert, Case, CaseEvent, InfoRequest, Party
from ..domain.personas import Persona, policy_of
from ..tenancy import tenant_of
from ..visibility import find_case
from . import semantic
from .semantic import FIELDS, MEASURES, QueryError

TEAM = "view_team"  # the permission that already guards the Team page


@dataclass
class ToolContext:
    """What a tool may touch: one client session and the person asking."""

    s: Session
    asker: Persona
    _facts: list[dict] | None = None

    @property
    def facts(self) -> list[dict]:
        if self._facts is None:
            self._facts = semantic.case_facts(self.s, self.asker)
        return self._facts


@dataclass
class ToolResult:
    data: dict
    case_ids: list[str] = field(default_factory=list)  # every case this result showed the model
    error: bool = False
    summary: str = ""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[ToolContext, dict], ToolResult]
    permission: str | None = None


FILTERS = {
    "type": "array", "description": "All must hold. Text matching ignores case.",
    "items": {"type": "object", "additionalProperties": False, "required": ["field", "op", "value"], "properties": {
        "field": {"type": "string", "enum": list(FIELDS)},
        "op": {"type": "string", "enum": sorted(semantic.OPS)},
        "value": {"description": "A string, number or boolean; a list of strings for 'in'; an ISO date for date fields"},
    }},
}


def _field_guide() -> str:
    return "; ".join(f"{f.id} ({f.kind}{': ' + f.help if f.help else ''})" for f in FIELDS.values())


def _team_only(ctx: ToolContext, what: str) -> None:
    if not ctx.asker.can(TEAM):
        raise QueryError(f"{what} needs the team view, which the {ctx.asker.title} role does not hold. "
                         "Questions about your own cases still work.")


def _guard_owner(ctx: ToolContext, group_by: list[str]) -> None:
    if "owner" in group_by:
        _team_only(ctx, "Workload split by person")


def _breakdown(ctx: ToolContext, a: dict) -> ToolResult:
    _guard_owner(ctx, a.get("group_by") or [])
    out = semantic.breakdown(ctx.facts, group_by=a.get("group_by"), measures=a.get("measures"), filters=a.get("filters"),
                             sort_by=a.get("sort_by"), descending=a.get("descending", True), limit=a.get("limit", semantic.MAX_ROWS))
    ids = [i for r in out["rows"] for i in r["case_ids"]]
    return ToolResult(out, ids, summary=f"{out['matching_cases']} cases in {out['groups']} group(s)")


def _list(ctx: ToolContext, a: dict) -> ToolResult:
    out = semantic.list_cases(ctx.facts, filters=a.get("filters"), sort_by=a.get("sort_by", "opened"),
                              descending=a.get("descending", True), limit=a.get("limit", 10))
    return ToolResult(out, [c["id"] for c in out["cases"]], summary=f"{len(out['cases'])} of {out['matching_cases']} cases")


def _case(ctx: ToolContext, a: dict) -> ToolResult:
    s, cid = ctx.s, str(a.get("case_id", "")).strip()
    c = find_case(s, cid)
    if c is None:
        raise QueryError(f"No case '{cid}' exists for this client")
    fact = next(f for f in ctx.facts if f["id"] == c.id)
    people = policy_of(tenant_of(s)).people
    who = lambda x: people[x].name if x in people else x  # noqa: E731
    alerts = list(s.scalars(select(Alert).where(Alert.case_id == c.id).order_by(Alert.created)))
    events = list(s.scalars(select(CaseEvent).where(CaseEvent.case_id == c.id).order_by(CaseEvent.ts.desc(), CaseEvent.id.desc()).limit(12)))
    requests = list(s.scalars(select(InfoRequest).where(InfoRequest.case_id == c.id).order_by(InfoRequest.created)))
    run = s.scalars(select(AgentRun).where(AgentRun.case_id == c.id).order_by(AgentRun.started.desc())).first()
    related = [x.id for x in s.scalars(select(Case).where((Case.parent_id == c.id) | (Case.id == (c.parent_id or "")))) if x.id != c.id]
    data = {
        "case": {k: semantic._show(fact[k]) for k in ("id", "case_type", "status", "stage", "priority", "owner", "queue", "outcome",
                                                       "opened", "closed", "age_days", "alerts", "alerted_amount", "stage_overdue",
                                                       "past_deadline", "agent_recommendation", "agent_agreed")},
        "customer": {"id": c.party_id, "name": c.party.name, "country": c.party.country, "occupation": c.party.occupation,
                     "risk_rating": c.party.risk_rating, "pep": bool(c.party.pep)},
        "alerts": [{"scenario": x.scenario, "score": round(x.score, 2), "explanation": x.explanation} for x in alerts],
        "agent": None if run is None else {"recommendation": run.recommendation, "confidence": round(run.confidence, 2),
                                           "findings": [f["text"] for st in run.steps for f in st.get("findings", [])][:8]},
        "narrative": c.narrative[:2500], "information_requests": [
            {"to": r.recipient, "question": r.question, "status": r.status, "response": r.response[:600]} for r in requests],
        "recent_history": [{"when": e.ts.date().isoformat(), "who": who(e.actor), "what": e.kind.replace("_", " ").replace(".", " "),
                            **({"note": str(e.payload["note"])[:300]} if e.payload.get("note") else {}),
                            **({"to": e.payload["to"]} if e.payload.get("to") else {})} for e in events],
        "related_case_ids": related,
    }
    return ToolResult(data, [c.id] + related, summary=f"{c.id}: {fact['case_type']}, {fact['stage']}")


def _customer(ctx: ToolContext, a: dict) -> ToolResult:
    q = str(a.get("name_or_id", "")).strip().lower()
    if len(q) < 2:
        raise QueryError("Give a customer name or id")
    found = [p for p in ctx.s.scalars(select(Party).order_by(Party.name)) if q == p.id.lower() or q in p.name.lower()]
    by_customer: dict[str, list[dict]] = {}
    for f in ctx.facts:
        by_customer.setdefault(f["customer_id"], []).append(f)
    found.sort(key=lambda p: -len(by_customer.get(p.id, [])))
    out, ids = [], []
    for p in found[:5]:
        cases = sorted(by_customer.get(p.id, []), key=lambda f: f["opened"], reverse=True)
        ids += [f["id"] for f in cases[:10]]
        out.append({"id": p.id, "name": p.name, "kind": p.kind, "country": p.country, "occupation": p.occupation,
                    "risk_rating": p.risk_rating, "pep": bool(p.pep), "customer_since": p.onboarded.date().isoformat(),
                    "cases": len(cases), "open_cases": sum(f["status"] == "open" for f in cases),
                    "sars_filed": sum(f["sar_filed"] for f in cases), "alerted_amount": round(sum(f["alerted_amount"] for f in cases), 2),
                    "case_list": [{k: semantic._show(f[k]) for k in ("id", "case_type", "stage", "status", "outcome", "opened", "alerted_amount")}
                                  for f in cases[:10]]})
    return ToolResult({"customers": out, "matching_customers": len(found), "truncated": len(found) > 5}, ids,
                      summary=f"{len(found)} customer(s) matching '{q}'")


def _team(ctx: ToolContext, a: dict) -> ToolResult:
    _team_only(ctx, "Team workload")
    policy = policy_of(tenant_of(ctx.s))
    open_cases = [f for f in ctx.facts if f["status"] == "open"]
    owned, late = Counter(f["owner"] for f in open_cases), Counter(f["owner"] for f in open_cases if f["stage_overdue"])
    closed = Counter(f["owner"] for f in ctx.facts if f["status"] == "closed")
    people = [{"name": p.name, "title": p.title, "queues": [policy.queues[q] for q in p.queues], "open_cases": owned[p.name],
               "past_stage_clock": late[p.name], "closed_cases": closed[p.name]} for p in policy.people.values() if p.can("work")]
    queues = []
    for label in policy.queues.values():
        inq = [f for f in open_cases if f["queue"] == label]
        queues.append({"queue": label, "open_cases": len(inq), "unassigned": sum(f["owner"] == "Unassigned" for f in inq),
                       "past_stage_clock": sum(f["stage_overdue"] for f in inq),
                       "oldest_days": max((f["age_days"] for f in inq), default=None)})
    worst = sorted((f for f in open_cases if f["stage_overdue"]), key=lambda f: -f["age_days"])[:8]
    return ToolResult({"people": sorted(people, key=lambda p: -p["open_cases"]), "queues": queues,
                       "oldest_past_stage_clock": [{k: semantic._show(f[k]) for k in ("id", "case_type", "stage", "owner", "age_days")} for f in worst]},
                      [f["id"] for f in worst], summary=f"{len(people)} people, {len(queues)} queues")


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("breakdown",
         "Count cases and compute measures, optionally split by one or two fields. Use this for any 'how many', 'how much', "
         "'average', 'trend', 'compare' or 'which ... has the most' question. Each row also carries up to five example case ids "
         "you can cite. Fields: " + _field_guide() + ". Measures: " + "; ".join(f"{k} ({v})" for k, v in MEASURES.items()) + ".",
         {"type": "object", "additionalProperties": False, "properties": {
             "group_by": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": [k for k, f in FIELDS.items() if f.group]}},
             "measures": {"type": "array", "items": {"type": "string", "enum": list(MEASURES)}, "description": "Defaults to ['cases']"},
             "filters": FILTERS,
             "sort_by": {"type": "string", "description": "One of the measures or group_by fields in this call"},
             "descending": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": semantic.MAX_ROWS}}},
         _breakdown),
    Tool("list_cases",
         "List the individual cases that match, sorted, with customer, stage, owner and amount. Use this to find the largest, "
         "oldest or newest cases, or the cases behind a number. Same filter fields as breakdown.",
         {"type": "object", "additionalProperties": False, "properties": {
             "filters": FILTERS, "sort_by": {"type": "string", "enum": [k for k, f in FIELDS.items() if f.kind != "list"]},
             "descending": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": semantic.MAX_LIST}}},
         _list),
    Tool("case_detail",
         "Everything about one case: customer, alerts and why they fired, the agent's findings, the narrative, information "
         "requests and recent history. Use it before explaining or summarising a specific case.",
         {"type": "object", "additionalProperties": False, "required": ["case_id"],
          "properties": {"case_id": {"type": "string", "description": "For example case-1a2b3c4d5e"}}},
         _case),
    Tool("customer",
         "Look up customers by name (or part of one) or id: profile, risk, and all of their cases.",
         {"type": "object", "additionalProperties": False, "required": ["name_or_id"], "properties": {"name_or_id": {"type": "string"}}},
         _customer),
    Tool("team_workload",
         "Open work per person and per queue, with what is past its stage clock. Only for roles that hold the team view.",
         {"type": "object", "additionalProperties": False, "properties": {}},
         _team, permission=TEAM),
]}


def available(asker: Persona) -> list[Tool]:
    """The tools this person's role may use. A tool they may not use is never offered to the model."""
    return [t for t in TOOLS.values() if t.permission is None or asker.can(t.permission)]


def _check_args(tool: Tool, args: Any) -> dict:
    if not isinstance(args, dict):
        raise QueryError("Tool input must be an object")
    props = tool.schema["properties"]
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise QueryError(f"Unknown input(s) for {tool.name}: {', '.join(unknown)}. Allowed: {', '.join(props) or 'none'}")
    missing = [k for k in tool.schema.get("required", []) if k not in args]
    if missing:
        raise QueryError(f"{tool.name} needs: {', '.join(missing)}")
    for k, v in args.items():
        kind = props[k].get("type")
        ok = {"array": isinstance(v, list), "string": isinstance(v, str), "boolean": isinstance(v, bool),
              "integer": isinstance(v, int) and not isinstance(v, bool)}.get(kind, True)
        if not ok:
            raise QueryError(f"'{k}' must be a {kind}")
    return args


def run(ctx: ToolContext, name: str, args: Any) -> ToolResult:
    """Run one tool call. Anything wrong with the call comes back as an error result the model can read and correct."""
    tool = TOOLS.get(name)
    try:
        if tool is None:
            raise QueryError(f"There is no tool '{name}'")
        if tool.permission and not ctx.asker.can(tool.permission):
            need = "the team view" if tool.permission == TEAM else f"the '{tool.permission.replace('_', ' ')}' permission"
            raise QueryError(f"{tool.name.replace('_', ' ').capitalize()} needs {need}, which the {ctx.asker.title} role does not hold. "
                             "Questions about your own cases still work.")
        return tool.handler(ctx, _check_args(tool, args))
    except QueryError as e:
        return ToolResult({"error": str(e)}, error=True, summary=str(e))
