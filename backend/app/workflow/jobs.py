"""Durable jobs: a piece of work started by a person or by the platform, run by whichever engine is configured.

A job is a named function `(session, persona, **params) -> dict` registered here. The built-in engine calls it in the
caller's transaction. The Temporal engine runs it as a `JobWorkflow`, in an activity with retries, so a batch that
dies halfway (a worker restarting, a model gateway timing out) is tried again instead of being lost; the caller
still waits for the result, so nothing it sees changes. A job's `key` names one run: a second request for the same
key while it runs waits for that run instead of starting another (two people pressing Run on one lookback).

Refusals (the rules saying no) are never retried and reach the caller as the same exception the function raised.
"""
from __future__ import annotations

import importlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..domain.personas import Persona


class UnknownJob(KeyError):
    pass


class UnknownPerson(LookupError):
    """The job names a person who is not (or no longer) a user of the client."""


@dataclass(frozen=True)
class Job:
    name: str
    target: str                 # "module:function", imported when the job runs, so registering costs nothing
    refusals: tuple[str, ...]   # "module:Exception" names: the rules saying no, never retried
    key: Callable[[dict], str]  # which run this is; equal keys share one run
    attempts: int = 3
    timeout_seconds: int = 900
    background: bool = False    # True: the caller does not wait for it (briefings on new cases)
    retry_on: tuple[str, ...] = ()  # refusal subclasses that mean "try again" (a channel that stumbled), if the module has them

    def function(self) -> Callable[..., dict]:
        module, fn = self.target.split(":")
        return getattr(importlib.import_module(module), fn)

    def refusal_types(self) -> tuple[type[BaseException], ...]:
        return _types(self.refusals)

    def retry_types(self) -> tuple[type[BaseException], ...]:
        return _types(self.retry_on)


def _types(names: tuple[str, ...]) -> tuple[type[BaseException], ...]:
    out = []
    for name in names:
        module, cls = name.split(":")
        found = getattr(importlib.import_module(module), cls, None)
        if found is not None:
            out.append(found)
    return tuple(out)


def _fresh(_: dict) -> str:
    return uuid.uuid4().hex[:12]


JOBS: dict[str, Job] = {j.name: j for j in (
    Job("lookback.run", "app.workflow.job_targets:lookback_run", ("app.detection.lookbacks:LookbackError",),
        key=lambda p: p["lookback_id"]),
    Job("agents.lookback", "app.workflow.job_targets:agents_lookback", ("app.agents.batch:BatchError",), key=_fresh),
    Job("agents.tuning", "app.workflow.job_targets:agents_tuning", ("app.agents.batch:BatchError",), key=_fresh),
    Job("filing.submit", "app.workflow.job_targets:filing_submit", ("app.filing.service:FilingError",), key=lambda p: p["filing_id"],
        attempts=4, timeout_seconds=120, retry_on=("app.filing.service:SendFailed",)),
    Job("agents.brief", "app.workflow.job_targets:agents_brief", (), key=lambda p: p["case_id"], attempts=5, timeout_seconds=600, background=True),
)}


def get(name: str) -> Job:
    if name not in JOBS:
        raise UnknownJob(name)
    return JOBS[name]


def run_inline(s: Session, name: str, params: dict, persona: Persona | None) -> dict:
    """Run a job here and now, in the caller's transaction."""
    return get(name).function()(s, persona, **params)
