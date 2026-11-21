"""Quality assurance sampling: a share of closed cases gets an independent second look.

A case type declares `qa: {sample_rate, dispositions, reopen_to}`. Sampling is a deterministic hash of the
case id, so the same case is always in or out and the rate can be audited.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy.orm import Session

from ..domain.models import Case
from ..domain.personas import Persona
from .case_types import load_case_types
from .service import ON_CLOSED, create_case, definition, enter_state, record

QA_CASE_TYPE = "qa_review"


def sampled(case_id: str, rate: float) -> bool:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < rate


def _sample_on_close(s: Session, case: Case, defn: dict, actor: Persona, ts: datetime) -> None:
    qa = defn.get("qa")
    if not qa or case.case_type == QA_CASE_TYPE or QA_CASE_TYPE not in load_case_types(case.tenant_id):
        return
    if qa.get("dispositions") and case.disposition not in qa["dispositions"]:
        return
    if (case.data or {}).get("qa_done") or not sampled(case.id, float(qa.get("sample_rate", 0))):
        return
    review = create_case(s, load_case_types(case.tenant_id)[QA_CASE_TYPE], case.party_id, "low", created=ts, parent_id=case.id, actor="qa-sampler")
    review.data = {"closed_by": actor.id, "closed_as": case.disposition}
    case.data = dict(case.data or {}) | {"qa_done": True}
    record(s, case.id, "qa-sampler", "system", "qa.sampled", {"review_case": review.id, "sample_rate": qa.get("sample_rate")}, ts=ts)


def _apply_qa_outcome(s: Session, review: Case, defn: dict, actor: Persona, ts: datetime) -> None:
    if review.case_type != QA_CASE_TYPE or not review.parent_id:
        return
    parent = s.get(Case, review.parent_id)
    if parent is None:
        return
    if review.disposition != "qa_failed":
        record(s, parent.id, actor.id, "human", "qa.passed", {"review_case": review.id}, ts=ts)
        return
    parent_defn = definition(parent)
    reopen_to = parent_defn.get("qa", {}).get("reopen_to")
    record(s, parent.id, actor.id, "human", "qa.failed", {"review_case": review.id, "reopen_to": reopen_to}, ts=ts)
    if reopen_to:
        previous = parent.disposition
        parent.closed = parent.disposition = None
        enter_state(s, parent, parent_defn, reopen_to, ts, actor=actor.id)
        record(s, parent.id, "workflow", "system", "case.reopened", {"to": reopen_to, "previous_disposition": previous}, ts=ts)


def install() -> None:
    for hook in (_sample_on_close, _apply_qa_outcome):
        if hook not in ON_CLOSED:
            ON_CLOSED.append(hook)
