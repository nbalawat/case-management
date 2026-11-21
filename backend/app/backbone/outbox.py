"""Fills the outbox: every audit event and every new alert becomes one message, in the transaction that made it.

Nothing calls this module. It listens to the unit of work, so a change and its event commit together or not at all,
and no feature can forget to publish. Messages are CloudEvents 1.0 in JSON, the same shape on any bus.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..config import settings
from ..domain.models import Alert, CaseEvent, OutboxEvent, now
from ..tenancy import TENANT_KEY
from . import topics

CONTENT_TYPE = "application/cloudevents+json"


def _iso(ts: datetime) -> str:
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).isoformat().replace("+00:00", "Z")


def envelope(tenant_id: str, kind: str, subject: str, ts: datetime, data: dict, event_id: str | None = None) -> dict:
    return {"specversion": "1.0", "id": event_id or str(uuid.uuid4()), "source": f"/{settings.event_topic_prefix}/{tenant_id}",
            "type": f"{settings.event_topic_prefix}.{kind}", "subject": subject, "time": _iso(ts),
            "datacontenttype": "application/json", "tenantid": tenant_id, "data": data}


def _row(tenant_id: str, topic: str, key: str, kind: str, subject: str, ts: datetime, data: dict) -> OutboxEvent:
    env = envelope(tenant_id, kind, subject, ts, data)
    return OutboxEvent(tenant_id=tenant_id, event_id=env["id"], topic=topics.topic(tenant_id, topic), key=key, type=env["type"],
                       payload=json.dumps(env, separators=(",", ":"), default=str), created=now())


def for_case_event(ev: CaseEvent, tenant_id: str) -> OutboxEvent:
    ts = ev.ts or now()
    return _row(tenant_id, topics.CASE_EVENTS, ev.case_id, ev.kind, ev.case_id, ts,
                {"case_id": ev.case_id, "actor": ev.actor, "actor_kind": ev.actor_kind, "payload": ev.payload or {}})


def for_alert(a: Alert, tenant_id: str) -> OutboxEvent:
    ts = a.created or now()
    return _row(tenant_id, topics.ALERTS, a.party_id, "alert.created", a.id, ts,
                {"alert_id": a.id, "source": a.source, "scenario": a.scenario, "scenario_version": a.scenario_version, "party_id": a.party_id,
                 "score": a.score, "explanation": a.explanation, "txn_ids": list(a.txn_ids or [])})


@event.listens_for(Session, "before_flush")
def _fill(session: Session, _context, _instances) -> None:
    tenant = session.info.get(TENANT_KEY)
    for obj in list(session.new):
        make = for_case_event if isinstance(obj, CaseEvent) else for_alert if isinstance(obj, Alert) else None
        if make is None or getattr(obj, "_in_outbox", False):
            continue
        owner = obj.tenant_id or tenant
        if owner is None:
            continue  # the tenancy check refuses this row in the same flush; nothing to publish
        obj._in_outbox = True
        session.add(make(obj, owner))
