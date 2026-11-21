"""Data pipelines: sources in a client's object storage, their runs, and where each loaded record came from."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import DataSource, IngestedObject, PipelineRun, Transaction
from ..domain.personas import Persona, policy_of
from ..ingest import locations, pipeline
from ..ingest.datasets import DATASETS
from ..storage import StorageError
from ..tenants.registry import TenantRuntime
from .deps import get_session, require, tenant

router = APIRouter(prefix="/api/data")
LOAD = "load_data"


class SourceIn(BaseModel):
    name: str = ""
    dataset: str = ""
    root: str = ""
    prefix: str = ""
    pattern: str = "**"
    format: str = "auto"
    options: dict = {}
    mapping: dict[str, str | None] = {}
    columns: list[str] = []
    rules: dict = {}
    schedule_minutes: int | None = None
    enabled: bool = True


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except pipeline.PipelineError as e:
        raise HTTPException(422, str(e)) from None


def _name(tenant_id: str, who: str) -> str:
    people = policy_of(tenant_id).people
    return people[who].name if who in people else who


def _run(r: PipelineRun, tenant_id: str, source_name: str | None = None) -> dict:
    return {"id": r.id, "source_id": r.source_id, "source": source_name, "batch_id": r.batch_id, "trigger": r.trigger,
            "started_by": _name(tenant_id, r.started_by), "started": r.started.isoformat(),
            "finished": r.finished.isoformat() if r.finished else None, "status": r.status, "mapping_version": r.mapping_version,
            "steps": r.steps, "stats": r.stats, "error": r.error}


def _source(s: Session, src: DataSource, rt: TenantRuntime, waiting: bool = True) -> dict:
    out = {"id": src.id, "name": src.name, "dataset": src.dataset, "dataset_label": DATASETS[src.dataset].label, "root": src.root,
           "prefix": src.prefix, "pattern": src.pattern, "format": src.format, "options": src.options, "mapping": src.mapping,
           "mapping_version": src.mapping_version, "columns": src.columns, "rules": pipeline.clean_rules(src.rules),
           "schedule_minutes": src.schedule_minutes, "enabled": src.enabled, "created": src.created.isoformat(),
           "created_by": _name(rt.id, src.created_by), "last_run_at": src.last_run_at.isoformat() if src.last_run_at else None,
           "depends_on": list(DATASETS[src.dataset].depends_on)} | pipeline.health(src)
    try:
        root = locations.root_of(rt.id, src.root)
        out["location"] = root["url"] + src.prefix
        out["provider"] = root.get("provider", "")
        if waiting:
            objects, fresh = pipeline.discover(s, src, locations.store_for(rt.id, src.root))
            out |= {"objects": len(objects), "waiting": len(fresh), "reachable": True}
    except StorageError as e:
        out |= {"objects": None, "waiting": None, "reachable": False, "problem": str(e)}
    return out


@router.get("")
def overview(s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    sources = list(s.scalars(select(DataSource).order_by(DataSource.created)))
    names = {x.id: x.name for x in sources}
    runs = list(s.scalars(select(PipelineRun).order_by(PipelineRun.started.desc(), PipelineRun.id.desc()).limit(40)))
    order = {d: i for i, d in enumerate(DATASETS)}
    return {"locations": [{k: r.get(k) for k in ("id", "label", "url", "provider")} for r in locations.roots(rt.id)],
            "datasets": [{"id": d.id, "label": d.label, "depends_on": list(d.depends_on),
                          "fields": [{"id": k, "required": m["required"], "desc": m["desc"]} for k, m in d.fields.items()]} for d in DATASETS.values()],
            "rules": [{"id": k, "default": default, "desc": desc} for k, (default, desc) in pipeline.RULES.items()],
            "sources": sorted((_source(s, x, rt) for x in sources), key=lambda x: (order[x["dataset"]], x["name"])),
            "runs": [_run(r, rt.id, names.get(r.source_id)) for r in runs]}


@router.post("/preview")
def post_preview(body: SourceIn, rt: TenantRuntime = Depends(tenant), _: Persona = Depends(require(LOAD))):
    return _guard(pipeline.preview, rt.id, body.model_dump())


@router.post("/sources")
def create_source(body: SourceIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(require(LOAD))):
    return _source(s, _guard(pipeline.save_source, s, body.model_dump(), p.id), rt)


def _get(s: Session, source_id: str) -> DataSource:
    src = s.get(DataSource, source_id)
    if src is None:
        raise HTTPException(404, "Source not found")
    return src


@router.put("/sources/{source_id}")
def update_source(source_id: str, body: SourceIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant),
                  p: Persona = Depends(require(LOAD))):
    return _source(s, _guard(pipeline.save_source, s, body.model_dump(), p.id, _get(s, source_id)), rt)


@router.post("/sources/{source_id}/run")
def run_one(source_id: str, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(require(LOAD))):
    src = _get(s, source_id)
    return _run(pipeline.run_source(s, src, p.id), rt.id, src.name)


class RunIn(BaseModel):
    due_only: bool = False


@router.post("/run")
def run_all(body: RunIn, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant), p: Persona = Depends(require(LOAD))):
    """Run every enabled source as one batch, in dependency order. With due_only, just the ones whose schedule has come round."""
    sources = [x for x in s.scalars(select(DataSource).where(DataSource.enabled.is_(True))) if not body.due_only or pipeline.health(x)["due"]]
    names = {x.id: x.name for x in sources}
    return {"runs": [_run(r, rt.id, names.get(r.source_id)) for r in pipeline.run_many(s, sources, p.id)]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    r = s.get(PipelineRun, run_id)
    if r is None:
        raise HTTPException(404, "Run not found")
    src = s.get(DataSource, r.source_id)
    objects = list(s.scalars(select(IngestedObject).where(IngestedObject.run_id == r.id).order_by(IngestedObject.key)))
    loaded = dict(s.execute(select(Transaction.feed_id, func.count()).where(Transaction.feed_id.in_([o.id for o in objects]))
                            .group_by(Transaction.feed_id)).all()) if objects else {}
    return _run(r, rt.id, src.name if src else None) | {
        "objects": [{"id": o.id, "key": o.key, "size": o.size, "fingerprint": o.fingerprint, "status": o.status, "reason": o.reason,
                     "stats": o.stats, "transactions_now": loaded.get(o.id, 0)} for o in objects]}


@router.get("/lineage/{txn_id}")
def get_lineage(txn_id: str, s: Session = Depends(get_session), rt: TenantRuntime = Depends(tenant)):
    """Where a transaction came from: the file, the source, the run and the mapping version that loaded it."""
    t = s.get(Transaction, {"tenant_id": rt.id, "id": txn_id})
    if t is None:
        raise HTTPException(404, "Transaction not found")
    return {"transaction": t.id, "origin": pipeline.lineage(s, t.feed_id) or {"object": None, "note": f"Loaded outside a pipeline ({t.feed_id})"}}
