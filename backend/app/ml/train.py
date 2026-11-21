"""Training a model on a client's labelled alerts, and recording it as a candidate in the registry."""
from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.access import Persona
from ..domain.models import ModelVersion, new_id, now
from ..features import get as feature_set
from ..tenancy import tenant_of
from . import NAME, TRAIN, artifacts
from .dataset import examples, matrix
from .metrics import reference, workload

MIN_ROWS, MIN_EACH = 100, 15
TEST_SHARE = 0.25
SEED = 7


class TrainingError(ValueError):
    pass


def _sklearn():
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        raise TrainingError("Training needs scikit-learn: install the backend's `ml` group (the container image has it)") from None
    return HistGradientBoostingClassifier, LogisticRegression, average_precision_score, brier_score_loss, roc_auc_score, make_pipeline, StandardScaler


def train(s: Session, persona: Persona, *, feature_set_id: str = "party_risk", note: str = "") -> ModelVersion:
    """Fit on the older alerts, measure on the newest ones, and register the result as a candidate."""
    if not persona.can(TRAIN):
        raise TrainingError("Training a model needs the 'tune detection' permission")
    HGB, LR, ap, brier, auc, pipeline, scaler = _sklearn()
    fs = feature_set(feature_set_id)
    rows = examples(s)
    pos = sum(e.label for e in rows)
    if len(rows) < MIN_ROWS or pos < MIN_EACH or len(rows) - pos < MIN_EACH:
        raise TrainingError(f"Not enough decided alerts to learn from: {len(rows)} with {pos} that mattered. A model needs at least "
                            f"{MIN_ROWS}, with {MIN_EACH} of each outcome. Close more cases, or load historical outcomes.")
    t0 = time.perf_counter()
    X, y = matrix(s, fs, rows), [int(e.label) for e in rows]
    cut = int(len(rows) * (1 - TEST_SHARE))  # older alerts to learn from, newest to test on: as it will be used
    if sum(y[cut:]) < 5 or sum(y[:cut]) < 5:
        raise TrainingError("The newest quarter of alerts, or the rest, has fewer than 5 that mattered; the test would mean nothing")
    # Choose between a boosted-tree model and a regularised logistic baseline on the newest part of the training
    # window, then refit the choice on all of it. The test alerts play no part in the choice.
    inner = int(cut * 0.8)
    candidates = {"HistGradientBoostingClassifier (scikit-learn)": lambda: HGB(max_iter=150, learning_rate=0.05, max_leaf_nodes=7, min_samples_leaf=40,
                                                                             l2_regularization=1.0, random_state=SEED),
                  "LogisticRegression, standardised (scikit-learn)": lambda: pipeline(scaler(), LR(max_iter=1000, C=0.5))}
    validation = {}
    for name, make in candidates.items():
        fitted = make().fit(X[:inner], y[:inner])
        validation[name] = float(auc(y[inner:cut], fitted.predict_proba(X[inner:cut])[:, 1])) if 0 < sum(y[inner:cut]) < cut - inner else 0.0
    algorithm = max(validation, key=validation.get)
    model = candidates[algorithm]().fit(X[:cut], y[:cut])
    other = next(n for n in candidates if n != algorithm)
    baseline = candidates[other]().fit(X[:cut], y[:cut])
    p = [float(v) for v in model.predict_proba(X[cut:])[:, 1]]
    pb = [float(v) for v in baseline.predict_proba(X[cut:])[:, 1]]
    test = y[cut:]
    metrics = {"auc": round(float(auc(test, p)), 4), "pr_auc": round(float(ap(test, p)), 4), "brier": round(float(brier(test, p)), 4),
               "base_rate": round(sum(test) / len(test), 4), "other_auc": round(float(auc(test, pb)), 4), "other_algorithm": other,
               "validation_auc": {k: round(v, 4) for k, v in validation.items()}, "work": workload(test, p),
               "reference": {n: reference([r[i] for r in X[:cut]]) for i, n in enumerate(fs.names)},
               "train_seconds": round(time.perf_counter() - t0, 2)}
    medians = [sorted(r[i] for r in X[:cut])[cut // 2] for i in range(len(fs.names))]
    version = (s.scalar(select(func.max(ModelVersion.version)).where(ModelVersion.name == NAME)) or 0) + 1
    tenant = tenant_of(s)
    key, digest = artifacts.save(f"{tenant}/{NAME}/v{version}.pkl", {"model": model, "features": fs.names, "set": fs.id,
                                                                    "set_version": fs.version, "medians": medians})
    sources: dict[str, int] = {}
    for e in rows:
        sources[e.source] = sources.get(e.source, 0) + 1
    mv = ModelVersion(id=new_id("mdl"), name=NAME, version=version, algorithm=algorithm,
                      feature_set=fs.id, set_version=fs.version, features=fs.names,
                      params={k: v for k, v in (model.get_params() if hasattr(model, "get_params") else {}).items() if isinstance(v, (int, float, str, bool))},
                      metrics=metrics, artifact=key, sha256=digest, status="candidate", trained_by=persona.id, trained=now(), note=note,
                      data={"rows": len(rows), "positives": pos, "train_rows": cut, "test_rows": len(rows) - cut, "sources": sources,
                            "from": _iso(rows[0].as_of), "test_from": _iso(rows[cut].as_of), "to": _iso(rows[-1].as_of)})
    s.add(mv)
    s.flush()
    return mv


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="minutes")
