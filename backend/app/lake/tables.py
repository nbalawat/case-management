"""What the lake keeps, and how a message from the bus becomes a row. Adding a table is adding an entry here."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from ..backbone import topics
from ..ingest.mapping import CANONICAL_FIELDS
from ..ingest.service import transform as canonical_transaction

# Column types are named plainly here and turned into the table format's own types by the adapter.
STRING, TIMESTAMP, DOUBLE = "string", "timestamp", "double"


@dataclass(frozen=True)
class LakeTable:
    name: str
    topic: str  # the kind of topic it is filled from
    columns: tuple[tuple[str, str, bool], ...]  # name, type, required
    time_column: str  # tables are partitioned by its day
    row: Callable[[bytes], dict]  # raises ValueError for a message that is not a row


def _time(iso: str) -> datetime:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def _event(value: bytes) -> dict:
    try:
        doc = json.loads(value)
    except ValueError:
        raise ValueError("not valid JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), dict) or not doc.get("id") or not doc.get("time"):
        raise ValueError("not a CloudEvent")
    return doc


def case_event_row(value: bytes) -> dict:
    e = _event(value)
    d = e["data"]
    return {"event_id": e["id"], "type": e.get("type", ""), "case_id": str(d.get("case_id", "")), "actor": str(d.get("actor", "")),
            "actor_kind": str(d.get("actor_kind", "")), "ts": _time(e["time"]), "payload": json.dumps(d.get("payload") or {}, sort_keys=True)}


def alert_row(value: bytes) -> dict:
    e = _event(value)
    d = e["data"]
    return {"event_id": e["id"], "alert_id": str(d.get("alert_id", "")), "source": str(d.get("source", "")), "scenario": str(d.get("scenario", "")),
            "scenario_version": str(d.get("scenario_version", "")), "party_id": str(d.get("party_id", "")), "score": float(d.get("score") or 0),
            "explanation": str(d.get("explanation", "")), "txn_ids": json.dumps(d.get("txn_ids") or []), "ts": _time(e["time"])}


def transaction_row(value: bytes) -> dict:
    """The canonical form of a transaction from the inbound stream.

    Malformed records are not kept. A well-formed one is kept as received even when the loader then sets it aside
    (a customer it does not know): the lake records what arrived, the operational store what was accepted.
    """
    try:
        doc = json.loads(value)
    except ValueError:
        raise ValueError("not valid JSON") from None
    if isinstance(doc, dict) and doc.get("specversion") and isinstance(doc.get("data"), dict):
        doc = doc["data"]
    if not isinstance(doc, dict):
        raise ValueError("not a JSON object")
    t = canonical_transaction({k: "" if v is None else str(v) for k, v in doc.items()}, {f: f for f in CANONICAL_FIELDS})
    return {k: t[k] for k in CANONICAL_FIELDS}


TABLES: dict[str, LakeTable] = {t.name: t for t in (
    LakeTable("case_events", topics.CASE_EVENTS, (("event_id", STRING, True), ("type", STRING, True), ("case_id", STRING, True), ("actor", STRING, False),
                                                  ("actor_kind", STRING, False), ("ts", TIMESTAMP, True), ("payload", STRING, False)), "ts", case_event_row),
    LakeTable("alerts", topics.ALERTS, (("event_id", STRING, True), ("alert_id", STRING, True), ("source", STRING, False), ("scenario", STRING, True),
                                        ("scenario_version", STRING, False), ("party_id", STRING, True), ("score", DOUBLE, False),
                                        ("explanation", STRING, False), ("txn_ids", STRING, False), ("ts", TIMESTAMP, True)), "ts", alert_row),
    LakeTable("transactions", topics.TRANSACTIONS, (("txn_id", STRING, True), ("account_id", STRING, True), ("party_id", STRING, True), ("ts", TIMESTAMP, True),
                                                    ("amount", DOUBLE, True), ("currency", STRING, False), ("direction", STRING, True), ("channel", STRING, True),
                                                    ("counterparty", STRING, False), ("counterparty_country", STRING, False)), "ts", transaction_row),
)}
BY_TOPIC = {t.topic: t for t in TABLES.values()}
