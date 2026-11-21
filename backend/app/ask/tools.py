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
from .. import knowledge
from ..domain.personas import Persona, policy_of
from ..tenancy import tenant_of
from ..visibility import find_case
from . import calculator, semantic
from .landscape import catalog, query as landscape
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
    # Every other record this result showed the model, as (kind, id, link details), so an answer can cite them.
    records: list[dict] = field(default_factory=list)


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


def _object(ctx: ToolContext, a: dict):
    """The landscape object a call names, after the asker's permission on it is checked. None means cases."""
    name = str(a.get("object") or "cases")
    if name == "cases":
        return None
    try:
        obj = catalog.get(name)
    except KeyError:
        raise QueryError(f"There is no object '{name}'. Objects: cases, {', '.join(catalog.objects())}") from None
    denied = obj.permission(ctx.asker)
    if denied:
        raise QueryError(f"{obj.label.capitalize()}: {denied} Questions about cases still work.")
    return obj


def _refs(obj, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        rid = r.get(obj.id_dim)
        if rid is None:
            continue
        out.append({"kind": obj.kind, "id": str(rid), "case_id": r.get(obj.case_dim) if obj.case_dim else None,
                    "customer_id": r.get(obj.customer_dim) if obj.customer_dim else None})
    return out


def _breakdown(ctx: ToolContext, a: dict) -> ToolResult:
    obj = _object(ctx, a)
    if obj is not None:
        out = landscape.breakdown(ctx.s, ctx.asker, obj, group_by=a.get("group_by"), measures=a.get("measures"), filters=a.get("filters"),
                                  sort_by=a.get("sort_by"), descending=a.get("descending", True), limit=a.get("limit", landscape.MAX_ROWS))
        refs = [{"kind": obj.kind, "id": i} for r in out["rows"] for i in r["ids"]]
        return ToolResult(out, [i for r in out["rows"] for i in r["ids"]] if obj.kind == "case" else [], records=refs,
                          summary=f"{out['matching']} {obj.label} in {out['groups']} group(s)")
    _guard_owner(ctx, a.get("group_by") or [])
    out = semantic.breakdown(ctx.facts, group_by=a.get("group_by"), measures=a.get("measures"), filters=a.get("filters"),
                             sort_by=a.get("sort_by"), descending=a.get("descending", True), limit=a.get("limit", semantic.MAX_ROWS))
    ids = [i for r in out["rows"] for i in r["case_ids"]]
    return ToolResult(out, ids, summary=f"{out['matching_cases']} cases in {out['groups']} group(s)")


def _list(ctx: ToolContext, a: dict) -> ToolResult:
    out = semantic.list_cases(ctx.facts, filters=a.get("filters"), sort_by=a.get("sort_by", "opened"),
                              descending=a.get("descending", True), limit=a.get("limit", 10))
    return ToolResult(out, [c["id"] for c in out["cases"]], summary=f"{len(out['cases'])} of {out['matching_cases']} cases")


def _list_records(ctx: ToolContext, a: dict) -> ToolResult:
    obj = _object(ctx, a)
    if obj is None:
        return _list(ctx, {k: v for k, v in a.items() if k in ("filters", "sort_by", "descending", "limit")})
    out = landscape.list_records(ctx.s, ctx.asker, obj, filters=a.get("filters"), sort_by=a.get("sort_by"), descending=a.get("descending", True),
                                 limit=a.get("limit", 10), columns=a.get("fields"))
    return ToolResult(out, [r["case_id"] for r in out["records"] if r.get("case_id")], records=_refs(obj, out["records"]),
                      summary=f"{len(out['records'])} of {out['matching']} {obj.label}")


def _record(ctx: ToolContext, a: dict) -> ToolResult:
    obj = _object(ctx, a)
    rid = str(a.get("id", "")).strip()
    if obj is None:
        return _case(ctx, {"case_id": rid})
    row = landscape.get_one(ctx.s, ctx.asker, obj, rid)
    if row is None:
        raise QueryError(f"No {obj.kind.replace('_', ' ')} '{rid}' exists for this client, or it is not one you can see")
    return ToolResult({"object": obj.id, "record": row}, [row["case_id"]] if row.get("case_id") else [], records=_refs(obj, [row]),
                      summary=f"{obj.kind.replace('_', ' ')} {rid}")


def _calculate(ctx: ToolContext, a: dict) -> ToolResult:
    expr = str(a.get("expression", ""))
    value = calculator.evaluate(expr)
    return ToolResult({"expression": expr, "value": value}, summary=f"{expr} = {value}")


KINDS = ("typology", "policy", "procedure", "regulation")


def _policy_search(ctx: ToolContext, a: dict) -> ToolResult:
    q = str(a.get("query", "")).strip()
    if len(q) < 3:
        raise QueryError("Give a few words to search for, for example 'structuring indicators'")
    kind = a.get("kind")
    if kind is not None and kind not in KINDS:
        raise QueryError(f"kind must be one of {', '.join(KINDS)}")
    hits = knowledge.search(tenant_of(ctx.s), q, kind=kind, scenario=a.get("scenario"), limit=a.get("limit", 4))
    refs = [{"kind": "policy", "id": h["id"], "case_id": None, "customer_id": None, "label": f"{h['title']}: {h['section']}",
             "link": knowledge.link(h["id"]), "excerpt": h["text"][:600]} for h in hits]
    return ToolResult({"query": q, "passages": hits, "matching": len(hits)}, records=refs,
                      summary=f"{len(hits)} passage(s) for '{q}'")


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
    scope = landscape.Scope(ctx.s, ctx.asker)
    found = [p for p in ctx.s.scalars(select(Party).where(scope.party_ok(Party.id)).order_by(Party.name)) if q == p.id.lower() or q in p.name.lower()]
    by_customer: dict[str, list[dict]] = {}
    for f in ctx.facts:
        by_customer.setdefault(f["customer_id"], []).append(f)
    found.sort(key=lambda p: -len(by_customer.get(p.id, [])))
    out, ids = [], []
    for p in found[:5]:
        cases = sorted(by_customer.get(p.id, []), key=lambda f: f["opened"], reverse=True)
        ids += [f["id"] for f in cases[:10]]
        extra = landscape.get_one(ctx.s, ctx.asker, catalog.get("customers"), p.id) or {}
        out.append({"id": p.id, "name": p.name, "kind": p.kind, "country": p.country, "occupation": p.occupation,
                    "kyc_band": extra.get("kyc_band"), "active_holds": extra.get("active_holds"), "transactions_90d": extra.get("transactions_90d"),
                    "risk_rating": p.risk_rating, "pep": bool(p.pep), "customer_since": p.onboarded.date().isoformat(),
                    "cases": len(cases), "open_cases": sum(f["status"] == "open" for f in cases),
                    "sars_filed": sum(f["sar_filed"] for f in cases), "alerted_amount": round(sum(f["alerted_amount"] for f in cases), 2),
                    "case_list": [{k: semantic._show(f[k]) for k in ("id", "case_type", "stage", "status", "outcome", "opened", "alerted_amount")}
                                  for f in cases[:10]]})
    return ToolResult({"customers": out, "matching_customers": len(found), "truncated": len(found) > 5}, ids,
                      records=[{"kind": "customer", "id": c["id"], "case_id": None, "customer_id": c["id"]} for c in out],
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


def _all(kind: str) -> list[str]:
    """Every field (or measure) name across cases and the landscape objects, for the tool schemas. Each call is checked
    against its own object's vocabulary, with an error naming the allowed values."""
    objs = catalog.objects().values()
    if kind == "group":
        names = [k for k, f in FIELDS.items() if f.group] + [d for o in objs for d, x in o.dims.items() if x.group]
    elif kind == "field":
        names = list(FIELDS) + [d for o in objs for d in o.dims]
    else:
        names = list(MEASURES) + [m for o in objs for m in o.measures]
    return list(dict.fromkeys(names))


ANY_FILTERS = {
    "type": "array", "description": "All must hold. Text matching ignores case. Use 'in' with a list of ids to follow on from an earlier lookup.",
    "items": {"type": "object", "additionalProperties": False, "required": ["field", "op", "value"], "properties": {
        "field": {"type": "string", "enum": _all("field")},
        "op": {"type": "string", "enum": sorted(semantic.OPS | set(landscape.OPS))},
        "value": {"description": "A string, number or boolean; a list for 'in'; an ISO date for date fields"},
    }},
}
OBJECT = {"type": "string", "enum": ["cases", *catalog.objects()], "description": "What to count or list. Defaults to cases."}


def catalog_text(asker: Persona | None = None) -> str:
    """The vocabulary of every object this person may read, for the model."""
    objs = catalog.available(asker) if asker else list(catalog.objects().values())
    parts = ["cases: " + _field_guide() + ". Measures: " + "; ".join(f"{k} ({v})" for k, v in MEASURES.items())]
    for o in objs:
        v = o.vocabulary()
        parts.append(f"{o.id} ({o.description}): fields " + "; ".join(f"{k} {d}" for k, d in v["fields"].items())
                     + ". Measures: " + "; ".join(f"{k} ({d})" for k, d in v["measures"].items()))
    return "\n".join(parts)


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("breakdown",
         "Count records and compute measures, optionally split by one or two fields, for any object: cases (the default), "
         "transactions, alerts, customers, filings, holds, disputes, risk assessments, KYC triggers, agent runs, agent actions, "
         "detection changes, lookbacks or pipeline runs. Use this for any 'how many', 'how much', 'average', 'trend', 'compare' or "
         "'which ... has the most' question. Each row carries up to five example record ids you can cite. The fields and measures "
         "of each object are listed below; a field or measure from another object is refused.\n{catalog}",
         {"type": "object", "additionalProperties": False, "properties": {
             "object": OBJECT,
             "group_by": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": _all("group")}},
             "measures": {"type": "array", "items": {"type": "string", "enum": _all("measure")}, "description": "Defaults to the object's first measure"},
             "filters": ANY_FILTERS,
             "sort_by": {"type": "string", "description": "One of the measures or group_by fields in this call"},
             "descending": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": semantic.MAX_ROWS}}},
         _breakdown),
    Tool("list_records",
         "List individual records of any object that match, sorted: the largest transactions, the overdue filings, a customer's "
         "holds, the alerts behind a number. For cases, list_cases gives more case detail. Same objects, fields and filters as "
         "breakdown. `fields` picks the columns (the id, case and customer are always included).",
         {"type": "object", "additionalProperties": False, "required": ["object"], "properties": {
             "object": OBJECT, "filters": ANY_FILTERS, "sort_by": {"type": "string", "enum": _all("field")},
             "descending": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": landscape.MAX_LIST},
             "fields": {"type": "array", "items": {"type": "string", "enum": _all("field")}, "maxItems": 15}}},
         _list_records),
    Tool("record",
         "Every field of one record of any object, by its id: a transaction, alert, filing, hold, dispute, risk assessment and so on. "
         "For a case use case_detail, for a customer's whole picture use customer.",
         {"type": "object", "additionalProperties": False, "required": ["object", "id"], "properties": {
             "object": OBJECT, "id": {"type": "string"}}},
         _record),
    Tool("calculate",
         "Work out a figure from figures you already have, for example a difference, a ratio or a percentage change: "
         "'(1250 - 980) / 980 * 100'. Numbers, + - * / ( ) and round(x, n) only. Use it rather than doing arithmetic yourself, "
         "so the figure in your answer can be checked.",
         {"type": "object", "additionalProperties": False, "required": ["expression"], "properties": {"expression": {"type": "string", "maxLength": 300}}},
         _calculate),
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
    Tool("policy_search",
         "Search the knowledge library: typologies (what a pattern looks like, its indicators and mitigating factors), "
         "the client's policies and procedures, and regulatory requirements. Use it for 'what does policy say', 'what are "
         "the signs of', 'what should I do when' and 'when must we file' questions, and to back a recommendation with the rule "
         "behind it. Each passage has an id like kb:structuring#indicators; cite that id when you rely on the passage. "
         "The library answers what the rules are; the other tools answer what is in the records.",
         {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {
             "query": {"type": "string", "maxLength": 300},
             "kind": {"type": "string", "enum": list(KINDS)},
             "scenario": {"type": "string", "description": "Only passages about this alert scenario, for example structuring"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 10}}},
         _policy_search),
    Tool("team_workload",
         "Open work per person and per queue, with what is past its stage clock. Only for roles that hold the team view.",
         {"type": "object", "additionalProperties": False, "properties": {}},
         _team, permission=TEAM),
]}


def available(asker: Persona) -> list[Tool]:
    """The tools this person's role may use. A tool they may not use is never offered to the model."""
    return [t for t in TOOLS.values() if t.permission is None or asker.can(t.permission)]


def specs(asker: Persona) -> list[tuple[str, str, dict]]:
    """(name, description, schema) for each tool this person may use, with the vocabulary of the objects they may read."""
    text = catalog_text(asker)
    return [(t.name, t.description.replace("{catalog}", text), t.schema) for t in available(asker)]


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
