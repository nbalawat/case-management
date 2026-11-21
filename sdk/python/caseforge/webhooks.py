"""Check a CaseForge webhook delivery (Standard Webhooks: HMAC-SHA256 over id, timestamp and the raw body)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

TOLERANCE = 300


def verify_webhook(secret: str, headers: dict, body: bytes, now: float | None = None) -> bool:
    """True when the delivery is genuine and fresh. Pass the raw body, before any JSON parsing."""
    h = {k.lower(): v for k, v in headers.items()}
    msg_id, ts, sigs = h.get("webhook-id", ""), h.get("webhook-timestamp", ""), h.get("webhook-signature", "")
    try:
        when = int(ts)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - when) > TOLERANCE:
        return False
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = "v1," + base64.b64encode(hmac.new(key, f"{msg_id}.{when}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, s) for s in sigs.split() if s.startswith("v1,"))
