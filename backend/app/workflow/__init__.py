"""Where a case's process runs.

A client configures each case type (stages, roles, tasks, approvals, deadlines) in the versioned workflow editor. That
configuration is interpreted in one place, `actions.apply`, whichever engine runs it. The engine decides only *where*
and *how durably* each step executes:

- `builtin` runs every step in the caller's own database transaction. Nothing to install; the laptop demo uses it.
- `temporal` runs each case as a durable Temporal workflow (`app.durable`): human steps arrive as workflow updates and
  are applied one at a time, deadlines are durable timers, side effects are retried activities, and every step a
  case took is in a replayable history. Each client can have a namespace of its own.

Code that changes a case on a person's behalf goes through `engine().perform(...)`, never straight to the service, so
the choice of engine is configuration (`WORKFLOW_ENGINE`), not code.
"""
from .actions import Action, CaseView, apply, view_of
from .engine import BuiltinEngine, EngineUnavailable, WorkflowEngine, engine, use
from . import observer  # noqa: F401,E402  registers the commit hooks

__all__ = ["Action", "BuiltinEngine", "CaseView", "EngineUnavailable", "WorkflowEngine", "apply", "engine", "use", "view_of"]
