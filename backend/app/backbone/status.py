"""What one client can see of the backbone: how its events are leaving and what is arriving on its streams."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..domain.models import OutboxEvent, StreamStat
from ..tenancy import tenant_of
from . import topics

BUS = {"": ("none", "No event bus is connected. Events are kept in the outbox and go out when one is."),
       "memory": ("memory", "An in-process bus, for a laptop and for tests. Nothing outside this process can read it."),
       "kafka": ("kafka", "Kafka, or a service that speaks its protocol.")}
STREAM_LABEL = {topics.TRANSACTIONS: "Transactions", topics.ALERTS_IN: "Alerts from other engines"}


def view(s: Session) -> dict:
    tenant = tenant_of(s)
    kind, note = BUS.get(settings.event_bus_url.split("://")[0], ("other", ""))
    waiting, oldest = s.execute(select(func.count(), func.min(OutboxEvent.created)).where(OutboxEvent.published_at.is_(None))).one()
    published, last = s.execute(select(func.count(), func.max(OutboxEvent.published_at)).where(OutboxEvent.published_at.is_not(None))).one()
    by_type = s.execute(select(OutboxEvent.type, func.count()).group_by(OutboxEvent.type).order_by(func.count().desc(), OutboxEvent.type).limit(8)).all()
    stats = {x.stream: x for x in s.scalars(select(StreamStat))}
    cdc = settings.outbox_publisher != "relay"
    return {
        "bus": {"kind": kind, "note": note, "workers": settings.backbone_workers},
        "publisher": "debezium" if cdc else "relay",
        # Under change data capture rows are never marked here, so "waiting" would mislead: report only the total.
        "outbox": {"waiting": None if cdc else waiting, "oldest_waiting": None if cdc or oldest is None else oldest.isoformat(),
                   "published": None if cdc else published, "last_published": last.isoformat() if last else None, "total": waiting + published,
                   "by_type": [{"type": t, "count": n} for t, n in by_type]},
        "topics": {"out": [topics.topic(tenant, topics.CASE_EVENTS), topics.topic(tenant, topics.ALERTS)],
                   "in": [topics.topic(tenant, n) for n in topics.STREAMS], "dead_letter": topics.topic(tenant, topics.DEAD_LETTER)},
        "streams": [{"stream": n, "label": STREAM_LABEL[n], "topic": topics.topic(tenant, n),
                     **({"received": st.received, "loaded": st.loaded, "duplicates": st.duplicates, "rejected": st.rejected, "alerts": st.alerts,
                         "last_at": st.last_at.isoformat() if st.last_at else None, "last_reject": st.last_reject}
                        if (st := stats.get(n)) else {"received": 0, "loaded": 0, "duplicates": 0, "rejected": 0, "alerts": 0, "last_at": None, "last_reject": ""})}
                    for n in topics.STREAMS],
    }
