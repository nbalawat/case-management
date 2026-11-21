"""Workflow code. Deterministic, and deliberately ignorant of the application: it keeps state, holds timers and calls
activities by name. Anything that reads a clock, a database or a case type happens in an activity.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

# Activity names, registered by the worker (app.durable.temporal.activities)
LOOK, PERFORM, STAGE_DUE, SWEEP, PIPELINES = "case_look", "case_perform", "stage_due", "sweep", "pipelines_due"
FILING_LOOK, FILING_DUE = "filing_look", "filing_follow_up"
REFUSED = "TransitionError"  # the application error type of a step the case type does not allow
RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0, maximum_interval=timedelta(minutes=5))
# A person is waiting on a step: retry quickly, a few times, and never retry a refusal
STEP_RETRY = RetryPolicy(initial_interval=timedelta(milliseconds=200), maximum_interval=timedelta(seconds=2), maximum_attempts=5,
                         non_retryable_error_types=[REFUSED])


def _when(iso: str) -> datetime:
    t = datetime.fromisoformat(iso)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)  # noqa: UP017  the application stores UTC without a zone


@workflow.defn(name="CaseWorkflow")
class CaseWorkflow:
    """One case, from the moment it opens until it closes.

    - People's steps (`perform` updates) are applied one at a time, in the order they arrive, each by an activity
      that runs the case type's checks. A refused step fails the update and changes nothing; two people pressing
      Approve at once can no longer both succeed.
    - The current stage's deadline is a durable timer. When it passes the escalation is applied, once.
    - Anything else that moves the case (the alert hub, a merge, QA reopening it) sends `observed`; the workflow then
      reads where the case is. It never trusts a signal's word for it.
    - The case closing ends the workflow. Reopening a closed case starts a new run under the same id.
    """

    @workflow.init
    def __init__(self, tenant_id: str, case_id: str) -> None:
        self._tenant, self._case = tenant_id, case_id
        self._view: dict | None = None
        self._stale = True  # read the case before doing anything
        self._lock = asyncio.Lock()
        self._steps = 0
        self._log: list[dict] = []

    @workflow.run
    async def run(self, tenant_id: str, case_id: str) -> dict:
        while True:
            if self._stale:
                async with self._lock:
                    if self._stale:
                        await self._look()
            view = self._view
            if view["closed"]:
                if self._stale:  # told to look again while it was reading the case (a reopening): look before ending
                    continue
                break
            wait = None if not view["due"] or view["breached"] else max(timedelta(0), _when(view["due"]) - workflow.now())
            try:
                await workflow.wait_condition(lambda: self._stale or self._view is not view, timeout=wait)  # noqa: B023
            except asyncio.TimeoutError:  # noqa: UP041
                outcome = ""
                async with self._lock:
                    if self._view is view and not self._stale:  # nothing moved the case while the timer ran out
                        outcome = await workflow.execute_activity(STAGE_DUE, args=[self._tenant, self._case, view["entered"]],
                                                                  start_to_close_timeout=timedelta(seconds=60), retry_policy=RETRY, result_type=str)
                        self._note("deadline", outcome, view["state"])
                        await self._look()
                if outcome == "not_due":  # the database's clock is behind this one: ask again shortly, not in a tight loop
                    await asyncio.sleep(5)
            if workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                workflow.continue_as_new(args=[self._tenant, self._case])
        await workflow.wait_condition(workflow.all_handlers_finished)
        return {"case_id": self._case, "state": self._view.get("state"), "steps": self._steps, "gone": bool(self._view.get("gone"))}

    async def _look(self) -> None:
        self._stale = False
        self._view = await workflow.execute_activity(LOOK, args=[self._tenant, self._case], start_to_close_timeout=timedelta(seconds=30),
                                                     retry_policy=RETRY, result_type=dict)

    def _note(self, what: str, outcome: str, state: str) -> None:
        self._log = [*self._log[-49:], {"at": workflow.now().isoformat(), "what": what, "outcome": outcome, "state": state}]

    @workflow.update
    async def perform(self, action: dict) -> dict:
        async with self._lock:
            try:
                view = await workflow.execute_activity(PERFORM, args=[self._tenant, self._case, action], start_to_close_timeout=timedelta(seconds=30),
                                                       retry_policy=STEP_RETRY, result_type=dict)
            except ActivityError as e:
                cause = e.cause
                refused = isinstance(cause, ApplicationError) and cause.type == REFUSED
                self._note(action.get("kind", "step"), "refused" if refused else "failed", (self._view or {}).get("state", ""))
                if refused:
                    raise ApplicationError(cause.message, type=REFUSED, non_retryable=True) from None
                raise ApplicationError("The step could not be applied", type="StepFailed") from None
            self._steps += 1
            self._view, self._stale = view, False
            self._note(action.get("kind", "step"), "done", view["state"])
            return view

    @workflow.signal
    def observed(self) -> None:
        self._stale = True

    @workflow.query
    def state(self) -> dict:
        return {"tenant": self._tenant, "case_id": self._case, "view": self._view, "steps": self._steps, "log": list(self._log)}


@workflow.defn(name="FilingWorkflow")
class FilingWorkflow:
    """One report to a regulator, from the moment it is sent until nothing more is owed on it.

    It holds a durable timer for the next follow-up (the regulator's answer being overdue, then, for an accepted
    suspicious-activity report, the continuing-activity review), has it applied when it falls due, and ends when no
    follow-up is left: answered and reviewed, rejected (a correction is a new report, with a workflow of its own) or
    superseded. A person recording the regulator's answer sends `observed`.
    """

    @workflow.init
    def __init__(self, tenant_id: str, filing_id: str) -> None:
        self._tenant, self._filing = tenant_id, filing_id
        self._view: dict = {}
        self._stale = True
        self._done: list[dict] = []

    @workflow.run
    async def run(self, tenant_id: str, filing_id: str) -> dict:
        while True:
            if self._stale:
                self._stale = False
                self._view = await workflow.execute_activity(FILING_LOOK, args=[self._tenant, self._filing], start_to_close_timeout=timedelta(seconds=30),
                                                             retry_policy=RETRY, result_type=dict)
            view = self._view
            if not view.get("next"):
                if self._stale:  # an answer was recorded while it was reading the report: look again before ending
                    continue
                break
            try:
                await workflow.wait_condition(lambda: self._stale, timeout=max(timedelta(0), _when(view["due"]) - workflow.now()))
            except asyncio.TimeoutError:  # noqa: UP041
                applied = await workflow.execute_activity(FILING_DUE, args=[self._tenant, self._filing], start_to_close_timeout=timedelta(seconds=60),
                                                          retry_policy=RETRY, result_type=list)
                self._done.extend({"at": workflow.now().isoformat(), "what": w} for w in applied)
                self._stale = True
                if not applied:  # not due by the database's clock yet: look again shortly, not in a tight loop
                    await asyncio.sleep(5)
        return {"filing_id": self._filing, "status": self._view.get("status"), "followed_up": [d["what"] for d in self._done]}

    @workflow.signal
    def observed(self) -> None:
        self._stale = True

    @workflow.query
    def state(self) -> dict:
        return {"tenant": self._tenant, "filing_id": self._filing, "view": self._view, "done": list(self._done)}


@workflow.defn(name="Sweep")
class Sweep:
    """The periodic sweep for one client (overdue requests, expiring alerts, KYC triggers, holds, dispute deadlines). Started by a schedule."""

    @workflow.run
    async def run(self, tenant_id: str) -> dict:
        return await workflow.execute_activity(SWEEP, args=[tenant_id], start_to_close_timeout=timedelta(minutes=10),
                                               retry_policy=RetryPolicy(maximum_attempts=2), result_type=dict)


@workflow.defn(name="PipelineRuns")
class PipelineRuns:
    """Start one client's data pipelines that are due. Started by a schedule; one at a time, however many workers run."""

    @workflow.run
    async def run(self, tenant_id: str) -> dict:
        return await workflow.execute_activity(PIPELINES, args=[tenant_id], start_to_close_timeout=timedelta(minutes=30),
                                               retry_policy=RetryPolicy(maximum_attempts=3), result_type=dict)


@workflow.defn(name="StageClock")
class StageClock:
    """Kept so that clocks started before case workflows existed can finish. Nothing starts new ones."""

    def __init__(self) -> None:
        self._entered: str | None = None
        self._due: str | None = None

    @workflow.run
    async def run(self, tenant_id: str, case_id: str, entered: str, due: str) -> str:
        if self._entered is None:
            self._entered, self._due = entered, due
        while True:
            entered, due = self._entered, self._due
            try:
                await workflow.wait_condition(lambda: self._entered != entered, timeout=max(timedelta(0), _when(due) - workflow.now()))  # noqa: B023
                continue
            except asyncio.TimeoutError:  # noqa: UP041
                pass
            outcome = await workflow.execute_activity(STAGE_DUE, args=[tenant_id, case_id, entered], start_to_close_timeout=timedelta(seconds=60),
                                                      retry_policy=RETRY, result_type=str)
            if self._entered == entered:
                return outcome

    @workflow.signal
    def stage(self, entered: str, due: str) -> None:
        if entered != self._entered:
            self._entered, self._due = entered, due


WORKFLOWS = [CaseWorkflow, FilingWorkflow, Sweep, PipelineRuns, StageClock]
