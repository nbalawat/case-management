"""Webhook signatures, following the Standard Webhooks specification (standardwebhooks.com).

Each delivery carries three headers:
    webhook-id         the delivery id; receivers drop a repeat by it
    webhook-timestamp  seconds since the epoch; receivers refuse anything older than a few minutes (replay)
    webhook-signature  space-separated "v1,<base64 HMAC-SHA256 of '{id}.{timestamp}.{body}'>" entries
During a secret rotation the payload is signed with both secrets, so the receiver can switch over at leisure.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from ..credentials import WEBHOOK_SECRET_PREFIX

TOLERANCE = 300


def _key(secret: str) -> bytes:
    return base64.b64decode(secret.removeprefix(WEBHOOK_SECRET_PREFIX))


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(_key(secret), f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def headers(secrets: list[str], msg_id: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    ts = int(timestamp if timestamp is not None else time.time())
    return {"webhook-id": msg_id, "webhook-timestamp": str(ts), "webhook-signature": " ".join(sign(x, msg_id, ts, body) for x in secrets),
            "content-type": "application/json", "user-agent": "CaseForge-Webhooks/1"}


def verify(secret: str, msg_id: str, timestamp: str, signature: str, body: bytes, now: float | None = None) -> bool:
    """What a receiver does. Shipped here so tests, the SDK and receivers agree on one definition."""
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > TOLERANCE:
        return False
    expected = sign(secret, msg_id, ts, body)
    return any(hmac.compare_digest(expected, s) for s in signature.split() if s.startswith("v1,"))
