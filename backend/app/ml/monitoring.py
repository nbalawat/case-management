"""Is a model still looking at the population it learnt from? Population stability per feature, over recent alerts."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Alert, ModelVersion, now
from ..features import compute, get as feature_set
from .metrics import psi, shares

WATCH, MOVED = 0.1, 0.25
# With ten bins, chance alone gives a population stability index of roughly (bins - 1) / n, so below a few hundred
# alerts a high figure is as likely to be noise as a moved population. Under this, figures are shown but not judged.
MIN_ALERTS = 200


def drift(s: Session, mv: ModelVersion, days: int = 30) -> dict:
    alerts = list(s.scalars(select(Alert).where(Alert.created >= now() - timedelta(days=days))))
    fs = feature_set(mv.feature_set)
    out = {"days": days, "alerts": len(alerts), "min_alerts": MIN_ALERTS, "enough": len(alerts) >= MIN_ALERTS, "features": []}
    if not alerts:
        return out
    values = compute(s, fs, [(a.party_id, a.created) for a in alerts])
    labels = {f.name: f.label for f in fs.features}
    for name in mv.features:
        ref = mv.metrics.get("reference", {}).get(name)
        if not ref or not ref["edges"]:
            continue
        now_shares = shares([values[(a.party_id, a.created)][name] for a in alerts], ref["edges"])
        index = psi(ref["shares"], now_shares)
        out["features"].append({"feature": name, "label": labels.get(name, name), "psi": index,
                                "state": "too_few" if not out["enough"] else "moved" if index > MOVED else "watch" if index > WATCH else "stable"})
    out["features"].sort(key=lambda f: -f["psi"])
    scored = [a.ranking["model"]["probability"] for a in alerts if (a.ranking or {}).get("model", {}).get("version") == mv.version]
    out["scored"] = len(scored)
    out["mean_score"] = round(sum(scored) / len(scored), 4) if scored else None
    return out
