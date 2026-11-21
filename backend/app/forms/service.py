"""Forms on cases: which forms a case offers, filling one in, completing and reopening it, and publishing a new version."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import library as case_type_library, load_case_types
from ..domain.models import Case, FormSubmission, FormVersion, new_id, now
from ..domain.personas import Persona
from ..tenancy import tenant_of
from ..visibility import system_work
from . import store
from .model import Form, FormError, to_yaml, version_key
from .schema import errors, to_json_schema, visible_only


class FormInvalid(FormError):
    """The answers do not satisfy the form. `fields` says which and why."""

    def __init__(self, fields: dict[str, str]):
        super().__init__("Some answers need attention: " + "; ".join(f"{k}: {v}" for k, v in fields.items()))
        self.fields = fields


# --- definitions ---------------------------------------------------------------------------------------------
def diff(old: Form | None, new: Form) -> list[str]:
    if old is None:
        return [f"New form '{new.title}'"]
    a, b = {f.key: f for f in old.fields}, {f.key: f for f in new.fields}
    out = [f"Adds field '{b[k].label}'" for k in b if k not in a]
    out += [f"Removes field '{a[k].label}'" for k in a if k not in b]
    out += [f"Changes field '{b[k].label}'" for k in b if k in a and a[k] != b[k]]
    if [k for k in a if k in b] != [k for k in b if k in a] or [s.title for s in old.sections] != [s.title for s in new.sections]:
        out.append("Rearranges the form")
    if (old.case_types, old.stages) != (new.case_types, new.stages):
        out.append("Changes where the form is offered")
    return out or ["No change to the fields"]


@system_work
def check(s: Session, form: Form) -> dict:
    tenant = tenant_of(s)
    types = case_type_library() | load_case_types(tenant)
    for ct in form.case_types:
        if ct not in types:
            raise FormError(f"Unknown case type '{ct}'")
    known = {st for ct in (form.case_types or types) for st in types[ct]["states"]}
    bad = [st for st in form.stages if st not in known]
    if bad:
        raise FormError(f"Unknown stage(s) for these case types: {', '.join(bad)}")
    current = store.active(s).get(form.id)
    if current is not None and version_key(form.version) <= version_key(current.version):
        raise FormError(f"version must be higher than the active {current.version}")
    return {"form": form.describe(), "yaml": form.source or to_yaml(form), "json_schema": to_json_schema(form), "is_new": current is None,
            "changes": diff(current, form)}


@system_work
def publish(s: Session, form: Form, persona: Persona, note: str = "") -> dict:
    if not persona.can("publish_workflow"):
        raise FormError("Publishing a form needs the 'publish workflow' permission")
    result = check(s, form)
    s.add(FormVersion(form_id=form.id, version=form.version, source=result["yaml"], published_by=persona.id, note=note))
    s.flush()
    return result


# --- forms on a case -----------------------------------------------------------------------------------------
def offered(form: Form, case: Case) -> bool:
    return case.case_type in form.case_types


def editable_now(form: Form, case: Case) -> str | None:
    """Why the form cannot be changed now, or None."""
    if case.closed is not None:
        return "The case is closed"
    if form.stages and case.state not in form.stages:
        return "This form is filled in another stage"
    return None


@system_work
def submission(s: Session, case: Case, form_id: str) -> FormSubmission | None:
    return s.scalars(select(FormSubmission).where(FormSubmission.case_id == case.id, FormSubmission.form_id == form_id)).first()


@system_work
def prefill(s: Session, case: Case, form: Form) -> dict:
    wanted = [f for f in form.fields if f.prefill]
    if not wanted:
        return {}
    from ..decisions.facts import case_facts  # noqa: PLC0415

    facts = case_facts(s, case)
    return {f.key: facts.get(f.prefill) for f in wanted if facts.get(f.prefill) not in (None, "")}


@system_work
def for_case(s: Session, case: Case) -> list[dict]:
    """Every form this case offers, with its answers so far (or the prefilled values) and what is still missing."""
    out = []
    for form in sorted(store.active(s).values(), key=lambda f: f.title):
        if not offered(form, case):
            continue
        sub = submission(s, case, form.id)
        if sub is not None and sub.form_version != form.version:
            try:
                form = store.get(s, form.id, sub.form_version)  # answers stay on the version they were given against
            except FormError:
                pass
        data = dict(sub.data) if sub else prefill(s, case, form)
        missing = errors(form, visible_only(form, data), complete=True)
        out.append({"form": form.describe(), "status": sub.status if sub else "not_started", "data": data,
                    "updated_by": sub.updated_by if sub else None, "updated": sub.updated.isoformat() if sub else None,
                    "completed_by": sub.completed_by if sub else None, "completed_at": sub.completed_at.isoformat() if sub and sub.completed_at else None,
                    "missing": missing, "locked": editable_now(form, case)})
    return out


@system_work
def save(s: Session, case: Case, form_id: str, data: dict, persona: Persona, complete: bool = False) -> FormSubmission:
    """Store answers. A draft may be partial; completing checks everything. A completed form is reopened before it changes."""
    from ..cases.service import record  # noqa: PLC0415

    if not persona.can("work"):
        raise FormError("Filling in a form needs the 'work' permission")
    sub = submission(s, case, form_id)
    form = store.get(s, form_id, sub.form_version if sub else None)
    if not offered(form, case):
        raise FormError(f"'{form.title}' is not used on this kind of case")
    why = editable_now(form, case)
    if why:
        raise FormError(why)
    if sub is not None and sub.status == "complete":
        raise FormError("The form is complete. Reopen it to change an answer.")
    if not isinstance(data, dict):
        raise FormError("Answers must be an object")
    clean = visible_only(form, data)
    problems = errors(form, clean, complete)
    if problems:
        raise FormInvalid(problems)
    at = now()
    if sub is None:
        sub = FormSubmission(id=new_id("frm"), case_id=case.id, form_id=form.id, form_version=form.version, data={}, updated_by=persona.id)
        s.add(sub)
    changed = sorted(k for k in set(sub.data) | set(clean) if sub.data.get(k) != clean.get(k))
    sub.data, sub.updated_by, sub.updated = clean, persona.id, at
    if complete:
        sub.status, sub.completed_by, sub.completed_at = "complete", persona.id, at
    record(s, case.id, persona.id, "human", "form.completed" if complete else "form.saved",
           {"form": form.id, "title": form.title, "version": form.version, "changed": changed}, ts=at)
    s.flush()
    return sub


@system_work
def reopen(s: Session, case: Case, form_id: str, persona: Persona, reason: str) -> FormSubmission:
    from ..cases.service import record  # noqa: PLC0415

    if not persona.can("work"):
        raise FormError("Reopening a form needs the 'work' permission")
    sub = submission(s, case, form_id)
    if sub is None or sub.status != "complete":
        raise FormError("Only a completed form can be reopened")
    if not reason.strip():
        raise FormError("Say why the form is reopened")
    form = store.get(s, form_id, sub.form_version)
    why = editable_now(form, case)
    if why:
        raise FormError(why)
    sub.status, sub.completed_by, sub.completed_at = "draft", None, None
    record(s, case.id, persona.id, "human", "form.reopened", {"form": form_id, "title": form.title, "reason": reason})
    s.flush()
    return sub
