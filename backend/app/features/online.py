"""The online store: the latest features per customer, kept in the database so scoring reads one row, not history.

`materialize` recomputes a set for a client's customers (all, or the ones whose activity just changed) as of now and
upserts them; `read` returns them, recomputing any that are missing or older than `max_age`.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import FeatureValue, Party, now
from .engine import compute
from .model import FeatureSet


def materialize(s: Session, fs: FeatureSet, party_ids: list[str] | None = None) -> int:
    at = now()
    ids = party_ids if party_ids is not None else list(s.scalars(select(Party.id)))
    if not ids:
        return 0
    values = compute(s, fs, [(p, at) for p in ids])
    have = {r.entity_id: r for r in s.scalars(select(FeatureValue).where(FeatureValue.feature_set == fs.id, FeatureValue.entity_id.in_(ids)))}
    for p in ids:
        row = have.get(p)
        if row is None:
            s.add(FeatureValue(feature_set=fs.id, set_version=fs.version, entity_id=p, values=values[(p, at)], computed=at))
        else:
            row.values, row.set_version, row.computed = values[(p, at)], fs.version, at
    s.flush()
    return len(ids)


def read(s: Session, fs: FeatureSet, party_ids: list[str], max_age: timedelta = timedelta(hours=1)) -> dict[str, dict]:
    rows = {r.entity_id: r for r in s.scalars(select(FeatureValue).where(FeatureValue.feature_set == fs.id, FeatureValue.entity_id.in_(party_ids)))}
    stale = [p for p in party_ids if p not in rows or rows[p].set_version != fs.version or rows[p].computed < now() - max_age]
    if stale:
        materialize(s, fs, stale)
        rows = {r.entity_id: r for r in s.scalars(select(FeatureValue).where(FeatureValue.feature_set == fs.id, FeatureValue.entity_id.in_(party_ids)))}
    return {p: {"values": rows[p].values, "computed": rows[p].computed.isoformat(), "set_version": rows[p].set_version} for p in party_ids if p in rows}
