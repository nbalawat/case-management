"""Labelled examples for a model, each with features as they stood at the moment the alert arrived."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Case, TrainingLabel
from ..features import FeatureSet, compute
from ..features.engine import POSITIVE


@dataclass(frozen=True)
class Example:
    ref: str
    party_id: str
    as_of: datetime
    label: bool
    source: str  # case outcome | the TrainingLabel source


def examples(s: Session) -> list[Example]:
    """Every alert whose case has closed, and every loaded outcome, oldest first."""
    out = [Example(a.id, a.party_id, a.created, c.disposition in POSITIVE, "case outcome")
           for a, c in s.execute(select(Alert, Case).join(Case, (Case.id == Alert.case_id) & (Case.tenant_id == Alert.tenant_id))
                                 .where(Case.closed.is_not(None), Case.disposition.is_not(None)))]
    out += [Example(t.ref, t.party_id, t.as_of, t.label, t.source) for t in s.scalars(select(TrainingLabel))]
    return sorted(out, key=lambda e: (e.as_of, e.ref))


def matrix(s: Session, fs: FeatureSet, rows: list[Example]) -> list[list[float]]:
    values = compute(s, fs, [(e.party_id, e.as_of) for e in rows])
    return [[values[(e.party_id, e.as_of)][n] for n in fs.names] for e in rows]
