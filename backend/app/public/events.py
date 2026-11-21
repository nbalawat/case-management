"""The event feed an API caller may see, shared by the /events endpoint and webhook delivery.

Events come from the transactional outbox (app/backbone/outbox.py), so the feed holds exactly what was committed,
in commit order, as CloudEvents 1.0. Each caller sees only:
  * events of their own client (the outbox is tenant-scoped like every table);
  * case events only with cases:read, and only for cases their roles see (restricted cases never reach an app);
  * alert events only with alerts:read.
"""
from __future__ import annotations

import fnmatch
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..backbone import topics
from ..config import settings
from ..domain.models import Case, OutboxEvent
from ..visibility import bind_viewer
from .principal import Principal

KNOWN = {
    "case.created": "A case was opened", "case.state_changed": "A case moved to another state", "case.assigned": "A case has a new owner",
    "case.released": "A case went back to its queue", "case.reopened": "A closed case was reopened", "case.merged_into": "A case was merged into another",
    "case.handed_off": "A case was handed to another team", "case.restricted": "A case was restricted (only cleared people see it from now on)",
    "task.completed": "A stage task was completed", "task.reopened": "A stage task was reopened", "narrative.saved": "The narrative was saved",
    "request.raised": "An information request was raised", "request.answered": "An information request was answered",
    "request.overdue": "An information request is overdue", "sla.stage_breached": "A stage clock ran out",
    "agent.recommended": "The investigation agent recommended an outcome", "sar.filed": "A suspicious activity report was filed",
    "qa.sampled": "A closed case was sampled for QA", "qa.passed": "QA passed a case", "qa.failed": "QA failed a case",
    "kyc.review_started": "A KYC review started", "kyc.trigger": "Something calls for a KYC review",
    "alert.created": "An alert was raised or accepted", "alert.attached": "An alert was added to a case",
}
CASE_TOPIC_SUFFIX, ALERT_TOPIC_SUFFIX = "." + topics.CASE_EVENTS, "." + topics.ALERTS


def public_type(stored: str) -> str:
    prefix = settings.event_topic_prefix + "."
    return stored[len(prefix):] if stored.startswith(prefix) else stored


def wanted(event_type: str, patterns: list[str] | None) -> bool:
    return not patterns or any(fnmatch.fnmatchcase(event_type, p) for p in patterns)


def check_patterns(patterns: list[str]) -> list[str]:
    out = []
    for p in patterns:
        p = str(p).strip()
        if not p or len(p) > 80 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789._*" for c in p):
            raise ValueError(f"Not an event type or pattern: {p!r}. Use names like case.state_changed or case.*")
        out.append(p)
    return sorted(set(out))


def visible(s: Session, p: Principal, rows: list[OutboxEvent]) -> list[tuple[OutboxEvent, dict]]:
    """The rows this caller may see, each with its envelope ready to send."""
    reads_cases, reads_alerts = "cases:read" in p.scopes, "alerts:read" in p.scopes
    case_rows = [r for r in rows if r.topic.endswith(CASE_TOPIC_SUFFIX)]
    seen: set[str] = set()
    if reads_cases and case_rows:
        bind_viewer(s, p.persona)  # the session may be shared with platform work; make sure the caller's view applies
        seen = set(s.scalars(select(Case.id).where(Case.id.in_({r.key for r in case_rows}))))
    out = []
    for r in rows:
        if r.topic.endswith(CASE_TOPIC_SUFFIX):
            if not (reads_cases and r.key in seen):
                continue
        elif r.topic.endswith(ALERT_TOPIC_SUFFIX):
            if not reads_alerts:
                continue
        else:
            continue
        env = json.loads(r.payload)
        env["type"] = public_type(env.get("type", r.type))
        env["sequence"] = r.id
        out.append((r, env))
    return out


def feed(s: Session, p: Principal, after: int, limit: int, patterns: list[str] | None = None) -> tuple[list[dict], int]:
    """Up to `limit` events after sequence `after`, and the sequence to continue from. Scans ahead past events the caller
    may not see, so a quiet caller still moves forward."""
    out: list[dict] = []
    cursor = after
    for _ in range(20):
        rows = list(s.scalars(select(OutboxEvent).where(OutboxEvent.id > cursor).order_by(OutboxEvent.id).limit(500)))
        if not rows:
            break
        for r, env in visible(s, p, rows):
            if wanted(env["type"], patterns):
                out.append(env)
                if len(out) == limit:
                    return out, r.id
        cursor = rows[-1].id
    return out, cursor
