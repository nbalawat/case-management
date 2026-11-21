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
        "lake": lake(tenant),
        "search": search_engine(tenant),
        "graph": graph_engine(tenant),
        "clocks": stage_clocks(tenant),
        "stream_jobs": stream_jobs(s, tenant),
        # Whether this API process also runs the relay and consumers. False is normal when a separate worker does.
        "bus": {"kind": kind, "note": note, "workers_in_api": settings.backbone_workers},
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


def lake(tenant_id: str) -> dict:
    """This client's tables on the lake. A lake that cannot be reached is reported, not raised: the page still loads."""
    from ..lake.base import LakeError
    from ..lake.iceberg import connect

    if not settings.lake_catalog:
        return {"connected": False, "note": "No lake is connected. History is kept in the operational database only.", "tables": []}
    try:
        store = connect()
        tables = store.tables(tenant_id)
    except LakeError as e:
        return {"connected": False, "note": str(e), "tables": []}
    except Exception:  # noqa: BLE001  catalogs fail in their own ways; none of it may break the page
        return {"connected": False, "note": "The lake catalog did not answer.", "tables": []}
    return {"connected": True, "note": f"Apache Iceberg ({store.url})",
            "tables": [{"name": t.name, "rows": t.rows, "commits": t.commits, "last_commit": t.last_commit.isoformat() if t.last_commit else None} for t in tables]}


def search_engine(tenant_id: str) -> dict:
    """Where this client's name search is answered. An engine that cannot be reached is reported, not raised."""
    if not settings.search_url:
        return {"engine": "database", "names": None, "note": "Names are searched in the operational database."}
    try:
        from ..entities import search

        return {"engine": "opensearch", "names": search.current().count(tenant_id), "note": "OpenSearch, one index per client, rebuilt after each entity resolution."}
    except Exception:  # noqa: BLE001
        return {"engine": "database", "names": None, "note": "The search engine did not answer, so names are being searched in the database."}


def stream_jobs(s: Session, tenant_id: str) -> dict:
    """This client's jobs on the stream processor, and whether the application is raising the same rule as well."""
    from ..detection import tuning
    from ..streamjobs import flink
    from ..streamjobs.jobs import JOBS

    from ..streamjobs import control

    on = {j.scenario: tuning.current(s, j.scenario)[1] for j in JOBS.values()} if settings.flink_url else {}
    view = flink.status(tenant_id, on)
    return view | {"jobs": control.describe(s, view["jobs"]), "controlled_here": control.enabled()}


def graph_engine(tenant_id: str) -> dict:
    """Where routes through this client's payment network are found. An engine that cannot be reached is reported, not raised."""
    if not settings.graph_url:
        return {"engine": "database", "entities": None, "links": None, "note": "Routes between customers are found in the operational database."}
    try:
        from ..entities import graphstore

        size = graphstore.current().size(tenant_id)
    except Exception:  # noqa: BLE001
        return {"engine": "database", "entities": None, "links": None, "note": "The graph engine did not answer, so routes are being found in the database."}
    if size is None:
        return {"engine": "age", "entities": None, "links": None, "note": "Apache AGE is connected. This client's graph is built at its next entity resolution; until then routes are found in the database."}
    return {"engine": "age", "entities": size[0], "links": size[1], "note": "Apache AGE, one graph per client, rebuilt after each entity resolution."}


def stage_clocks(tenant_id: str) -> dict:
    """Where this client's stage clocks run: the sweep, or durable timers on Temporal."""
    from ..durable import temporal

    return temporal.status(tenant_id)
