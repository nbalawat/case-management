"""Topic names. Each client has its own topics, so access, retention and encryption can be set per client."""
from __future__ import annotations

import re

from ..config import settings

CASE_EVENTS = "case-events"   # out: everything on a case's audit trail
ALERTS = "alerts"             # out: every alert this platform raises or accepts
TRANSACTIONS = "transactions"  # in: canonical transactions
ALERTS_IN = "alerts-in"       # in: alerts from another monitoring or screening engine
DEAD_LETTER = "dead-letter"   # in-side rejects, each with the reason in its headers
STREAMS = (TRANSACTIONS, ALERTS_IN)
ALL = (CASE_EVENTS, ALERTS, TRANSACTIONS, ALERTS_IN, DEAD_LETTER)


def topic(tenant_id: str, name: str) -> str:
    return f"{settings.event_topic_prefix}.{tenant_id}.{name}"


def pattern(name: str) -> str:
    """Every client's topic of one kind, as a regular expression. A client onboarded later is picked up without a restart."""
    return rf"^{re.escape(settings.event_topic_prefix)}\.[^.]+\.{re.escape(name)}$"


def parse(full: str) -> tuple[str, str] | None:
    """(client, kind) from a topic name, or None when it is not one of ours."""
    prefix = settings.event_topic_prefix + "."
    if not full.startswith(prefix):
        return None
    tenant, _, name = full[len(prefix):].partition(".")
    return (tenant, name) if tenant and name in ALL else None
