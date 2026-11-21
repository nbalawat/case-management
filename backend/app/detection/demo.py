"""Synthetic detection governance for the demo: one change waiting for a second person, and one lookback planned but not yet run.

Nothing here changes what detection raises: the proposal is pending and the lookback has only been counted.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..domain.models import side_ids
from ..domain.personas import policy_of
from ..tenancy import tenant_of
from . import lookbacks, tuning


def seed(s: Session, end: datetime) -> None:
    tenant = tenant_of(s)
    if tenant != "meridian":
        return
    people = [p for p in policy_of(tenant).people.values() if not p.read_only and p.can(tuning.TUNE)]
    proposer = next((p for p in people if not p.can(tuning.APPROVE)), None)
    planner = next((p for p in people if p.can(tuning.APPROVE)), None)
    if proposer is None or planner is None:
        return
    with side_ids(f"detection:{tenant}"):
        tuning.propose(s, "structuring", {"count": 4}, True,
                       "Three deposits in a week catches ordinary cash businesses banking their takings. Four keeps every alert that led to a filing "
                       "last year and removes a third of the rest.", proposer, at=end - timedelta(hours=20))
        lookbacks.create(s, planner, "Cash deposits from $5,000", "Internal audit found that deposits kept between $5,000 and $8,000 were never looked at. "
                         "This runs the last year again from $5,000 so that anything missed is reviewed by a person.", "structuring", {"min": 5000},
                         (end - timedelta(days=365)).date().isoformat(), (end - timedelta(days=1)).date().isoformat(),
                         due=(end + timedelta(days=45)).date().isoformat(), at=end - timedelta(days=2))
        s.flush()
