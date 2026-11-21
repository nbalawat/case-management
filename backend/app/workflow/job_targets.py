"""The functions behind the registered jobs (app.workflow.jobs). Each returns plain data: ids, not database objects."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.personas import Persona


def lookback_run(s: Session, persona: Persona, lookback_id: str) -> dict:
    from ..detection import lookbacks

    return {"lookback_id": lookbacks.run(s, lookback_id, persona).id}


def agents_lookback(s: Session, persona: Persona, days: int = 90, case_type: str | None = None) -> dict:
    from ..agents import batch

    return {"job_id": batch.lookback(s, persona.id, days, case_type).id}


def agents_tuning(s: Session, persona: Persona) -> dict:
    from ..agents import batch

    return {"job_id": batch.tuning(s, persona.id).id}


def filing_submit(s: Session, persona: Persona, filing_id: str) -> dict:
    from ..filing import service

    return {"filing_id": service.submit(s, filing_id, persona).id}


def agents_brief(s: Session, persona: Persona | None, case_id: str, token: str = "") -> dict:
    """The briefing a case type declares, run once per request for it (`token`), on an open case."""
    from sqlalchemy import select

    from ..agents.investigator import investigate
    from ..domain.models import Case
    from ..visibility import system

    with system(s):
        case = s.scalars(select(Case).where(Case.id == case_id)).first()
        if case is None:
            return {"case_id": case_id, "run_id": None, "skipped": "gone"}
        if token and (case.data or {}).get("brief_token") == token:
            return {"case_id": case_id, "run_id": None, "skipped": "done"}  # a retry after the briefing was committed
        run = investigate(s, case)
        if token:
            case.data = dict(case.data or {}) | {"brief_token": token}
        s.flush()
        return {"case_id": case_id, "run_id": run.id}
