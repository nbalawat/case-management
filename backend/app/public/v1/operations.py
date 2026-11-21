"""Filings, screening, case types, Ask, data pipelines and metrics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...api.workflows import describe as describe_case_type
from ...cases.case_types import load_case_types
from ...domain.models import DataSource, PipelineRun
from ..conventions import ApiError
from ..guard import need, session
from ..principal import Principal
from . import schemas as sc

filings = APIRouter(prefix="/filings", tags=["Regulatory filings"])
screening = APIRouter(prefix="/screening", tags=["Screening"])
workflows = APIRouter(prefix="/case-types", tags=["Case types"])
ask = APIRouter(prefix="/ask", tags=["Ask anything"])
data = APIRouter(prefix="/data", tags=["Data pipelines"])
metrics = APIRouter(prefix="/metrics", tags=["Metrics"])


# --- filings ----------------------------------------------------------------------------------------------------------

def _filing_reader(p: Principal = Depends(need("filings:read"))) -> Principal:
    if not (p.persona.can("submit_filings") or p.persona.can("view_team")):
        raise ApiError(403, f"{p.persona.title} cannot see regulatory reports", "forbidden")
    return p


def _filing(fn, *a, **kw):
    from ...filing import service

    try:
        return service.row(fn(*a, **kw))
    except service.FilingError as e:
        raise ApiError(409, str(e), "filing_refused") from None


@filings.get("", summary="The filing register")
def list_filings(status: str | None = None, kind: str | None = None, p: Principal = Depends(_filing_reader), s: Session = Depends(session)) -> dict:
    """Reports on cases the caller cannot see are left out."""
    from ...filing import service

    rows = service.overview(s, p.tenant_id)["filings"]
    rows = [r for r in rows if (status is None or r["status"] == status) and (kind is None or r["kind"] == kind)]
    return {"data": rows}


@filings.get("/{filing_id}", summary="Read a filing, with its history and integrity check")
def read_filing(filing_id: str, p: Principal = Depends(_filing_reader), s: Session = Depends(session)) -> dict:
    from ...filing import service

    try:
        d = service.detail(s, filing_id)
    except service.FilingError:
        raise ApiError(404, "No filing with that id", "filing_not_found") from None
    return {k: v for k, v in d.items() if k != "document"}


@filings.get("/{filing_id}/document", summary="The filed document, exactly as sent (FinCEN BSA XML, goAML, ...)")
def filing_document(filing_id: str, p: Principal = Depends(_filing_reader), s: Session = Depends(session)):
    from ...filing import service
    from ...filing.formats import FORMATS

    try:
        d = service.detail(s, filing_id)
    except service.FilingError:
        raise ApiError(404, "No filing with that id", "filing_not_found") from None
    ext = FORMATS[d["format"]].extension if d["format"] in FORMATS else "txt"
    return Response(d["document"], media_type="application/xml" if ext == "xml" else "text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{d["id"]}.{ext}"', "Digest": f"sha-256={d['sha256']}"})


@filings.post("/{filing_id}/submission", summary="Send a prepared filing (people only)")
def submit_filing(filing_id: str, p: Principal = Depends(need("filings:write")), s: Session = Depends(session)) -> dict:
    from ...filing import service

    return _filing(service.submit, s, filing_id, p.persona)


@filings.post("/{filing_id}/acknowledgement", summary="Record the regulator's acceptance (people only)")
def acknowledge_filing(filing_id: str, body: dict, p: Principal = Depends(need("filings:write")), s: Session = Depends(session)) -> dict:
    from ...filing import service

    ref = str(body.get("reference") or "").strip()
    if not ref or len(ref) > 120:
        raise ApiError(422, "reference is required (the regulator's reference, at most 120 characters)", "invalid_request")
    return _filing(service.acknowledge, s, filing_id, ref, p.persona)


@filings.post("/{filing_id}/rejection", summary="Record the regulator's rejection (people only)")
def reject_filing(filing_id: str, body: dict, p: Principal = Depends(need("filings:write")), s: Session = Depends(session)) -> dict:
    from ...filing import service

    reason = str(body.get("reason") or "").strip()
    if not reason or len(reason) > 2000:
        raise ApiError(422, "reason is required (at most 2000 characters)", "invalid_request")
    return _filing(service.reject, s, filing_id, reason, p.persona)


# --- screening --------------------------------------------------------------------------------------------------------

@screening.get("/lists", summary="The screening lists in force")
def screening_lists(p: Principal = Depends(need("screening:read")), s: Session = Depends(session)) -> dict:
    from ...sanctions import lists

    v = lists.view(s, p.persona, limit=0)
    return {"lists": [{k: x[k] for k in ("code", "name", "kind", "source", "version", "active", "updated_at", "entries")} for x in v["lists"]]}


@screening.post("/checks", summary="Screen names against the client's lists")
def screen_names(body: sc.ScreenIn, p: Principal = Depends(need("screening:read")), s: Session = Depends(session)) -> dict:
    """The same matching, threshold and approved exemptions the platform's own screening uses. Nothing is stored and no case
    is opened: send an alert for a hit you want worked."""
    from ...sanctions import lists
    from ...sanctions.screening import best_match
    from ...domain.models import WatchlistEntry

    w = lists.watch(s)
    entries = {e.id: e for e in s.scalars(select(WatchlistEntry).where(WatchlistEntry.id.in_({x for _, x in w.names})))} if w.names else {}
    out = []
    for name in body.names:
        name = name.strip()[:200]
        hit = best_match(name, w.names, w.threshold or 0.86, w.exempt) if name else None
        e = entries.get(hit[1]) if hit else None
        out.append({"name": name, "hit": hit is not None, "score": round(hit[2], 3) if hit else None, "matched_name": hit[0] if hit else None,
                    "entry": None if e is None else {"id": e.id, "ref": e.ref, "name": e.name, "kind": e.kind, "programs": e.programs,
                                                     "countries": e.countries}})
    return {"threshold": w.threshold or 0.86, "results": out}


# --- case types -------------------------------------------------------------------------------------------------------

@workflows.get("", summary="The case types this client uses")
def list_case_types(p: Principal = Depends(need("workflows:read"))) -> dict:
    return {"data": [describe_case_type(ct, p.runtime.policy) for ct in load_case_types(p.tenant_id).values()]}


@workflows.get("/{case_type}", summary="One case type: states, transitions, tasks, clocks and who may take each step")
def read_case_type(case_type: str, p: Principal = Depends(need("workflows:read"))) -> dict:
    ct = load_case_types(p.tenant_id).get(case_type)
    if ct is None:
        raise ApiError(404, "This client does not use that case type", "case_type_not_found")
    return describe_case_type(ct, p.runtime.policy)


# --- ask --------------------------------------------------------------------------------------------------------------

@ask.post("", summary="Ask a question in plain language")
def post_ask(body: sc.AskIn, p: Principal = Depends(need("ask")), s: Session = Depends(session)) -> dict:
    """Answered only from what the caller can see, with the sources cited. Pass conversation_id back to follow up."""
    from ...ask import service

    try:
        return service.ask(s, p.persona, body.question, body.conversation_id)
    except service.AskError as e:
        raise ApiError(422, str(e), "ask_refused") from None


# --- data pipelines ---------------------------------------------------------------------------------------------------

def _run_out(r: PipelineRun) -> dict:
    return {"id": r.id, "source_id": r.source_id, "status": r.status, "started": r.started, "finished": r.finished,
            "stats": r.stats, "trigger": r.trigger, "started_by": r.started_by, "error": r.error or None}


@data.get("/sources", summary="Data sources: object-storage locations the platform loads from")
def data_sources(p: Principal = Depends(need("data:read")), s: Session = Depends(session)) -> dict:
    return {"data": [{"id": x.id, "name": x.name, "dataset": x.dataset, "enabled": x.enabled, "schedule_minutes": x.schedule_minutes,
                      "last_run_at": x.last_run_at} for x in s.scalars(select(DataSource).order_by(DataSource.created))]}


@data.post("/sources/{source_id}/runs", status_code=202, summary="Run a source's pipeline now")
def run_source(source_id: str, p: Principal = Depends(need("data:write")), s: Session = Depends(session)) -> dict:
    from ...ingest import pipeline

    src = s.get(DataSource, source_id)
    if src is None:
        raise ApiError(404, "No data source with that id", "source_not_found")
    return _run_out(pipeline.run_source(s, src, p.caller))


@data.get("/runs", summary="Recent pipeline runs")
def data_runs(limit: int = 50, p: Principal = Depends(need("data:read")), s: Session = Depends(session)) -> dict:
    from ..conventions import limit_of

    return {"data": [_run_out(r) for r in s.scalars(select(PipelineRun).order_by(PipelineRun.started.desc(), PipelineRun.id.desc()).limit(limit_of(limit)))]}


@data.get("/runs/{run_id}", summary="One pipeline run")
def data_run(run_id: str, p: Principal = Depends(need("data:read")), s: Session = Depends(session)) -> dict:
    r = s.get(PipelineRun, run_id)
    if r is None:
        raise ApiError(404, "No run with that id", "run_not_found")
    return _run_out(r)


# --- metrics ----------------------------------------------------------------------------------------------------------

@metrics.get("/dashboard", summary="Operational metrics: backlog, ageing, throughput, outcomes")
def dashboard(p: Principal = Depends(need("metrics:read")), s: Session = Depends(session)) -> dict:
    """Computed over the cases the caller can see."""
    from ...reporting.metrics import dashboard as compute

    return compute(s)


