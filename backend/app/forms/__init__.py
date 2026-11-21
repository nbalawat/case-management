"""Forms: structured questions a case asks at a stage (a SAR decision record, an EDD questionnaire, a fraud claim
intake), built without code and compiled to JSON Schema.

  model     a form, read from YAML and validated
  schema    the JSON Schema 2020-12 it compiles to, and validation of answers
  store     the shipped library (backend/forms) and each client's published versions
  service   forms on a case: fill, complete, reopen; check and publish a definition

The interface other components build on:

    list_forms(s)                      every form in force at this client: [{id, title, version, case_types, stages}]
    is_complete(s, case, form_id)      (True, None) or (False, why)
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.models import Case
from ..visibility import system_work
from . import store
from .model import Form, FormError
from .service import submission

__all__ = ["Form", "FormError", "is_complete", "list_forms"]


@system_work
def list_forms(s: Session) -> list[dict]:
    return [{"id": f.id, "title": f.title, "version": f.version, "case_types": list(f.case_types), "stages": list(f.stages)}
            for f in sorted(store.active(s).values(), key=lambda f: f.title)]


@system_work
def is_complete(s: Session, case: Case, form_id: str) -> tuple[bool, str | None]:
    form = store.active(s).get(form_id)
    title = form.title if form else form_id
    sub = submission(s, case, form_id)
    if sub is None:
        return False, f"Fill in the form '{title}'"
    if sub.status != "complete":
        return False, f"Complete the form '{title}'"
    return True, None
