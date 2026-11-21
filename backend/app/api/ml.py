"""Models that rank alerts, and the feature store behind them: the registry, model cards, training, approval and drift."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import ModelVersion, Party, TrainingLabel
from ..domain.personas import Persona
from ..features import FeatureError, get as feature_set, library
from ..features import online
from ..ml import APPROVE, NAME, TRAIN, registry
from ..ml.dataset import examples
from ..ml.monitoring import drift
from ..ml.train import MIN_EACH, MIN_ROWS, TrainingError, train
from .deps import actor, get_session, persona

router = APIRouter(prefix="/api/ml")


def _reader(p: Persona = Depends(persona)) -> Persona:
    if not (p.can("view_team") or p.can(TRAIN) or p.can(APPROVE)):
        raise HTTPException(403, f"{p.title} cannot see models. It needs team oversight, tuning or approval permissions.")
    return p


def _row(mv: ModelVersion) -> dict:
    m = mv.metrics or {}
    return {"id": mv.id, "name": mv.name, "version": mv.version, "algorithm": mv.algorithm, "status": mv.status, "trained_by": mv.trained_by,
            "trained": mv.trained.isoformat(), "approved_by": mv.approved_by, "approved": mv.approved.isoformat() if mv.approved else None,
            "auc": m.get("auc"), "pr_auc": m.get("pr_auc"), "base_rate": m.get("base_rate"), "could_wait_at_95": m.get("work", {}).get("could_wait_at_95"),
            "rows": mv.data.get("rows"), "positives": mv.data.get("positives"), "sources": mv.data.get("sources", {})}


@router.get("/models")
def models(s: Session = Depends(get_session), p: Persona = Depends(_reader)):
    labels = dict(s.execute(select(TrainingLabel.source, func.count()).group_by(TrainingLabel.source)).all())
    rows = examples(s)
    return {"name": NAME, "versions": [_row(mv) for mv in registry.versions(s)],
            "data": {"examples": len(rows), "positives": sum(e.label for e in rows), "loaded_labels": labels, "min_rows": MIN_ROWS, "min_each": MIN_EACH},
            "can": {"train": p.can(TRAIN) and not p.read_only, "approve": p.can(APPROVE) and not p.read_only, "me": p.id}}


@router.get("/models/{model_id}")
def card(model_id: str, s: Session = Depends(get_session), _: Persona = Depends(_reader)):
    mv = s.get(ModelVersion, model_id)
    if mv is None:
        raise HTTPException(404, "No such model")
    fs = feature_set(mv.feature_set)
    labels = {f.name: f.label for f in fs.features}
    metrics = {k: v for k, v in mv.metrics.items() if k != "reference"}
    return _row(mv) | {"metrics": metrics, "data": mv.data, "params": mv.params, "note": mv.note, "history": mv.history,
                       "feature_set": {"id": fs.id, "version": mv.set_version}, "features": [{"name": n, "label": labels.get(n, n)} for n in mv.features],
                       "drift": drift(s, mv), "sha256": mv.sha256}


class TrainIn(BaseModel):
    note: str = ""


@router.post("/models/train")
def train_now(body: TrainIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    try:
        mv = train(s, p, note=body.note.strip())
    except TrainingError as e:
        raise HTTPException(400, str(e)) from e
    s.commit()
    return _row(mv)


class DecisionIn(BaseModel):
    note: str = ""
    role: str = "champion"


def _registry_call(fn, s: Session):
    try:
        mv = fn()
    except registry.RegistryError as e:
        raise HTTPException(400, str(e)) from e
    s.commit()
    return _row(mv)


@router.post("/models/{model_id}/approve")
def approve(model_id: str, body: DecisionIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return _registry_call(lambda: registry.approve(s, model_id, p, body.note.strip()), s)


@router.post("/models/{model_id}/promote")
def promote(model_id: str, body: DecisionIn, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return _registry_call(lambda: registry.promote(s, model_id, p, body.role), s)


@router.post("/models/{model_id}/retire")
def retire(model_id: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return _registry_call(lambda: registry.retire(s, model_id, p), s)


@router.get("/features")
def feature_sets(_: Persona = Depends(_reader)):
    return {"sets": [fs.as_dict() for fs in library().values()]}


@router.get("/features/{set_id}/{party_id}")
def customer_features(set_id: str, party_id: str, s: Session = Depends(get_session), _: Persona = Depends(_reader)):
    try:
        fs = feature_set(set_id)
    except FeatureError as e:
        raise HTTPException(404, str(e)) from e
    if s.scalar(select(Party.id).where(Party.id == party_id)) is None:
        raise HTTPException(404, "No such customer")
    got = online.read(s, fs, [party_id])[party_id]
    s.commit()
    return {"set": fs.id, "version": got["set_version"], "computed": got["computed"],
            "features": [{"name": f.name, "label": f.label, "value": got["values"].get(f.name)} for f in fs.features]}
