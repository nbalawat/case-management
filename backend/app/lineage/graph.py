"""Lineage read back for the UI: datasets and jobs as nodes, reads and writes as edges, from a client's kept events."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import LineageEvent, now


def _key(d: dict) -> str:
    return f"{d['namespace']}|{d['name']}"


def graph(s: Session, days: int = 30) -> dict:
    """Every job seen in the window and every dataset it touched. Rows add up over the window; layers run left to
    right from datasets nothing writes (sources) to datasets nothing reads (ends)."""
    since = now() - timedelta(days=days)
    datasets: dict[str, dict] = {}
    jobs: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    for ev in s.scalars(select(LineageEvent).where(LineageEvent.at >= since).order_by(LineageEvent.at)):
        j = jobs.setdefault(ev.job, {"id": f"job:{ev.job}", "type": "job", "name": ev.job, "runs": 0, "failed": 0, "last_run": None, "last_state": None})
        j["runs"] += 1
        j["failed"] += ev.state == "FAIL"
        j["last_run"], j["last_state"] = ev.at.isoformat(), ev.state
        for d in ev.inputs:
            ds = datasets.setdefault(_key(d), {"id": f"ds:{_key(d)}", "type": "dataset", "namespace": d["namespace"], "name": d["name"],
                                               "kind": d.get("kind", "table"), "rows_written": 0})
            edges.setdefault((ds["id"], j["id"]), {"from": ds["id"], "to": j["id"], "rows": None, "runs": 0})["runs"] += 1
        for d in ev.outputs:
            ds = datasets.setdefault(_key(d), {"id": f"ds:{_key(d)}", "type": "dataset", "namespace": d["namespace"], "name": d["name"],
                                               "kind": d.get("kind", "table"), "rows_written": 0})
            ds["rows_written"] += d.get("rows") or 0
            e = edges.setdefault((j["id"], ds["id"]), {"from": j["id"], "to": ds["id"], "rows": 0, "runs": 0})
            e["rows"] += d.get("rows") or 0
            e["runs"] += 1
    nodes = {n["id"]: n for n in [*datasets.values(), *jobs.values()]}
    _layer(nodes, list(edges.values()))
    return {"days": days, "nodes": list(nodes.values()), "edges": list(edges.values())}


def _layer(nodes: dict[str, dict], edges: list[dict]) -> None:
    """Longest path from a source, so each node sits right of everything that feeds it. Cycles (a job that reads
    and writes the same table) are cut by visiting each node once per path."""
    into: dict[str, list[str]] = {n: [] for n in nodes}
    for e in edges:
        into[e["to"]].append(e["from"])
    memo: dict[str, int] = {}

    def depth(n: str, path: frozenset[str]) -> int:
        if n in memo:
            return memo[n]
        parents = [p for p in into[n] if p not in path]
        d = 0 if not parents else 1 + max(depth(p, path | {n}) for p in parents)
        memo[n] = d
        return d
    for n in nodes:
        nodes[n]["layer"] = depth(n, frozenset())


def events(s: Session, job: str | None = None, limit: int = 100) -> list[dict]:
    q = select(LineageEvent).order_by(LineageEvent.at.desc(), LineageEvent.id.desc()).limit(min(limit, 500))
    if job:
        q = q.where(LineageEvent.job == job)
    return [{"id": e.id, "job": e.job, "run_id": e.run_id, "state": e.state, "at": e.at.isoformat(), "inputs": e.inputs, "outputs": e.outputs}
            for e in s.scalars(q)]


def event(s: Session, event_id: int) -> dict | None:
    e = s.get(LineageEvent, event_id)
    return e.event if e else None
