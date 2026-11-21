"""The steps a person can take on a case, and the one function that applies them.

This is the translation layer between a client's configured case type and whatever engine runs it. It knows nothing
about engines: the built-in engine calls `apply` in the caller's transaction, the Temporal engine calls it inside an
activity. Either way the same guards (roles, four-eyes, required tasks, notes, reasons, conditions) run in the same order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from ..cases.service import TransitionError, complete_stage_tasks, definition, record, set_task, stage_due, transition
from ..domain.models import Case
from ..domain.personas import Persona

Kind = Literal["transition", "task"]


@dataclass(frozen=True)
class Action:
    """One step on one case, as plain data, so it can travel to a workflow and be kept in its history."""

    kind: Kind
    persona_id: str
    to: str = ""            # transition: the stage to move to
    note: str = ""
    reason: str = ""
    tick_tasks: bool = False  # transition: complete the stage's open tasks first, as a bulk step may
    task_id: str = ""       # task: which one
    done: bool = True       # task: ticked or unticked
    batch: dict = field(default_factory=dict)  # a bulk step: {"batch": id, "of": n}, recorded with the step
    step_id: str = ""       # set by an engine that may retry, so a step is never taken twice

    def __post_init__(self) -> None:
        if self.kind not in ("transition", "task"):
            raise TransitionError(f"Unknown step '{self.kind}'")
        if self.kind == "transition" and not self.to:
            raise TransitionError("Say which stage to move to")
        if self.kind == "task" and not self.task_id:
            raise TransitionError("Say which task")

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Action:
        return cls(**d)


@dataclass(frozen=True)
class CaseView:
    """What an engine needs to know about where a case is: its stage, when it entered it, when that is due, and whether it is closed."""

    case_id: str
    state: str
    entered: str          # ISO-8601; names this visit to the stage
    due: str | None       # ISO-8601, or None when the stage has no deadline
    closed: bool
    breached: bool = False  # the escalation for this visit has been applied

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CaseView:
        return cls(**d)


def view_of(case: Case) -> CaseView:
    due = stage_due(case, definition(case))
    return CaseView(case.id, case.state, case.state_entered.isoformat(), due.isoformat() if due else None, case.closed is not None,
                    (case.data or {}).get("stage_breach") == case.state_entered.isoformat())


def apply(s: Session, case: Case, persona: Persona, action: Action) -> CaseView:
    """Take one step, with every check the case type declares. Raises TransitionError, leaving the case as it was, when a check fails."""
    if action.persona_id != persona.id:
        raise TransitionError("The step names a different person from the one taking it")
    point = s.begin_nested()  # all of the step or none of it: ticked tasks go with a refused transition
    try:
        if action.kind == "task":
            set_task(s, case, action.task_id, persona, action.done)
        else:
            if action.tick_tasks:
                complete_stage_tasks(s, case, persona)
            transition(s, case, action.to, persona, action.note, reason=action.reason)
        if action.batch:
            record(s, case.id, persona.id, "human", "bulk.applied", {"batch": action.batch["batch"], "action": "transition", "of": action.batch["of"]})
        s.flush()
        point.commit()
    except Exception:
        point.rollback()
        s.expire(case)
        raise
    return view_of(case)
