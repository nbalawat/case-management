"""A form as JSON Schema 2020-12, and validation of an answer against it.

The schema is the contract: what the platform checks is what the exported schema says, so a form can be filled by any
JSON Schema renderer or checked by any validator. Conditional fields become `if`/`then` clauses.
"""
from __future__ import annotations

from jsonschema import Draft202012Validator, FormatChecker

from .model import Field, Form

DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _property(f: Field) -> dict:
    p: dict = {"title": f.label}
    if f.help:
        p["description"] = f.help
    if f.type in ("text", "long_text"):
        p["type"] = "string"
        if f.min_length is not None:
            p["minLength"] = f.min_length
        if f.max_length is not None:
            p["maxLength"] = f.max_length
        if f.pattern:
            p["pattern"] = f.pattern
        if f.type == "long_text":
            p["x-widget"] = "textarea"
    elif f.type in ("number", "money"):
        p["type"] = "number"
        if f.minimum is not None:
            p["minimum"] = f.minimum
        if f.maximum is not None:
            p["maximum"] = f.maximum
        if f.type == "money":
            p["x-widget"] = "money"
    elif f.type == "date":
        p |= {"type": "string", "format": "date"}
    elif f.type == "email":
        p |= {"type": "string", "format": "email"}
    elif f.type == "boolean":
        p["type"] = "boolean"
    elif f.type == "choice":
        p |= {"type": "string", "enum": list(f.options)}
    elif f.type == "multi_choice":
        p |= {"type": "array", "items": {"type": "string", "enum": list(f.options)}, "uniqueItems": True}
    if f.option_labels:
        p["x-option-labels"] = dict(f.option_labels)
    return p


def _condition(f: Field) -> dict:
    c = f.visible_when
    match = {"properties": {c.key: {"const": c.value}}, "required": [c.key]}
    return match if c.op == "==" else {"not": match}


def to_json_schema(form: Form) -> dict:
    fields = form.fields
    schema: dict = {
        "$schema": DIALECT, "$id": f"urn:caseforge:form:{form.id}:{form.version}", "title": form.title, "type": "object",
        "properties": {f.key: _property(f) for f in fields},
        "required": [f.key for f in fields if f.required and f.visible_when is None],
        "additionalProperties": False,
        "x-sections": [{"title": s.title, "fields": [f.key for f in s.fields]} for s in form.sections],
    }
    if form.description:
        schema["description"] = form.description
    conditional = []
    for f in fields:
        if f.visible_when is None:
            continue
        then = {"required": [f.key]} if f.required else {}
        conditional.append({"if": _condition(f), "then": then, "else": {"not": {"required": [f.key]}}})
    if conditional:
        schema["allOf"] = conditional
    return schema


def visible_only(form: Form, data: dict) -> dict:
    """Drop answers to fields that are hidden by an earlier answer, and empty answers, in field order."""
    out: dict = {}
    for f in form.fields:
        if not f.visible(out):  # conditions name fields above, which are already in out
            continue
        v = data.get(f.key)
        if v is None or v == "" or v == []:
            continue
        out[f.key] = v
    return out


def errors(form: Form, data: dict, complete: bool) -> dict[str, str]:
    """Field key -> what is wrong. A draft is checked for well-formed values only; a completed form for everything."""
    schema = to_json_schema(form)
    if not complete:
        schema = {k: v for k, v in schema.items() if k not in ("required", "allOf")}
    out: dict[str, str] = {}
    for e in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data):
        if e.validator == "required":
            for key in e.validator_value:
                if key not in (e.instance or {}):
                    out.setdefault(key, "Required")
        elif e.validator == "additionalProperties":
            out.setdefault("_form", "Answers to fields the form does not have")
        else:
            key = str(e.path[0]) if e.path else "_form"
            out.setdefault(key, _say(e, form))
    return out


def _say(e, form: Form) -> str:
    v = e.validator
    if v == "minLength":
        return f"At least {e.validator_value} characters"
    if v == "maxLength":
        return f"At most {e.validator_value} characters"
    if v == "minimum":
        return f"At least {e.validator_value:g}"
    if v == "maximum":
        return f"At most {e.validator_value:g}"
    if v == "enum":
        return "Not one of the options"
    if v == "format":
        return {"date": "Not a date (YYYY-MM-DD)", "email": "Not an email address"}.get(e.validator_value, "Not valid")
    if v == "pattern":
        return "Not in the expected format"
    if v == "type":
        return {"number": "A number is needed", "boolean": "Yes or no", "string": "Text is needed", "array": "A list is needed"}.get(e.validator_value, "Wrong kind of value")
    return e.message
