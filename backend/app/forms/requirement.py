"""A step can require forms to be complete: `requires: [form]` in a case type.

The forms are the ones the step names (`forms: [sar_decision]` on the transition) or, when it names none, every form
this case type offers at the stage the case is in. A draft does not count; a form reopened after completion counts
again only once it is completed again.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..cases.service import register_requirement
from ..domain.models import Case

NAME = "form"


def check(s: Session, case: Case, transition: dict) -> str | None:
    from . import is_complete, store

    forms = store.active(s)
    named = transition.get("forms") or []
    wanted = [f for f in named if f in forms] if named else \
        [f.id for f in sorted(forms.values(), key=lambda f: f.title) if case.case_type in f.case_types and (not f.stages or case.state in f.stages)]
    missing = [f for f in named if f not in forms]
    if missing:
        return f"This step needs a form this client does not have: {', '.join(missing)}"
    for form_id in wanted:
        done, why = is_complete(s, case, form_id)
        if not done:
            return why
    return None


def register() -> None:
    register_requirement(NAME, "The stage's forms, completed (or the forms the step names)", check)
