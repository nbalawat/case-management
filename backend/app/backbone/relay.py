"""Moves outbox rows onto the bus.

Rows go out in the order they were written and are marked only after the bus has acknowledged them, so a crash
re-sends the last batch rather than losing it: delivery is at least once, and consumers drop repeats by event id.
Several relays can run side by side on Postgres; each takes rows the others have not locked.

With OUTBOX_PUBLISHER=debezium the relay stands down: change data capture reads the same rows from the database
log and publishes the same payloads.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..domain.models import OutboxEvent, now
from .bus import EventBus, Message
from .outbox import CONTENT_TYPE

BATCH = 500


def message(row: OutboxEvent) -> Message:
    return Message(row.topic, row.key, row.payload.encode(), {"content-type": CONTENT_TYPE, "id": row.event_id, "type": row.type})


def publish_batch(s: Session, bus: EventBus, batch: int = BATCH) -> int:
    """Publish the oldest unpublished rows, up to `batch`. The caller commits."""
    q = select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.id).limit(batch)
    if s.get_bind().dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    rows = list(s.scalars(q))
    if not rows:
        return 0
    bus.publish(message(r) for r in rows)  # raises before anything is marked
    at = now()
    for r in rows:
        r.published_at = at
    return len(rows)


def drain(bus: EventBus, batch: int = BATCH, limit: int | None = None) -> int:
    """Publish until the outbox is empty (or `limit` rows have gone), one transaction per batch."""
    if settings.outbox_publisher != "relay":
        return 0
    sent = 0
    while limit is None or sent < limit:
        with session_scope(platform=True) as s:
            n = publish_batch(s, bus, batch if limit is None else min(batch, limit - sent))
        if not n:
            break
        sent += n
    return sent


def prune(keep_days: int | None = None) -> int:
    """Delete rows that have done their job. Under Debezium nothing is ever marked, so age alone decides."""
    cutoff = now() - timedelta(days=settings.outbox_keep_days if keep_days is None else keep_days)
    q = delete(OutboxEvent).where(OutboxEvent.created < cutoff)
    if settings.outbox_publisher == "relay":
        q = q.where(OutboxEvent.published_at.is_not(None))
    with session_scope(platform=True) as s:
        return s.execute(q).rowcount or 0
