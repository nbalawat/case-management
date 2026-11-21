"""The Temporal workflow engine: each case a durable workflow, each person's step a workflow update."""
from __future__ import annotations

import asyncio
import importlib
import logging
import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from ..cases.service import TransitionError
from ..config import settings
from ..domain.models import Case
from ..domain.personas import Persona
from ..workflow.actions import Action, CaseView, apply
from ..workflow.engine import EngineUnavailable
from . import temporal
from .bridge import bridge

log = logging.getLogger("caseforge.durable")


def _on_the_application_database(s: Session) -> bool:
    from .. import db

    bind = s.get_bind()
    return getattr(bind, "engine", bind) is db.engine


class TemporalEngine:
    """Runs case workflows on Temporal.

    `perform` ends the caller's transaction (so the worker's write never waits on it), sends the step to the case's
    workflow as an update, starting the workflow if the case has none yet, and waits for the outcome. The caller's
    objects are expired afterwards, so what it reads next is what the workflow wrote.
    """

    name, in_process = "temporal", False

    def __init__(self) -> None:
        self._base = None
        self._clients: dict[str, object] = {}
        self._lock: asyncio.Lock | None = None

    async def _client(self, tenant_id: str):
        """This client's namespace, connected (and registered) once. Only ever called on the bridge's loop."""
        if tenant_id in self._clients:
            return self._clients[tenant_id]
        self._lock = self._lock or asyncio.Lock()
        async with self._lock:
            if self._base is None:
                self._base = await temporal.connect()
            if tenant_id not in self._clients:
                self._clients[tenant_id] = await temporal.client_for(self._base, tenant_id)
        return self._clients[tenant_id]

    def reset(self) -> None:
        """Forget connections (tests, and after Temporal moved)."""
        self._base, self._clients, self._lock = None, {}, None

    def perform(self, s: Session, case: Case, persona: Persona, action: Action) -> CaseView:
        from temporalio.client import WithStartWorkflowOperation, WorkflowUpdateFailedError
        from temporalio.common import WorkflowIDConflictPolicy
        from temporalio.exceptions import ApplicationError
        from temporalio.service import RPCError

        from .workflows import REFUSED, CaseWorkflow

        tenant_id, cid = case.tenant_id, case.id
        if not _on_the_application_database(s):  # a worker can only reach the application's own database
            return apply(s, case, persona, action)
        if persona.read_only:  # refused here as the built-in engine would, without a round trip
            raise TransitionError("Read-only access")
        act = Action.from_dict(action.as_dict() | {"step_id": action.step_id or uuid.uuid4().hex})
        s.commit()

        async def go() -> dict:
            client = await self._client(tenant_id)
            start = WithStartWorkflowOperation(CaseWorkflow.run, args=[tenant_id, cid], id=temporal.case_id(tenant_id, cid),
                                               task_queue=settings.temporal_task_queue, id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)
            return await client.execute_update_with_start_workflow(CaseWorkflow.perform, act.as_dict(), start_workflow_operation=start, result_type=dict)

        try:
            view = bridge.call(go, timeout=settings.temporal_step_seconds)
        except WorkflowUpdateFailedError as e:
            cause = e.cause
            if isinstance(cause, ApplicationError) and cause.type == REFUSED:
                raise TransitionError(cause.message) from None
            raise EngineUnavailable("The case workflow could not apply that step. Nothing was changed; try again.") from e
        except (TimeoutError, RPCError, temporal.DurableError) as e:
            self.reset()
            raise EngineUnavailable("The workflow engine did not answer. Nothing was changed; try again in a moment.") from e
        finally:
            s.expire_all()
        return CaseView.from_dict({k: v for k, v in view.items() if k in CaseView.__dataclass_fields__})

    def observe(self, tenant_id: str, case_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(case_ids))
        if not ids:
            return

        async def go() -> None:
            try:
                await temporal.adopt(await self._client(tenant_id), tenant_id, ids)
            except Exception:  # noqa: BLE001  the worker adopts every open case at its next pass, so nothing is lost
                self.reset()
                log.warning("Could not tell %d case workflow(s) at %s; the worker will catch up", len(ids), tenant_id, exc_info=True)

        bridge.submit(go)

    # ---- registered jobs ---------------------------------------------------------------------------------------------

    def run_job(self, s: Session, name: str, params: dict, persona: Persona | None) -> dict:
        """Run a job as a JobWorkflow and wait for it (a background job is queued to start after commit instead)."""
        from temporalio.client import WorkflowFailureError
        from temporalio.common import WorkflowIDConflictPolicy
        from temporalio.exceptions import ApplicationError
        from temporalio.service import RPCError

        from ..tenancy import tenant_of
        from ..workflow import jobs
        from ..workflow.observer import JOBS
        from .workflows import JOB_REFUSED, JobWorkflow

        job = jobs.get(name)
        if not _on_the_application_database(s):
            return jobs.run_inline(s, name, params, persona)
        tenant_id, pid = tenant_of(s), persona.id if persona else None
        if job.background:
            s.info.setdefault(JOBS, []).append((tenant_id, name, params, pid))
            return {"queued": True}
        s.commit()

        async def go() -> dict:
            client = await self._client(tenant_id)
            return await client.execute_workflow(JobWorkflow.run, args=[tenant_id, name, params, pid, job.attempts, job.timeout_seconds],
                                                 id=temporal.job_id(tenant_id, name, job.key(params)), task_queue=settings.temporal_task_queue,
                                                 id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING, result_type=dict)

        try:
            return bridge.call(go, timeout=job.timeout_seconds * job.attempts + 30)
        except WorkflowFailureError as e:
            cause = e.cause
            while cause is not None and not (isinstance(cause, ApplicationError) and cause.type == JOB_REFUSED):
                cause = cause.cause
            if cause is not None and cause.details:
                module, cls = str(cause.details[0]).split(":")
                raise getattr(importlib.import_module(module), cls)(cause.message) from None
            raise EngineUnavailable(f"The {name.replace('.', ' ')} job failed after {job.attempts} attempts. Nothing it had not finished was kept.") from e
        except (TimeoutError, RPCError, temporal.DurableError) as e:
            self.reset()
            raise EngineUnavailable("The workflow engine did not answer. Try again in a moment.") from e
        finally:
            s.expire_all()

    def brief(self, s: Session, case: Case) -> None:
        """Queue the case's briefing: a JobWorkflow starts it after commit, and retries it if the agents or their model fail."""
        self.run_job(s, "agents.brief", {"case_id": case.id, "token": uuid.uuid4().hex[:12]}, None)

    def start_jobs(self, items: list[tuple[str, str, dict, str | None]]) -> None:
        from temporalio.common import WorkflowIDConflictPolicy

        from ..workflow import jobs
        from .workflows import JobWorkflow

        async def go() -> None:
            for tenant_id, name, params, pid in items:
                job = jobs.get(name)
                try:
                    client = await self._client(tenant_id)
                    await client.start_workflow(JobWorkflow.run, args=[tenant_id, name, params, pid, job.attempts, job.timeout_seconds],
                                                id=temporal.job_id(tenant_id, name, job.key(params)), task_queue=settings.temporal_task_queue,
                                                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)
                except Exception:  # noqa: BLE001  logged; the work it stood for can be asked for again
                    self.reset()
                    log.warning("Could not start %s for %s at %s", name, params, tenant_id, exc_info=True)

        bridge.submit(go)

    def observe_filings(self, tenant_id: str, filing_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(filing_ids))
        if not ids:
            return

        async def go() -> None:
            try:
                await temporal.adopt_filings(await self._client(tenant_id), tenant_id, ids)
            except Exception:  # noqa: BLE001  the worker adopts every sent report at its next pass
                self.reset()
                log.warning("Could not tell %d filing workflow(s) at %s; the worker will catch up", len(ids), tenant_id, exc_info=True)

        bridge.submit(go)

    def status(self, tenant_id: str) -> dict:
        """Where this client's case workflows run, and how many are running. Temporal being down is reported, not raised."""
        from .namespaces import namespace_for

        ns = namespace_for(tenant_id)

        async def ask() -> int:
            return await temporal.running(await self._client(tenant_id), tenant_id)

        try:  # the kept connection: a first look after a start is not mistaken for Temporal being down
            n = bridge.call(ask, timeout=10)
        except Exception:  # noqa: BLE001
            self.reset()
            return {"engine": "temporal", "connected": False, "clocks": None, "workflows": None, "namespace": ns,
                    "note": "Temporal did not answer. People's steps on cases wait until it does; the sweep still checks stage clocks."}
        from .encryption import describe

        return {"engine": "temporal", "connected": True, "clocks": n, "workflows": n, "namespace": ns, "encryption": describe(),
                "note": "Each open case is a durable Temporal workflow: people's steps are applied one at a time, in order, and its stage deadline is a durable timer."}
