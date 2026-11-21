"""The event backbone as one client sees it, and the controls for its stream jobs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..backbone import status
from ..domain.personas import Persona
from ..streamjobs import control
from ..streamjobs.flink import FlinkError
from ..streamjobs.gateway import GatewayError
from ..streamjobs.jobs import JobError
from .deps import actor, get_session, persona

router = APIRouter(prefix="/api")


@router.get("/backbone")
def backbone(s: Session = Depends(get_session), p: Persona = Depends(persona)):
    """Counts only: how many of this client's events have gone out and what its inbound streams have taken in."""
    view = status.view(s)
    return view | {"stream_jobs": view["stream_jobs"] | {"can_control": control.may(p)}}


def _do(fn):
    try:
        return control.row(fn())
    except control.ControlError as e:
        text = str(e)
        raise HTTPException(404 if text.endswith("not found") else 403 if " needs the " in text else 409, text) from None
    except JobError as e:
        raise HTTPException(409, str(e)) from None
    except (GatewayError, FlinkError) as e:
        raise HTTPException(502, str(e)) from None


class StopIn(BaseModel):
    note: str = ""


@router.post("/stream-jobs/{job}/start")
def start_job(job: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return _do(lambda: control.start(s, job, p))


@router.post("/stream-jobs/{job}/stop")
def stop_job(job: str, body: StopIn | None = None, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return _do(lambda: control.stop(s, job, p, body.note if body else ""))


@router.post("/stream-jobs/{job}/restart")
def restart_job(job: str, s: Session = Depends(get_session), p: Persona = Depends(actor)):
    return _do(lambda: control.restart(s, job, p))
