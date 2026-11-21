"""The payment network on Apache AGE, the graph extension for Postgres.

One graph per client, named for it, loaded whole after each entity resolution inside one transaction, so a reader
sees the old network or the new one and never half of each. The graph holds ids and nothing else: no names, no
amounts. It can live in the application's own Postgres or in another.

GRAPH_URL=age://user:password@host:5432/database
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager

import psycopg
from sqlalchemy.orm import Session

from ..config import settings
from ..tenancy import tenant_of
from .graphstore import GraphError, links

TENANT = re.compile(r"[a-z][a-z0-9-]{2,30}")
BATCH = 1000
# AGE wants the graph name as a constant in the statement, not a parameter. `{g}` is only ever filled from
# graph_name(), which builds it from a checked client id. Every value travels as a parameter.
_ROUTE = "SELECT * FROM cypher('{g}', $$ MATCH p = (a:Entity {{id: $a}})-[:LINK*{n}..{n}]-(b:Entity {{id: $b}}) RETURN p LIMIT 1 $$, %s) AS (p agtype)"
_NODES = "SELECT * FROM cypher('{g}', $$ UNWIND $rows AS r CREATE (:Entity {{id: r}}) $$, %s) AS (x agtype)"
_LINKS = "SELECT * FROM cypher('{g}', $$ UNWIND $rows AS r MATCH (a:Entity {{id: r[0]}}), (b:Entity {{id: r[1]}}) CREATE (a)-[:LINK]->(b) $$, %s) AS (x agtype)"


def graph_name(tenant_id: str) -> str:
    """A client's graph. Client ids hold no underscores, so two clients can never map to one name."""
    if not TENANT.fullmatch(tenant_id or ""):
        raise GraphError("That is not a client id.")
    return "cf_" + tenant_id.replace("-", "_")


def _ids(path: str) -> list[str]:
    """The entity ids along a path as AGE prints one: vertices and edges in turn, each tagged with its type."""
    items = json.loads(re.sub(r"::(vertex|edge|path)", "", path))
    return [x["properties"]["id"] for x in items[::2]]


class AgeGraph:
    def __init__(self, dsn: str, timeout_ms: int = 3000) -> None:
        self.dsn, self.timeout_ms = dsn, timeout_ms
        self._ready = False

    @contextmanager
    def _cursor(self):
        try:
            with psycopg.connect(self.dsn, connect_timeout=5) as conn, conn.cursor() as cur:
                if not self._ready:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS age")
                    conn.commit()
                    self._ready = True
                cur.execute("LOAD 'age'")
                cur.execute('SET search_path = ag_catalog, "$user", public')
                yield cur
        except psycopg.Error as e:
            raise GraphError(f"The graph engine refused: {type(e).__name__}") from e

    @staticmethod
    def _exists(cur, name: str) -> bool:
        cur.execute("SELECT 1 FROM ag_catalog.ag_graph WHERE name = %s", (name,))
        return cur.fetchone() is not None

    def rebuild(self, s: Session) -> dict:
        """Replace this client's graph with what resolution has just built. All in one transaction."""
        name = graph_name(tenant_of(s))
        nodes, pairs = links(s)
        with self._cursor() as cur:
            if self._exists(cur, name):
                cur.execute("SELECT drop_graph(%s, true)", (name,))
            cur.execute("SELECT create_graph(%s)", (name,))
            cur.execute("SELECT create_vlabel(%s, 'Entity')", (name,))
            cur.execute("SELECT create_elabel(%s, 'LINK')", (name,))
            cur.execute(f'CREATE INDEX ON {name}."Entity" USING gin (properties)')  # name is built from a checked client id
            ordered = sorted(nodes)
            for i in range(0, len(ordered), BATCH):
                cur.execute(_NODES.format(g=name), (json.dumps({"rows": ordered[i:i + BATCH]}),), prepare=True)
            for i in range(0, len(pairs), BATCH):
                cur.execute(_LINKS.format(g=name), (json.dumps({"rows": [list(p) for p in pairs[i:i + BATCH]]}),), prepare=True)
        return {"graph": name, "entities": len(nodes), "links": len(pairs)}

    def route(self, tenant_id: str, start: str, goal: str, max_hops: int) -> list[str] | None:
        """A shortest chain of entity ids from start to goal, or None when there is none within reach.

        Tried one length at a time, shortest first, so the first found is a shortest. A search that runs past the
        time limit raises, and the caller answers from the database instead.
        """
        name = graph_name(tenant_id)
        with self._cursor() as cur:
            if not self._exists(cur, name):
                raise GraphError("This client's graph has not been built yet.")
            cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
            args = json.dumps({"a": start, "b": goal})
            for n in range(1, max_hops + 1):
                cur.execute(_ROUTE.format(g=name, n=int(n)), (args,), prepare=True)
                row = cur.fetchone()
                if row:
                    return _ids(row[0])
        return None

    def size(self, tenant_id: str) -> tuple[int, int] | None:
        """Entities and links held for a client, or None when its graph has not been built."""
        name = graph_name(tenant_id)
        with self._cursor() as cur:
            if not self._exists(cur, name):
                return None
            cur.execute(f'SELECT (SELECT count(*) FROM {name}."Entity"), (SELECT count(*) FROM {name}."LINK")')
            return tuple(cur.fetchone())

    def drop(self, tenant_id: str) -> None:
        name = graph_name(tenant_id)
        with self._cursor() as cur:
            if self._exists(cur, name):
                cur.execute("SELECT drop_graph(%s, true)", (name,))


def connect(url: str | None = None) -> AgeGraph:
    url = settings.graph_url if url is None else url
    if not url.startswith("age://"):
        raise GraphError("GRAPH_URL must look like age://user:password@host:5432/database")
    return AgeGraph("postgresql://" + url.removeprefix("age://"))
