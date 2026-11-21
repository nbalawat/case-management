"""The model registry: every version, and the controls around putting one to work.

A trained model is a candidate. Someone other than the person who trained it approves it (four-eyes, with the
'approve detection changes' permission). Only an approved model can become the champion (whose score is shown on
alerts) or the challenger (scored alongside, shown only in comparisons). Promoting a new champion retires the old one.
Every step is recorded on the governance change register.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.access import Persona
from ..domain.models import ModelVersion, now
from . import APPROVE, NAME


class RegistryError(ValueError):
    pass


def versions(s: Session, name: str = NAME) -> list[ModelVersion]:
    return list(s.scalars(select(ModelVersion).where(ModelVersion.name == name).order_by(ModelVersion.version.desc())))


def in_role(s: Session, role: str, name: str = NAME) -> ModelVersion | None:
    return s.scalars(select(ModelVersion).where(ModelVersion.name == name, ModelVersion.status == role)).first()


def _get(s: Session, model_id: str) -> ModelVersion:
    mv = s.get(ModelVersion, model_id)
    if mv is None:
        raise RegistryError(f"No model '{model_id}'")
    return mv


def approve(s: Session, model_id: str, persona: Persona, note: str = "") -> ModelVersion:
    mv = _get(s, model_id)
    if not persona.can(APPROVE):
        raise RegistryError("Approving a model needs the 'approve detection changes' permission")
    if mv.status != "candidate":
        raise RegistryError(f"Only a candidate can be approved; this one is {mv.status}")
    if persona.id == mv.trained_by:
        raise RegistryError("Four-eyes: the person who trained a model cannot approve it")
    mv.status, mv.approved_by, mv.approved = "approved", persona.id, now()
    mv.note = (mv.note + ("\n" if mv.note else "") + f"Approved: {note}").strip() if note else mv.note
    _register(s, mv, persona, "approved", note)
    return mv


def promote(s: Session, model_id: str, persona: Persona, role: str = "champion") -> ModelVersion:
    if role not in ("champion", "challenger"):
        raise RegistryError("A model is promoted to champion or challenger")
    mv = _get(s, model_id)
    if not persona.can(APPROVE):
        raise RegistryError("Promoting a model needs the 'approve detection changes' permission")
    if mv.status not in ("approved", "challenger", "champion"):
        raise RegistryError("Only an approved model can be put to work")
    if mv.status == role:
        return mv
    current = in_role(s, role, mv.name)
    if current is not None:
        current.status = "retired" if role == "champion" else "approved"
    mv.status = role
    s.flush()
    _register(s, mv, persona, f"promoted to {role}", f"replacing v{current.version}" if current else "")
    return mv


def retire(s: Session, model_id: str, persona: Persona) -> ModelVersion:
    mv = _get(s, model_id)
    if not persona.can(APPROVE):
        raise RegistryError("Retiring a model needs the 'approve detection changes' permission")
    mv.status = "retired"
    _register(s, mv, persona, "retired", "")
    return mv


def _register(s: Session, mv: ModelVersion, persona: Persona, what: str, note: str) -> None:
    """Kept on the model itself; the governance change register reads it from there."""
    mv.history = [*(mv.history or []), {"at": now().isoformat(), "what": what, "by": persona.id, "note": note}]
    s.flush()
