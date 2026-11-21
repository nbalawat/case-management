"""python -m app.mcp --client meridian --person maya

Runs the MCP server over stdio as one person at one client, for a local MCP client (an IDE or a desktop assistant)
pointed at this command. It reads the database the app is configured with. Whoever can run this command can already
read that database, so the person is taken from the command line; over HTTP the person comes from the sign-in.
"""
from __future__ import annotations

import argparse
import sys

from ..db import session_scope
from ..tenants.onboarding import hydrate
from ..tenants.registry import UnknownTenant, runtime
from .server import build, fixed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.mcp", description="CaseForge's Ask tools as an MCP server over stdio")
    ap.add_argument("--client", required=True, help="client id, for example meridian")
    ap.add_argument("--person", required=True, help="person id at that client, for example maya")
    a = ap.parse_args(argv)
    with session_scope(platform=True) as p:
        hydrate(p)
    try:
        rt = runtime(a.client)
    except UnknownTenant:
        print(f"No client '{a.client}'", file=sys.stderr)
        return 2
    if a.person not in rt.policy.people:
        print(f"'{a.person}' is not a user of {rt.name}", file=sys.stderr)
        return 2
    build(fixed(rt.id, rt.policy.people[a.person])).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
