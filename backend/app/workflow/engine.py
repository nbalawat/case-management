"""The workflow-engine interface, the built-in engine, and the choice between engines.

    WORKFLOW_ENGINE=builtin    every step in the caller's transaction (the default without Temporal)
    WORKFLOW_ENGINE=temporal   every case a durable Temporal workflow (the default when TEMPORAL_URL is set)
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from ..config import settings
from ..domain.models import Case
from ..domain.personas import Persona
from .actions import Action, CaseView, apply, view_of


class EngineUnavailable(RuntimeError):
    """The engine that runs case workflows could not be reached. Nothing was changed."""


@runtime_checkable
class WorkflowEngine(Protocol):
    name: str
    in_process: bool  # True when `perform` works inside the caller's transaction (and so inside a savepoint)

    def perform(self, s: Session, case: Case, persona: Persona, action: Action) -> CaseView:
        """Take a person's step on a case. Raises TransitionError when the case type refuses it, EngineUnavailable when nothing could run it."""

    def observe(self, tenant_id: str, case_ids: Iterable[str]) -> None:
        """Cases were opened or moved by something other than `perform` (the alert hub, a merge, QA reopening a case). Called after commit."""

    def observe_filings(self, tenant_id: str, filing_ids: Iterable[str]) -> None:
        """Reports to a regulator were sent, answered or replaced. Called after commit."""

    def run_job(self, s: Session, name: str, params: dict, persona: Persona | None) -> dict:
        """Run a registered job (app.workflow.jobs) and return its result. A background job is only started, after commit."""

    def brief(self, s: Session, case: Case) -> None:
        """Have the agents a case type declares brief this case: at once on the built-in engine, durably and retried on Temporal."""

    def start_jobs(self, items: list[tuple[str, str, dict, str | None]]) -> None:
        """Background jobs queued in a transaction that has just committed: (tenant, name, params, persona id)."""

    def status(self, tenant_id: str) -> dict:
        """Where this client's case workflows run, for the operator and the Integrations page. Never raises."""


class BuiltinEngine:
    """Runs each step at once, in the caller's transaction. Deadlines are checked by the sweep."""

    name, in_process = "builtin", True

    def perform(self, s: Session, case: Case, persona: Persona, action: Action) -> CaseView:
        return apply(s, case, persona, action)

    def observe(self, tenant_id: str, case_ids: Iterable[str]) -> None:
        return None  # nothing to keep in step: the sweep reads the database

    def observe_filings(self, tenant_id: str, filing_ids: Iterable[str]) -> None:
        return None

    def run_job(self, s: Session, name: str, params: dict, persona: Persona | None) -> dict:
        from .jobs import run_inline

        return run_inline(s, name, params, persona)

    def brief(self, s: Session, case: Case) -> None:
        from ..agents.investigator import investigate

        investigate(s, case)

    def start_jobs(self, items: list[tuple[str, str, dict, str | None]]) -> None:
        return None

    def status(self, tenant_id: str) -> dict:
        return {"engine": "builtin", "connected": False, "clocks": None, "workflows": None, "namespace": None,
                "note": "Case workflows run inside the application. Stage clocks are checked by the sweep, when it is asked for and when work lists load."}


_current: WorkflowEngine | None = None


def choose() -> str:
    wanted = (settings.workflow_engine or ("temporal" if settings.temporal_url else "builtin")).lower()
    if wanted not in ("builtin", "temporal"):
        raise ValueError("WORKFLOW_ENGINE must be builtin or temporal")
    return wanted


def engine() -> WorkflowEngine:
    """The engine this process runs case workflows on, made on first use."""
    global _current
    if _current is None:
        if choose() == "temporal":
            from ..durable.engine import TemporalEngine

            _current = TemporalEngine()
        else:
            _current = BuiltinEngine()
    return _current


def use(e: WorkflowEngine | None) -> WorkflowEngine | None:
    """Swap the engine (tests, and the worker). Returns the one it replaced. None goes back to choosing from settings."""
    global _current
    old, _current = _current, e
    return old


__all__ = ["Action", "BuiltinEngine", "CaseView", "EngineUnavailable", "WorkflowEngine", "engine", "use", "view_of"]
