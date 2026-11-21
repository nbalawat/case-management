"""The case list at volume: one page at a time, sorted and filtered in the database, with facet counts and export."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..cases.case_types import load_case_types
from ..detection.scenarios import describe
from ..cases.listing import CaseQuery, export_csv, facets, page, parse_sort
from ..domain.personas import Persona
from ..tenants.registry import TenantRuntime
from .deps import case_row, get_session, persona, tenant

router = APIRouter(prefix="/api")


def case_query(status: str = "open", case_type: list[str] = Query(default=[]), priority: list[str] = Query(default=[]),
               queue: list[str] = Query(default=[]), state: list[str] = Query(default=[]), owner: str | None = None,
               q: str = "", sort: str | None = None, page: int = 1, size: int = 50,
               due: str | None = None, scenario: list[str] = Query(default=[]), opened: str = "", closed: str = "",
               p: Persona = Depends(persona)) -> CaseQuery:
    try:
        return CaseQuery(status=status, case_types=tuple(case_type), priorities=tuple(priority), queues=tuple(queue),
                         states=tuple(state), owner=p.id if owner == "me" else owner or None, text=q,
                         sort=parse_sort(sort), page=page, size=size, due=due or None, scenarios=tuple(scenario),
                         opened=opened, closed_in=closed)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _labels(rt: TenantRuntime) -> dict:
    types = load_case_types(rt.id)
    states: dict[str, str] = {}
    for ct in types.values():
        for sid, st in ct["states"].items():
            states.setdefault(sid, st["label"])
    return {"case_type": {k: v["title"] for k, v in types.items()}, "queue": dict(rt.policy.queues), "state": states}


@router.get("/case-list")
def case_list(cq: CaseQuery = Depends(case_query), s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    rows, total, alerts = page(s, cq)
    return {"rows": [case_row(c) | {"alerts": alerts.get(c.id, 0)} for c in rows], "total": total, "page": cq.page, "size": cq.size,
            "facets": facets(s, cq), "labels": _labels(rt) | {"scenario": {k: describe(k)["title"] for k in cq.scenarios}}}


@router.get("/case-list/export.csv")
def case_list_csv(cq: CaseQuery = Depends(case_query), s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    queues = rt.policy.queues
    text, _ = export_csv(s, cq, lambda c: case_row(c) | {"queue_label": queues.get(c.queue or "", "")})
    return PlainTextResponse(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="cases-{rt.id}-{date.today()}.csv"'})
