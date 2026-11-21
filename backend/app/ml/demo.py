"""Demo only: a labelled history to learn from, a trained champion, and scores on the demo's alerts.

A new client has few closed cases, so a real deployment loads historical outcomes from the client's previous system
(TrainingLabel rows with that system named as the source). The demo has no such history, so it writes a synthetic
one: moments in the demo's own transaction history, labelled by a known rule plus noise. Every such label says
"synthetic history (demo)", the model card shows it, and the metrics describe how well a model recovers that rule,
not how it would do on real outcomes.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Alert, TrainingLabel, Transaction
from ..domain.personas import policy_of
from ..features import compute, get as feature_set
from ..tenancy import tenant_of
from . import APPROVE, TRAIN

SOURCE = "synthetic history (demo)"
SIZES = {"meridian": 1500}
DEFAULT_SIZE = 600


def _truth(f: dict, r: random.Random) -> bool:
    z = (-3.6 + 0.9 * min(f["near_threshold_cash_30d"], 4) + 0.9 * min(f["high_risk_country_30d"], 3) + 0.9 * f["risk_level"] + 1.2 * f["pep"]
         + 1.2 * min(f["confirmed_concerns"], 2) + 0.5 * min(f["alerts_90d"], 3) + 0.35 * math.log1p(f["cash_in_30d"] / 1000) + r.gauss(0, 0.5))
    return r.random() < 1 / (1 + math.exp(-z))


def seed(s: Session, end: datetime) -> dict:
    tenant = tenant_of(s)
    r = random.Random(f"ml:{tenant}")
    # Moments just after real payments in the demo's history, as alerts are raised just after payments.
    recent = list(s.execute(select(Transaction.party_id, Transaction.ts).where(Transaction.ts >= end - timedelta(days=20), Transaction.ts < end)))
    if not recent:
        return {}
    fs = feature_set("party_risk")
    n = SIZES.get(tenant, DEFAULT_SIZE)
    keys = list(dict.fromkeys((t.party_id, t.ts + timedelta(minutes=1)) for t in r.sample(recent, min(n, len(recent)))))
    n = len(keys)
    values = compute(s, fs, keys)
    for i, k in enumerate(keys):
        s.add(TrainingLabel(ref=f"demo-history-{i:05d}", party_id=k[0], as_of=k[1], label=_truth(values[k], r), source=SOURCE))
    s.flush()
    people = list(policy_of(tenant).people.values())
    trainer = next((p for p in people if p.can(TRAIN) and not p.can(APPROVE)), None) or next((p for p in people if p.can(TRAIN)), None)
    approver = next((p for p in people if p.can(APPROVE) and trainer and p.id != trainer.id), None)
    if trainer is None or approver is None:
        return {"labels": n}
    from . import registry
    from .scoring import score
    from .train import TrainingError, train

    try:
        mv = train(s, trainer, note="First model for this client, trained on the demo's synthetic history.")
    except TrainingError:
        return {"labels": n}
    registry.approve(s, mv.id, approver, "Reviewed the model card: workload curve and reasons read sensibly for a first champion.")
    registry.promote(s, mv.id, approver, "champion")
    scored = score(s, list(s.scalars(select(Alert))))
    return {"labels": n, "model": f"v{mv.version}", "auc": mv.metrics["auc"], "scored": scored}
