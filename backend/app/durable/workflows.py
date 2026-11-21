"""Workflow code. Deterministic, and deliberately ignorant of the application: it holds timers and calls
activities by name. Anything that reads a clock or a database happens in an activity.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import RetryPolicy

STAGE_DUE, SWEEP = "stage_due", "sweep"  # activity names, registered by the worker
RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0, maximum_interval=timedelta(minutes=5))


def _when(iso: str) -> datetime:
    t = datetime.fromisoformat(iso)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)  # noqa: UP017  the application stores UTC without a zone


@workflow.defn(name="StageClock")
class StageClock:
    """One case's stage deadline. Waits until it is due, has the escalation applied, and ends.

    If the case moves to another stage first, a `stage` signal replaces the deadline and the wait starts again. A
    case that closes, or moves to a stage with no deadline, is discovered when the old deadline passes: the activity
    reports it and the workflow ends without doing anything.
    """

    def __init__(self) -> None:
        self._entered: str | None = None
        self._due: str | None = None
        self._fired: list[str] = []

    @workflow.run
    async def run(self, tenant_id: str, case_id: str, entered: str, due: str) -> str:
        if self._entered is None:  # a signal delivered with the start has already set them otherwise
            self._entered, self._due = entered, due
        while True:
            entered, due = self._entered, self._due
            wait = max(timedelta(0), _when(due) - workflow.now())
            try:
                await workflow.wait_condition(lambda: self._entered != entered, timeout=wait)  # noqa: B023
                continue  # the case changed stage: start again with the new deadline
            except asyncio.TimeoutError:  # noqa: UP041
                pass
            outcome = await workflow.execute_activity(STAGE_DUE, args=[tenant_id, case_id, entered], start_to_close_timeout=timedelta(seconds=60),
                                                      retry_policy=RETRY, result_type=str)
            self._fired.append(outcome)
            if self._entered == entered:  # nothing newer arrived while the activity ran
                return outcome

    @workflow.signal
    def stage(self, entered: str, due: str) -> None:
        if entered != self._entered:
            self._entered, self._due = entered, due

    @workflow.query
    def state(self) -> dict:
        return {"entered": self._entered, "due": self._due, "fired": list(self._fired)}


@workflow.defn(name="Sweep")
class Sweep:
    """The periodic sweep for every client. Started by a schedule."""

    @workflow.run
    async def run(self) -> dict:
        return await workflow.execute_activity(SWEEP, start_to_close_timeout=timedelta(minutes=10), retry_policy=RetryPolicy(maximum_attempts=2), result_type=dict)
