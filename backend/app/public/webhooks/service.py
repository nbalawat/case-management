"""Webhook endpoints and their deliveries.

Fan-out walks the client's outbox past each endpoint's cursor and queues a delivery for every event the endpoint's
owner may see and asked for. Delivery signs and posts it, and on failure retries on a widening schedule for about
a day and a half. An endpoint that keeps failing is switched off and says why; nothing is lost, the owner can
retry any delivery after fixing their receiver.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import session_scope
from ...domain.models import OutboxEvent, new_id, now
from ...tenancy import tenant_of
from ...tenants.registry import UnknownTenant, all_runtimes, runtime
from .. import credentials, events, scopes as catalogue
from ..models import ApiClient, WebhookDelivery, WebhookEndpoint
from ..principal import APP_ACTOR, Principal, PrincipalError, app_persona
from . import netguard, signing

log = logging.getLogger("caseforge.webhooks")
BACKOFF = [0, 30, 120, 600, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600]  # seconds before each attempt
DISABLE_AFTER = 50  # failed attempts in a row
MAX_ENDPOINTS = 20
Transport = Callable[[str, bytes, dict], int]


class WebhookError(ValueError):
    pass


def _transport(url: str, body: bytes, headers: dict) -> int:
    return netguard.post(url, body, headers)


transport: Transport = _transport


def swap_transport(t: Transport) -> Transport:
    global transport
    old, transport = transport, t
    return old


def _check(url: str, types: list[str]) -> tuple[str, list[str]]:
    try:
        return netguard.check_url(url), events.check_patterns(types)
    except (netguard.UnsafeAddress, ValueError) as e:
        raise WebhookError(str(e)) from None


def create(s: Session, owner: Principal, url: str, types: list[str], description: str = "") -> tuple[WebhookEndpoint, str]:
    url, types = _check(url, types)
    if (s.scalar(select(func.count()).select_from(WebhookEndpoint).where(WebhookEndpoint.client_id == owner.caller)) or 0) >= MAX_ENDPOINTS:
        raise WebhookError(f"At most {MAX_ENDPOINTS} endpoints per caller")
    secret = credentials.new_webhook_secret()
    start = s.scalar(select(func.max(OutboxEvent.id))) or 0  # only events from now on
    ep = WebhookEndpoint(id=new_id("whk"), client_id=owner.caller, url=url, event_types=types, description=description.strip()[:500],
                         secret=credentials.seal(secret), cursor=start, created_by=owner.caller)
    s.add(ep)
    s.flush()
    return ep, secret


def mine(s: Session, owner: Principal) -> list[WebhookEndpoint]:
    return list(s.scalars(select(WebhookEndpoint).where(WebhookEndpoint.client_id == owner.caller).order_by(WebhookEndpoint.created)))


def get(s: Session, owner: Principal | None, endpoint_id: str) -> WebhookEndpoint:
    ep = s.get(WebhookEndpoint, endpoint_id)
    if ep is None or ep.tenant_id != tenant_of(s) or (owner is not None and ep.client_id != owner.caller):
        raise WebhookError("No such webhook endpoint")
    return ep


def update(s: Session, ep: WebhookEndpoint, *, url: str | None = None, types: list[str] | None = None, description: str | None = None,
           status: str | None = None) -> WebhookEndpoint:
    if url is not None or types is not None:
        ep.url, ep.event_types = _check(url if url is not None else ep.url, types if types is not None else ep.event_types)
    if description is not None:
        ep.description = description.strip()[:500]
    if status is not None:
        if status not in ("active", "paused"):
            raise WebhookError("Status is active or paused")
        if status == "active":
            ep.failures, ep.disabled_reason = 0, ""
        ep.status = status
    return ep


def rotate(s: Session, ep: WebhookEndpoint, grace_hours: int = 24) -> str:
    if not (0 <= grace_hours <= 168):
        raise WebhookError("The overlap must be between 0 and 168 hours")
    secret = credentials.new_webhook_secret()
    ep.previous_secret, ep.previous_until = (ep.secret, now() + timedelta(hours=grace_hours)) if grace_hours else ("", None)
    ep.secret = credentials.seal(secret)
    return secret


def row(ep: WebhookEndpoint) -> dict:
    return {"id": ep.id, "url": ep.url, "description": ep.description, "event_types": ep.event_types, "status": ep.status,
            "disabled_reason": ep.disabled_reason or None, "created": ep.created.isoformat(), "created_by": ep.created_by,
            "rotating_until": ep.previous_until.isoformat() if ep.previous_until and ep.previous_until > now() else None}


def delivery_row(d: WebhookDelivery) -> dict:
    return {"id": d.id, "event_id": d.event_id, "event_type": d.event_type, "status": d.status, "attempts": d.attempts,
            "next_attempt": d.next_attempt.isoformat() if d.next_attempt else None, "last_code": d.last_code, "last_error": d.last_error or None,
            "created": d.created.isoformat(), "delivered": d.delivered.isoformat() if d.delivered else None}


def deliveries(s: Session, ep: WebhookEndpoint, status: str | None = None, limit: int = 50) -> list[WebhookDelivery]:
    q = select(WebhookDelivery).where(WebhookDelivery.endpoint_id == ep.id)
    if status:
        q = q.where(WebhookDelivery.status == status)
    return list(s.scalars(q.order_by(WebhookDelivery.created.desc(), WebhookDelivery.id.desc()).limit(limit)))


def retry(s: Session, ep: WebhookEndpoint, delivery_id: str) -> WebhookDelivery:
    d = s.get(WebhookDelivery, delivery_id)
    if d is None or d.endpoint_id != ep.id:
        raise WebhookError("No such delivery")
    d.status, d.next_attempt, d.attempts = "pending", now(), 0
    return d


def ping(s: Session, ep: WebhookEndpoint) -> WebhookDelivery:
    """Queue a webhook.test event to this endpoint alone, to check the receiver and its signature check."""
    import uuid

    event_id = str(uuid.uuid4())
    body = {"specversion": "1.0", "id": event_id, "type": "webhook.test", "source": "/caseforge/webhooks", "subject": ep.id,
            "time": now().isoformat() + "Z", "datacontenttype": "application/json", "data": {"endpoint_id": ep.id, "message": "Test delivery"}}
    d = WebhookDelivery(id=new_id("msg"), endpoint_id=ep.id, event_id=event_id, event_type="webhook.test",
                        payload=json.dumps(body, separators=(",", ":")), next_attempt=now())
    s.add(d)
    s.flush()
    return d


# --- the worker ---------------------------------------------------------------------------------------------------------

def owner_of(s: Session, ep: WebhookEndpoint) -> Principal | None:
    """Who the endpoint sees as, now. None when that caller no longer exists or may no longer subscribe."""
    rt = runtime(ep.tenant_id)
    if ep.client_id.startswith(APP_ACTOR):
        c = s.get(ApiClient, ep.client_id[len(APP_ACTOR):])
        if c is None or c.status != "active" or (c.expires and c.expires <= now()) or "webhooks:manage" not in (c.scopes or []):
            return None
        try:
            persona = app_persona(rt.policy, c.id, c.name, list(c.roles), list(c.queues))
        except PrincipalError:
            return None
        return Principal("app", rt, persona, frozenset(c.scopes or []), c.id)
    persona = rt.policy.people.get(ep.client_id)
    return None if persona is None else Principal("person", rt, persona, frozenset(catalogue.CATALOGUE))


def fan_out(s: Session, ep: WebhookEndpoint, owner: Principal, batch: int = 500, at: datetime | None = None) -> int:
    at = at or now()
    rows = list(s.scalars(select(OutboxEvent).where(OutboxEvent.id > ep.cursor).order_by(OutboxEvent.id).limit(batch)))
    if not rows:
        return 0
    queued = 0
    for r, env in events.visible(s, owner, rows):
        if events.wanted(env["type"], ep.event_types):
            s.add(WebhookDelivery(id=new_id("msg"), endpoint_id=ep.id, event_id=env["id"], event_type=env["type"],
                                  payload=json.dumps(env, separators=(",", ":"), default=str), next_attempt=at))
            queued += 1
    ep.cursor = rows[-1].id
    return queued


def _secrets(ep: WebhookEndpoint) -> list[str]:
    out = [credentials.unseal(ep.secret)]
    if ep.previous_secret and ep.previous_until and ep.previous_until > now():
        out.append(credentials.unseal(ep.previous_secret))
    return out


def deliver(s: Session, ep: WebhookEndpoint, d: WebhookDelivery, at: datetime | None = None) -> bool:
    at = at or now()
    body = d.payload.encode()
    d.attempts += 1
    try:
        code = transport(ep.url, body, signing.headers(_secrets(ep), d.id, body))
        ok, d.last_code, d.last_error = 200 <= code < 300, code, "" if 200 <= code < 300 else f"Receiver answered {code}"
    except netguard.UnsafeAddress as e:
        ok, d.last_code, d.last_error = False, None, str(e)[:300]
    except Exception as e:  # noqa: BLE001  any network failure is a failed attempt, never a crash of the worker
        ok, d.last_code, d.last_error = False, None, f"{type(e).__name__}: {e}"[:300]
    if ok:
        d.status, d.delivered, d.next_attempt, ep.failures = "delivered", at, None, 0
        return True
    ep.failures += 1
    if d.attempts >= len(BACKOFF):
        d.status, d.next_attempt = "failed", None
    else:
        d.next_attempt = at + timedelta(seconds=BACKOFF[d.attempts])
    if ep.failures >= DISABLE_AFTER and ep.status == "active":
        ep.status, ep.disabled_reason = "disabled", f"Switched off after {ep.failures} failed attempts in a row: {d.last_error}"
    return False


def run_tenant(s: Session, at: datetime | None = None, limit: int = 200) -> dict:
    at = at or now()
    stats = {"queued": 0, "delivered": 0, "failed": 0}
    eps = list(s.scalars(select(WebhookEndpoint).where(WebhookEndpoint.status == "active")))
    for ep in eps:
        owner = owner_of(s, ep)
        if owner is None:
            ep.status, ep.disabled_reason = "disabled", "Its owner no longer exists or may no longer receive events"
            continue
        stats["queued"] += fan_out(s, ep, owner, at=at)
    s.flush()
    by_id = {ep.id: ep for ep in eps if ep.status == "active"}
    if not by_id:
        return stats
    due = list(s.scalars(select(WebhookDelivery).where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt <= at,
                                                       WebhookDelivery.endpoint_id.in_(by_id)).order_by(WebhookDelivery.created).limit(limit)))
    for d in due:
        ep = by_id[d.endpoint_id]
        if ep.status != "active":
            continue
        stats["delivered" if deliver(s, ep, d, at) else "failed"] += 1
    return stats


def run_once(at: datetime | None = None) -> dict:
    """One pass over every live client: fan out new events, then send what is due."""
    total = {"queued": 0, "delivered": 0, "failed": 0}
    for rt in all_runtimes():
        if rt.status != "live":
            continue
        try:
            with session_scope(rt.id) as s:
                for k, v in run_tenant(s, at).items():
                    total[k] += v
        except UnknownTenant:
            continue
        except Exception:  # noqa: BLE001  one client's trouble must not stop delivery to the others
            log.exception("Webhook pass failed for %s", rt.id)
    return total
