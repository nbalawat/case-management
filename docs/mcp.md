# CaseForge as an MCP server

Ask's governed tools are also an [MCP](https://modelcontextprotocol.io) server, so any MCP client (a desktop
assistant, an IDE, another team's agent) can read a client's case landscape. It is read only.

Every call runs as one person at one client, with the same rules as Ask in the app:
- the session is bound to the client;
- the person's case visibility applies;
- objects their role may not read are refused with the reason;
- `ask` is recorded in the client's question log.

## Tools

| Tool | What it does |
| --- | --- |
| `catalog` | The objects this person may read, with their fields and measures |
| `breakdown` | Counts, totals, averages and rates over one object, split by up to two fields, with example ids |
| `list_records` | Up to 25 matching records of one object |
| `record` | One record by id, if the person may see it |
| `calculate` | Arithmetic for derived figures (numbers, `+ - * /`, brackets, `round(x, n)`) |
| `policy_search` | Passages from the knowledge library (typologies, the client's policies and procedures), each with an id to cite |
| `ask` | A whole question answered in plain language, with sources and a check of every figure |

## Over HTTP

The API serves the MCP streamable HTTP transport at `/mcp`. It is stateless: each request stands alone. Callers sign
in the same way as for the rest of the API:
- in OIDC mode, a bearer token from the client's identity provider;
- in the demo, the `X-Tenant` and `X-Persona` headers.

An unknown caller is turned away with 401 or 403 before any MCP traffic. Each tool call then resolves the caller
again from its own request.

Only local host names are accepted by default, as protection against DNS rebinding. A deployment behind its own
name sets `MCP_ALLOWED_HOSTS`, for example `caseforge.example.com,caseforge.example.com:*`.

An MCP client configuration for the demo:

```json
{
  "mcpServers": {
    "caseforge": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "X-Tenant": "meridian", "X-Persona": "maya" }
    }
  }
}
```

## Over stdio

For a local client pointed at a command:

```
cd backend && uv run python -m app.mcp --client meridian --person maya
```

This reads the database the app is configured with, as that person. Anyone who can run the command can already read
that database, so the person comes from the command line. Over HTTP the person always comes from the sign-in.
