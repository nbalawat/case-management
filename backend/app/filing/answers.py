"""Reading the regulator's answers back in: accepted with a number, or rejected with a reason.

A client's secure transfer drops each answer it collects from the regulator into its landing storage under
`regulatory/inbox/`. This reads every answer file not seen before and records it on the report it is about, through
the same acknowledge and reject steps a person uses, so the history reads the same either way. Run it on a schedule
(`collect` is the entry point) or from the Regulatory reports page.

Answer files are in a neutral form, one JSON object per line:
    {"filing_id": "sar-...", "status": "accepted", "reference": "31000123456789"}
    {"receipt": "regulatory/outbox/sar/sar-....xml", "status": "rejected", "reason": "Subject TIN missing"}
Turning a regulator's own acknowledgement format into these lines is the job of the client's transfer, or of an
adapter added here per regulator. FinCEN's and goAML's native acknowledgement files have not been parsed yet.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Filing, IngestedObject, new_id, now
from ..domain.personas import Persona
from ..tenancy import tenant_of
from ..visibility import system_work
from . import service

INBOX = "regulatory/inbox/"
SYSTEM = Persona("system:regulator-inbox", "Regulator inbox", "system", "Answers collected on a schedule")
SOURCE = "regulator-answers"  # how answer files are marked in the ingested-objects ledger, so each is read once


class AnswerError(ValueError):
    pass


def _find(s: Session, line: dict) -> Filing | None:
    if line.get("filing_id"):
        return s.scalars(select(Filing).where(Filing.id == str(line["filing_id"]))).first()
    if line.get("receipt"):
        return s.scalars(select(Filing).where(Filing.receipt == str(line["receipt"]))).first()
    return None


def apply(s: Session, line: dict, persona: Persona, ts: datetime | None = None) -> dict:
    """Record one answer. Returns what happened, in words, for the run's summary."""
    f = _find(s, line)
    if f is None:
        return {"ok": False, "why": "no report matches this answer"}
    status = str(line.get("status") or "").lower()
    try:
        if status in ("accepted", "acknowledged"):
            service.acknowledge(s, f.id, str(line.get("reference") or ""), persona, ts)
        elif status == "rejected":
            service.reject(s, f.id, str(line.get("reason") or "The regulator gave no reason"), persona, ts)
        else:
            return {"ok": False, "filing_id": f.id, "why": f"unknown status {status!r}"}
    except service.FilingError as e:
        return {"ok": False, "filing_id": f.id, "why": str(e)}
    return {"ok": True, "filing_id": f.id, "status": f.status}


@system_work
def collect(s: Session, persona: Persona, ts: datetime | None = None) -> dict:
    """Read every answer file in the client's inbox that has not been read before."""
    from ..ingest.locations import store_for
    from ..storage.base import StorageError

    ts = ts or now()
    try:
        store = store_for(tenant_of(s), "landing")
        objects = list(store.list(INBOX))
    except (StorageError, KeyError) as e:
        raise AnswerError(f"The client's inbox cannot be read: {e}") from None
    seen = set(s.scalars(select(IngestedObject.fingerprint).where(IngestedObject.source_id == SOURCE)))
    out = {"files": 0, "answers": 0, "recorded": 0, "problems": []}
    for obj in objects:
        with store.open(obj.key) as fh:
            data = fh.read()
        fingerprint = hashlib.sha256(obj.key.encode() + b"\0" + data).hexdigest()
        if fingerprint in seen:
            continue
        out["files"] += 1
        for n, raw in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if not raw.strip():
                continue
            out["answers"] += 1
            try:
                line = json.loads(raw)
                result = apply(s, line, persona, ts) if isinstance(line, dict) else {"ok": False, "why": "not an object"}
            except json.JSONDecodeError:
                result = {"ok": False, "why": "not JSON"}
            if result["ok"]:
                out["recorded"] += 1
            else:
                out["problems"].append({"file": obj.key, "line": n, **result})
        s.add(IngestedObject(id=new_id("obj"), source_id=SOURCE, run_id=f"answers-{ts:%Y%m%d%H%M%S}", key=obj.key[:300], fingerprint=fingerprint,
                             status="loaded", stats={"answers": out["answers"]}))
        s.flush()
    return out


def collect_for(tenant_id: str, ts: datetime | None = None) -> dict:
    """One client's inbox, read on a schedule (Temporal's per-client schedule, or the in-process scheduler).

    A client with no landing storage, or whose inbox cannot be read right now, is skipped with the reason rather
    than failing: the next pass tries again, and the Regulatory reports page shows the same reason when run by hand.
    """
    from ..db import session_scope

    with session_scope(tenant_id) as s:
        try:
            out = collect(s, SYSTEM, ts)
        except AnswerError as e:
            s.rollback()
            return {"skipped": str(e)}
        s.commit()
        return out
