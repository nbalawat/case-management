"""Starting, stopping and restarting a client's stream jobs from the product.

A job is always rendered here from the client's settings in force, never taken from a caller, and every start and
stop is recorded with who did it. Changing what a client is monitored with is the same decision as approving a
detection change, so it needs the same permission.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..backbone import topics
from ..backbone.bus import BusError, connect
from ..config import settings
from ..detection import tuning
from ..domain.models import StreamJobRun, new_id, now
from ..domain.personas import Persona
from ..tenancy import tenant_of
from . import flink, gateway
from .jobs import JOBS, render

PERMISSION = tuning.APPROVE


class ControlError(ValueError):
    pass


def enabled() -> bool:
    """Whether jobs can be controlled from the product: it needs the cluster and its SQL Gateway."""
    return bool(settings.flink_url and settings.flink_gateway_url)


def may(persona: Persona) -> bool:
    return enabled() and persona.can(PERMISSION) and not persona.read_only


def _check(job: str, persona: Persona) -> None:
    if job not in JOBS:
        raise ControlError(f"Stream job '{job}' not found")
    if not enabled():
        raise ControlError("Stream jobs are not controlled from here: set FLINK_URL and FLINK_GATEWAY_URL")
    if not persona.can(PERMISSION) or persona.read_only:
        raise ControlError(f"Starting or stopping a stream job needs the '{PERMISSION}' permission")


def _live(tenant_id: str, job: str) -> dict:
    view = flink.status(tenant_id)
    if not view["connected"]:
        raise flink.FlinkError(view["note"])
    return next(j for j in view["jobs"] if j["job"] == job)


def _record(s: Session, job: str, action: str, persona: Persona, engine_job_id: str, params: dict | None = None, note: str = "") -> StreamJobRun:
    run = StreamJobRun(id=new_id("sjr"), tenant_id=tenant_of(s), job=job, action=action, params=params or {}, engine_job_id=engine_job_id,
                       by=persona.id, at=now(), note=note.strip()[:500])
    s.add(run)
    s.flush()
    return run


def _submit(s: Session, job: str, persona: Persona) -> StreamJobRun:
    """Render the job from the client's settings in force, make sure its topics exist, and hand it to the cluster."""
    tenant = tenant_of(s)
    params, _on, _revision = tuning.current(s, JOBS[job].scenario)
    sql = render(job, tenant, params, settings.flink_kafka_bootstrap or None)
    _ensure_topics(tenant)
    return _record(s, job, "started", persona, gateway.submit(sql), params)


def _ensure_topics(tenant_id: str) -> None:
    """A job whose topics do not exist yet fails and restarts for ever, so they are created first.

    Normally the backbone worker has already made them. This covers a client onboarded while no worker was running.
    """
    try:
        bus = connect()
        if bus is not None:
            try:
                bus.ensure_topics(topics.topic(tenant_id, name) for name in topics.ALL)
            finally:
                bus.close()
    except BusError as e:
        raise ControlError(f"The event bus did not answer, so the job was not started: {e}") from None


def start(s: Session, job: str, persona: Persona) -> StreamJobRun:
    """Render the job from the client's settings in force and submit it. Refused while one is already running."""
    _check(job, persona)
    if _live(tenant_of(s), job)["engine_job_id"]:
        raise ControlError("This job is already running for this client. Restart it to apply new settings")
    return _submit(s, job, persona)


def stop(s: Session, job: str, persona: Persona, note: str = "") -> StreamJobRun:
    _check(job, persona)
    running = _live(tenant_of(s), job)["engine_job_id"]
    if not running:
        raise ControlError("This job is not running for this client")
    flink.cancel(running)
    return _record(s, job, "stopped", persona, running, note=note)


def restart(s: Session, job: str, persona: Persona) -> StreamJobRun:
    """Stop the running job, if there is one, and start it again on the settings in force now.

    The new job reads the topic from the start, so alerts already raised are raised again and dropped downstream as
    duplicates (same source and id).
    """
    _check(job, persona)
    running = _live(tenant_of(s), job)["engine_job_id"]
    if running:
        flink.cancel(running)
        _record(s, job, "stopped", persona, running, note="Restarted")
    return _submit(s, job, persona)


def last_starts(s: Session) -> dict[str, StreamJobRun]:
    """The most recent start of each job for this client."""
    out: dict[str, StreamJobRun] = {}
    for run in s.scalars(select(StreamJobRun).where(StreamJobRun.action == "started").order_by(StreamJobRun.at.desc(), StreamJobRun.id.desc())):
        out.setdefault(run.job, run)
    return out


def describe(s: Session, jobs: list[dict]) -> list[dict]:
    """Each job with what the product knows about the run on the cluster: who started it, and whether the client's
    settings have changed since. A job started by hand has no record here and is shown as such."""
    starts = last_starts(s)
    out = []
    for j in jobs:
        run = starts.get(j["job"])
        ours = bool(run and j["engine_job_id"] and run.engine_job_id == j["engine_job_id"])
        current = tuning.current(s, j["scenario"])[0] if ours else None
        out.append(j | {"started_by": run.by if ours else None,
                        "settings_changed": bool(ours and current != run.params)})
    return out


def row(run: StreamJobRun) -> dict:
    return {"id": run.id, "job": run.job, "action": run.action, "params": run.params, "engine_job_id": run.engine_job_id, "by": run.by,
            "at": run.at.isoformat(), "note": run.note}
