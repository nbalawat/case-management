"""The champion's score on each new alert, with its reasons; the challenger's alongside, for comparison only.

Reasons are local and plain: for each feature, how much the probability falls when that feature is set to a typical
(training median) value. The three that matter most are kept with the score, so an analyst sees "cash deposited in
30 days: 46,200" rather than a bare number.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..domain.models import Alert, ModelVersion
from ..features import compute, get as feature_set
from . import artifacts
from .registry import in_role

log = logging.getLogger("caseforge.ml")
TOP = 3


def _score(s: Session, mv: ModelVersion, alerts: list[Alert]) -> dict[str, dict]:
    art = artifacts.load(mv.artifact, mv.sha256)
    fs = feature_set(mv.feature_set)
    names, medians, model = art["features"], art["medians"], art["model"]
    values = compute(s, fs, [(a.party_id, a.created) for a in alerts])
    rows = [[values[(a.party_id, a.created)][n] for n in names] for a in alerts]
    base = model.predict_proba(rows)[:, 1]
    labels = {f.name: f.label for f in fs.features}
    out = {}
    for a, row, p in zip(alerts, rows, base):
        what_if = [row[:i] + [medians[i]] + row[i + 1:] for i in range(len(names))]
        drops = [float(p - q) for q in model.predict_proba(what_if)[:, 1]]
        top = sorted(range(len(names)), key=lambda i: -drops[i])[:TOP]
        out[a.id] = {"model": mv.name, "version": mv.version, "probability": round(float(p), 4),
                     "reasons": [{"feature": names[i], "label": labels.get(names[i], names[i]), "value": row[i], "typical": medians[i],
                                  "effect": round(drops[i], 4)} for i in top if drops[i] > 0.005]}
    return out


def score(s: Session, alerts: list[Alert]) -> int:
    """Write the champion's (and any challenger's) reading onto each alert's triage. Never stops alert handling."""
    if not alerts:
        return 0
    done = 0
    for role, slot in (("champion", "model"), ("challenger", "challenger_model")):
        try:
            mv = in_role(s, role)
            if mv is None:
                continue
            readings = _score(s, mv, alerts)
        except Exception as e:  # noqa: BLE001  a model that cannot score leaves alerts as they were, and says so
            log.warning("The %s model could not score %d alert(s): %s", role, len(alerts), e)
            continue
        for a in alerts:
            a.triage = {**(a.triage or {}), slot: readings[a.id]}
        done = len(alerts)
    return done
