"""Checking a rendered report against the regulator's own published schema before anything is sent.

Each format names the schema it is written to. FinCEN's batch schemas ship with the app (app/filing/schemas/fincen,
US government works, links inside them pointed at the local copies). goAML schemas are published by each FIU, usually
on terms that do not allow passing them on, so they are not in the repository: an operator downloads the client's
FIU schema (scripts/fetch-goaml-schema.sh) into GOAML_SCHEMA_DIR and names the file in the client's goAML profile.

Schemas are XSD 1.1 (both FinCEN's and goAML's use assertions), checked with the open-source `xmlschema` library.
A compiled schema is kept for the life of the process: compiling FinCEN's takes a second or two, checking a report
a few milliseconds.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import settings

FINCEN_DIR = Path(__file__).parent / "schemas" / "fincen"
DEFAULT_GOAML_FILE = "xsd-goaml-2026-10-19.xsd"  # FIU Luxembourg's current schema, as named on its download page
MAX_ERRORS = 20


@dataclass(frozen=True)
class SchemaRef:
    title: str
    path: Path | None  # None: no schema is available for this format here
    missing: str = ""  # why not, in words a person can act on


@dataclass
class Result:
    schema: str
    checked: bool
    valid: bool
    errors: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _goaml(institution: dict) -> SchemaRef:
    name = str(institution.get("goaml_schema") or DEFAULT_GOAML_FILE)
    if "/" in name or "\\" in name or name.startswith("."):
        return SchemaRef("goAML", None, "The goAML schema must be named by its file name in the schema folder")
    path = settings.goaml_schema_dir / name
    if not path.is_file():
        return SchemaRef(f"goAML ({name})", None,
                         f"The FIU's goAML schema {name} is not installed, so the report has not been checked against it. "
                         "An operator downloads it into the schema folder (scripts/fetch-goaml-schema.sh).")
    return SchemaRef(f"goAML ({name})", path)


def schema_for(format_id: str, institution: dict) -> SchemaRef:
    if format_id == "fincen_sar_xml":
        return SchemaRef("FinCEN SAR batch schema 1.2 (EFL_SARXBatchSchema.xsd)", FINCEN_DIR / "EFL_SARXBatchSchema.xsd")
    if format_id == "fincen_ctr_xml":
        return SchemaRef("FinCEN CTR batch schema 1.2 (EFL_CTRXBatchSchema.xsd)", FINCEN_DIR / "EFL_CTRXBatchSchema.xsd")
    if format_id.startswith("goaml_"):
        return _goaml(institution)
    return SchemaRef("No published schema", None, "This regulator publishes no XML schema for this report; it is checked by its own rules only")


@lru_cache(maxsize=8)
def _compiled(path: str):
    import xmlschema

    return xmlschema.XMLSchema11(path)


def _say(e) -> str:
    """The schema's own words where it has them: FIUs write their business rules as assertions with a message."""
    where = (e.path or "").replace("fc2:", "")
    reason = str(e.reason)
    elem = getattr(getattr(e, "validator", None), "elem", None)
    if elem is not None:
        message = next((v for k, v in elem.attrib.items() if k.endswith("}message")), "")
        if message:  # often HTML for the FIU's portal, in two languages: keep the first bold sentence, else the text
            bold = re.search(r"<b>(.*?)</b>", message, re.S)
            reason = " ".join(re.sub(r"<[^>]+>", " ", bold.group(1) if bold else message).split())
        elif elem.get("test") and reason.startswith("assertion test is false"):
            reason = f"business rule not met: {' '.join(elem.get('test').split())[:300]}"
    return f"{reason} (at {where})" if where else reason


def validate(format_id: str, institution: dict, document: str) -> Result:
    ref = schema_for(format_id, institution)
    if ref.path is None:
        return Result(ref.title, checked=False, valid=False, note=ref.missing)
    try:
        schema = _compiled(str(ref.path))
    except Exception as e:  # a broken or unreadable schema file is the operator's to fix, not a reason to crash a filing
        return Result(ref.title, checked=False, valid=False, note=f"The schema could not be read: {str(e).splitlines()[0][:200]}")
    errors = []
    for e in schema.iter_errors(document):
        errors.append(_say(e))
        if len(errors) >= MAX_ERRORS:
            break
    return Result(ref.title, checked=True, valid=not errors, errors=errors)


def problems(result: Result) -> list[str]:
    """What the check means for sending: schema errors always stop it; an unchecked report stops it only if the platform says so."""
    if result.checked:
        return [f"The regulator's schema refuses it: {e}" for e in result.errors]
    if settings.filing_require_schema and result.note:
        return [result.note]
    return []
