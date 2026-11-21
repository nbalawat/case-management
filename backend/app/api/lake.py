"""SQL over a client's own history on the lake: what tables there are, and read-only queries, optionally as of a moment."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..lake.base import LakeError
from ..lake.query import MAX_ROWS, QueryError, engine
from ..tenants.registry import TenantRuntime
from .deps import require, tenant

router = APIRouter(prefix="/api/lake")

EXAMPLES = [
    {"title": "Alerts by scenario and month", "sql": "SELECT scenario, date_trunc('month', ts) AS month, count(*) AS alerts, round(avg(score), 2) AS avg_score\n"
     "FROM alerts GROUP BY ALL ORDER BY month DESC, alerts DESC"},
    {"title": "Customers moving the most cash", "sql": "SELECT party_id, count(*) AS deposits, round(sum(amount)) AS total\nFROM transactions\n"
     "WHERE channel = 'cash' AND direction = 'in'\nGROUP BY party_id ORDER BY total DESC LIMIT 20"},
    {"title": "Case activity by type of event", "sql": "SELECT type, actor_kind, count(*) AS events, min(ts) AS first, max(ts) AS last\n"
     "FROM case_events GROUP BY ALL ORDER BY events DESC"},
    {"title": "Daily volume and value", "sql": "SELECT CAST(ts AS DATE) AS day, count(*) AS payments, round(sum(amount)) AS value\n"
     "FROM transactions GROUP BY ALL ORDER BY 1 DESC LIMIT 60"},
]


def _engine():
    try:
        eng = engine()
    except LakeError as e:
        raise HTTPException(503, str(e)) from e
    if eng is None:
        raise HTTPException(409, "This deployment has no lake. Set LAKE_CATALOG (make full does) to keep history in Iceberg tables.")
    return eng


@router.get("/tables")
def tables(rt: TenantRuntime = Depends(tenant), _=Depends(require("view_team"))):
    eng = _engine()
    return {"engine": eng.name, "max_rows": MAX_ROWS, "tables": eng.schema(rt.id), "examples": EXAMPLES}


class QueryIn(BaseModel):
    sql: str
    as_of: datetime | None = None


@router.post("/query")
def query(body: QueryIn, rt: TenantRuntime = Depends(tenant), _=Depends(require("view_team"))):
    eng = _engine()
    try:
        return {"ok": True} | eng.query(rt.id, body.sql, as_of=body.as_of).as_dict()
    except QueryError as e:
        return {"ok": False, "error": str(e)}
