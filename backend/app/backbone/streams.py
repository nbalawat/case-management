"""Inbound streams: each client's transactions and third-party alerts, read from the bus.

A consumer reads a micro-batch, loads it through the same code the file pipelines use, commits the database, and only
then commits its position on the bus. Loading is idempotent (a transaction id or an engine's alert id seen before is
left alone), so the redelivery that follows a crash changes nothing. A record that can never load goes to the
client's dead-letter topic with the reason, and the stream carries on.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.investigator import investigate
from ..alerts.hub import ingest_external_alert, route_alerts
from ..config import settings
from ..db import session_scope
from ..detection.engine import run_detection
from ..domain.models import Alert, StreamStat, now
from ..ingest.datasets import load_transactions
from ..ingest.mapping import CANONICAL_FIELDS
from ..ingest.service import transform as canonical_transaction
from ..tenants import registry
from ..visibility import system_work
from . import topics
from .bus import EventBus, Message, Subscription

log = logging.getLogger("caseforge.backbone")
IDENTITY = {f: f for f in CANONICAL_FIELDS}
ALERT_REQUIRED = ("source", "external_id", "scenario", "party_id", "score", "explanation")


@dataclass
class Outcome:
    received: int = 0
    loaded: int = 0
    duplicates: int = 0
    alerts: int = 0
    rejects: list[tuple[Message, str]] = field(default_factory=list)


def _body(m: Message) -> dict:
    """The record in a message: plain JSON, or the data of a CloudEvent."""
    try:
        doc = json.loads(m.value)
    except ValueError:
        raise ValueError("not valid JSON") from None
    if isinstance(doc, dict) and doc.get("specversion") and isinstance(doc.get("data"), dict):
        doc = doc["data"]
    if not isinstance(doc, dict):
        raise ValueError("not a JSON object")
    return doc


@system_work
def load_transaction_batch(s: Session, messages: list[Message]) -> Outcome:
    out = Outcome(received=len(messages))
    rows: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for n, m in enumerate(messages):
        try:
            values = canonical_transaction({k: "" if v is None else str(v) for k, v in _body(m).items()}, IDENTITY)
        except (ValueError, KeyError) as e:
            out.rejects.append((m, str(e)))
            continue
        if values["txn_id"] in seen:  # the same payment twice in one batch
            out.duplicates += 1
            continue
        seen.add(values["txn_id"])
        rows.append((n, values))
    result = load_transactions(s, rows, origin="stream", rules={"unknown_customer": settings.stream_unknown_customer})
    out.loaded, out.duplicates = result.inserted, out.duplicates + result.unchanged
    out.rejects += [(messages[n], reason) for n, reason in result.rejects]
    if result.parties:
        alerts = run_detection(s, result.parties)
        for c in route_alerts(s, alerts):
            investigate(s, c)
        out.alerts = len(alerts)
    return out


@system_work
def load_alert_batch(s: Session, messages: list[Message]) -> Outcome:
    out = Outcome(received=len(messages))
    for m in messages:
        try:
            a = _body(m)
            missing = [k for k in ALERT_REQUIRED if a.get(k) in (None, "")]
            if missing:
                raise ValueError(f"missing {', '.join(missing)}")
            score = float(a["score"])
            if not 0 <= score <= 1:
                raise ValueError("score must be between 0 and 1")
            if len(str(a["source"])) > 40 or len(str(a["external_id"])) > 70 or len(str(a["scenario"])) > 60 or len(str(a["party_id"])) > 40:
                raise ValueError("an identifier is longer than the alert format allows")
        except (ValueError, TypeError) as e:
            out.rejects.append((m, str(e)))
            continue
        key = f"ext:{a['source']}:{a['external_id']}"
        if s.scalars(select(Alert.id).where(Alert.dedupe_key == key)).first() is not None:
            out.duplicates += 1
            continue
        _alert, case = ingest_external_alert(
            s, source=str(a["source"]), external_id=str(a["external_id"]), scenario=str(a["scenario"]), party_id=str(a["party_id"]), score=score,
            explanation=str(a["explanation"]), party_name=a.get("party_name"), txn_ids=[str(x) for x in a.get("txn_ids") or []])
        if case is not None:
            investigate(s, case)
        out.loaded += 1
        out.alerts += 1
    return out


HANDLERS: dict[str, Callable[[Session, list[Message]], Outcome]] = {topics.TRANSACTIONS: load_transaction_batch, topics.ALERTS_IN: load_alert_batch}


def _count(s: Session, stream: str, out: Outcome) -> None:
    stat = s.scalars(select(StreamStat).where(StreamStat.stream == stream)).first()
    if stat is None:
        stat = StreamStat(stream=stream, received=0, loaded=0, duplicates=0, rejected=0, alerts=0)
        s.add(stat)
    stat.received += out.received
    stat.loaded += out.loaded
    stat.duplicates += out.duplicates
    stat.rejected += len(out.rejects)
    stat.alerts += out.alerts
    stat.last_at = now()
    if out.rejects:
        stat.last_reject = out.rejects[-1][1][:300]


def dead_letter(m: Message, tenant_id: str, reason: str) -> Message:
    return Message(topics.topic(tenant_id, topics.DEAD_LETTER), m.key, m.value,
                   m.headers | {"dl_reason": reason[:300], "dl_topic": m.topic, "dl_partition": str(m.partition), "dl_offset": str(m.offset)})


def _live() -> set[str]:
    return {rt.id for rt in registry.all_runtimes() if rt.status == "live"}


class StreamConsumer:
    """One stream kind across every client. Call `step` in a loop; each step is one micro-batch."""

    def __init__(self, bus: EventBus, stream: str, group: str | None = None, refresh: Callable[[], None] | None = None) -> None:
        self.bus, self.stream = bus, stream
        self.refresh = refresh  # a process that is not the API reloads its list of clients through this
        self.group = group or f"{settings.event_topic_prefix}-{stream}"
        self._sub: Subscription | None = None

    def step(self, max_messages: int = 500, timeout: float = 1.0) -> dict[str, Outcome]:
        if self._sub is None:
            self._sub = self.bus.subscribe(self.group, topics.pattern(self.stream))
        try:
            return self._step(self._sub.poll(max_messages, timeout))
        except Exception:
            # Nothing was committed on the bus, so drop the subscription: the next step starts again from the last commit.
            sub, self._sub = self._sub, None
            sub.close()
            raise

    def _step(self, messages: list[Message]) -> dict[str, Outcome]:
        if not messages:
            return {}
        by_tenant: dict[str, list[Message]] = defaultdict(list)
        for m in messages:
            parsed = topics.parse(m.topic)
            if parsed:
                by_tenant[parsed[0]].append(m)
        live = _live()
        if self.refresh is not None and set(by_tenant) - live:  # perhaps a client onboarded since this process last looked
            self.refresh()
            live = _live()
        done: dict[str, Outcome] = {}
        for tenant_id, batch in by_tenant.items():
            if tenant_id not in live:
                # Not lost: the messages wait on the dead-letter topic and can be replayed once the client is live.
                log.warning("Set aside %d message(s) on %s: no live client '%s'", len(batch), batch[0].topic, tenant_id)
                self.bus.publish(dead_letter(m, tenant_id, "this client is not live") for m in batch)
                done[tenant_id] = Outcome(received=len(batch), rejects=[(m, "this client is not live") for m in batch])
                continue
            with session_scope(tenant_id) as s:
                out = HANDLERS[self.stream](s, batch)
                _count(s, self.stream, out)
            if out.rejects:  # after the database commit, before the position moves: a crash here repeats them, never drops them
                self.bus.publish(dead_letter(m, tenant_id, reason) for m, reason in out.rejects)
            done[tenant_id] = out
        self._sub.commit()
        return done

    def close(self) -> None:
        if self._sub is not None:
            self._sub.close()
            self._sub = None
