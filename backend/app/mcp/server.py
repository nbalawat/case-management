"""The MCP server: Ask's governed tools for any MCP client (an assistant, an IDE, another agent).

Every tool call runs as one person at one client, exactly as Ask's own tools do: the session is bound to that client,
the person's case visibility applies, objects their role may not read are refused, and results carry the same record
ids Ask cites. The server never decides who is calling. A `Principal` resolver does: over HTTP it reads the
request's sign-in (or the demo headers), and over stdio it is fixed when the server starts.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from ..ask import service, tools
from ..ask.landscape import catalog
from ..ask.semantic import FIELDS, MEASURES
from ..db import session_scope
from ..domain.personas import Persona
from ..visibility import bind_viewer

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
INSTRUCTIONS = """CaseForge financial-crime case management, read only, as one person at one client.
Start with `catalog` to see which records you may read and their fields. Use `breakdown` for counts, totals and
rates (split by up to two fields), `list_records` for the records themselves, `record` for one record by id, and
`calculate` for any figure you derive. `ask` answers a whole question in plain language with sources. Chain lookups
by filtering one object's id fields with the ids another returned (op "in"). Every result is limited to what this
person may see; say so when it matters."""


class Unauthorized(Exception):
    """The caller could not be resolved to a person at a client. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    persona: Persona


Resolver = Callable[[Context], Principal]


def fixed(tenant_id: str, persona: Persona) -> Resolver:
    """One person for the life of the server: the stdio transport, started by someone who already signed in."""
    principal = Principal(tenant_id, persona)
    return lambda _ctx: principal


def _clean(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if v is not None}


def build(resolve: Resolver) -> MCPServer:
    """An MCP server whose every tool runs as whoever `resolve` says is calling."""
    mcp = MCPServer("caseforge", title="CaseForge", instructions=INSTRUCTIONS, version="1")

    def run(ctx: Context, tool: str, args: dict[str, Any]) -> dict:
        who = _principal(resolve, ctx)
        with session_scope(who.tenant_id) as s:
            result = tools.run(tools.ToolContext(bind_viewer(s, who.persona), who.persona), tool, _clean(args))
        if result.error:
            raise ToolError(result.summary)
        return result.data

    @mcp.tool(name="catalog", title="What can I read?", annotations=READ_ONLY)
    def catalog_of(ctx: Context) -> dict:
        """The records this person may read: for each object its description, fields (to filter, split and sort by)
        and measures. Cases are always readable; other objects depend on the person's role."""
        who = _principal(resolve, ctx)
        out = {"client": who.tenant_id, "person": who.persona.name, "role": who.persona.title,
               "objects": {"cases": {"about": "Cases being worked: investigations, sanctions hits, fraud claims, KYC reviews.",
                                     "fields": {f.id: f.help or f.label for f in FIELDS.values()}, "measures": dict(MEASURES)}}}
        for o in catalog.available(who.persona):
            v = o.vocabulary()
            out["objects"][o.id] = {"about": o.description, "fields": v["fields"], "measures": v["measures"]}
        return out

    @mcp.tool(title="Count and compare", annotations=READ_ONLY)
    def breakdown(ctx: Context, object: str = "cases", group_by: list[str] | None = None, measures: list[str] | None = None,
                  filters: list[dict] | None = None, sort_by: str | None = None, descending: bool | None = None,
                  limit: int | None = None) -> dict:
        """Measures over the matching records of one object, optionally split by up to two fields, with example ids
        per group. Filters are {field, op, value}; op is one of = != > >= < <= in contains."""
        return run(ctx, "breakdown", {"object": object, "group_by": group_by, "measures": measures, "filters": filters,
                                      "sort_by": sort_by, "descending": descending, "limit": limit})

    @mcp.tool(title="List records", annotations=READ_ONLY)
    def list_records(ctx: Context, object: str = "cases", filters: list[dict] | None = None, sort_by: str | None = None,
                     descending: bool | None = None, limit: int | None = None, fields: list[str] | None = None) -> dict:
        """The matching records of one object (up to 25), sorted, with the fields asked for."""
        return run(ctx, "list_records", {"object": object, "filters": filters, "sort_by": sort_by, "descending": descending,
                                         "limit": limit, "fields": fields})

    @mcp.tool(title="Read one record", annotations=READ_ONLY)
    def record(ctx: Context, object: str, id: str) -> dict:
        """One record by id with every field, if this person may see it. For cases, the case with its history."""
        return run(ctx, "record", {"object": object, "id": id})

    @mcp.tool(title="Calculate", annotations=READ_ONLY)
    def calculate(ctx: Context, expression: str) -> dict:
        """Arithmetic on figures from other results: numbers, + - * / ( ) and round(x, n). Use it for every derived
        figure, so the figure can be checked."""
        return run(ctx, "calculate", {"expression": expression})

    @mcp.tool(title="Search policy and typologies", annotations=READ_ONLY)
    def policy_search(ctx: Context, query: str, kind: str | None = None, scenario: str | None = None, limit: int | None = None) -> dict:
        """Passages from the knowledge library: typologies, this client's policies and procedures, and regulatory
        requirements. kind is one of typology, policy, procedure, regulation. Each passage carries an id
        (kb:<document>#<section>) to cite. A client's own documents are only ever searched for that client."""
        return run(ctx, "policy_search", {"query": query, "kind": kind, "scenario": scenario, "limit": limit})

    @mcp.tool(title="Ask a question", annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    def ask(ctx: Context, question: str) -> dict:
        """Answer a whole question in plain language, through the same governed lookups, with the records it rests on
        and a check of every figure. Recorded in the client's question log like any question asked in the app."""
        who = _principal(resolve, ctx)
        with session_scope(who.tenant_id) as s:
            bind_viewer(s, who.persona)
            try:
                out = service.ask(s, who.persona, question)
            except service.AskError as e:
                raise ToolError(str(e)) from None
        return {k: out.get(k) for k in ("answer", "status", "sources", "cited", "unverified", "figures", "provider")} \
            | {"lookups": [{"tool": st["tool"], "input": st["input"], "ok": st["ok"], "summary": st["summary"]} for st in out.get("steps", [])]}

    return mcp


def _principal(resolve: Resolver, ctx: Context) -> Principal:
    try:
        return resolve(ctx)
    except Unauthorized as e:
        raise ToolError(str(e)) from None
