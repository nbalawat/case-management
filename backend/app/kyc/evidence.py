"""The evidence locker: documents held for a customer, fingerprinted on the way in and checked on the way out.

Bytes go to the client's own area of an object store (any provider behind `ObjectStore`). The row keeps the SHA-256,
so a file changed in storage is caught when it is next opened, and a chain of custody in which each entry seals the
one before. Redaction never touches the original: it writes a second copy with the chosen details removed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..config import settings
from ..domain.models import Case, Evidence, Party, new_id, now
from ..domain.personas import Persona
from ..storage import ObjectStore, open_store
from ..tenancy import tenant_of
from ..visibility import find_case, system_work

MAX_BYTES = 5 * 1024 * 1024
TEXT_TYPES = ("text/", "application/json", "application/xml", "message/rfc822")
ALLOWED = TEXT_TYPES + ("application/pdf", "image/png", "image/jpeg")
# What can be removed from a text document. Each pattern is deliberately narrow: redaction that guesses removes evidence.
PATTERNS = {
    "account_number": (re.compile(r"\b(?:[A-Z]{2}\d{2}[A-Z0-9]{10,30}|\d{8,17})\b"), "Account and card numbers"),
    "national_id": (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "Social security numbers"),
    "email": (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "Email addresses"),
    "phone": (re.compile(r"(?<!\w)\+?\d[\d ()-]{8,16}\d(?!\w)"), "Phone numbers"),
    "date_of_birth": (re.compile(r"(?i)\b(?:date of birth|dob|born)[:\s]+\S+(?: \S+){0,2}"), "Dates of birth"),
}


class EvidenceError(ValueError):
    pass


def store(tenant_id: str) -> ObjectStore:
    return open_store(f"{settings.evidence_url.rstrip('/')}/{tenant_id}/")


def clear(tenant_id: str) -> None:
    """Remove a client's documents when its data is rebuilt from scratch. Only a local directory is ever cleared this way."""
    import shutil
    from pathlib import Path

    if settings.evidence_url.startswith("file://"):
        shutil.rmtree(Path(settings.evidence_url[7:]) / tenant_id, ignore_errors=True)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _seal(e: Evidence, by: str, action: str, detail: str = "", at: datetime | None = None) -> None:
    """Add a custody entry whose hash covers the entry before it, so the history cannot be quietly rewritten."""
    prev = e.custody[-1]["hash"] if e.custody else e.sha256
    entry = {"at": (at or now()).isoformat(), "by": by, "action": action, "detail": detail, "prev": prev}
    entry["hash"] = _digest(json.dumps(entry, sort_keys=True).encode())
    e.custody = [*e.custody, entry]


def custody_intact(e: Evidence) -> bool:
    prev = e.sha256
    for entry in e.custody:
        body = {k: v for k, v in entry.items() if k != "hash"}
        if entry.get("prev") != prev or entry.get("hash") != _digest(json.dumps(body, sort_keys=True).encode()):
            return False
        prev = entry["hash"]
    return True


def _is_text(media_type: str) -> bool:
    return media_type.startswith(TEXT_TYPES)


def _may_work(persona: Persona) -> None:
    if persona.read_only or not persona.can("work"):
        raise EvidenceError("Changing the evidence locker needs someone who works cases")


@system_work
def add(s: Session, party_id: str, *, kind: str, title: str, filename: str, media_type: str, data: bytes, persona: Persona, case_id: str | None = None,
        expires: datetime | None = None, replaces: str | None = None, at: datetime | None = None) -> Evidence:
    """Take a document into the locker: store it, fingerprint it and open its chain of custody."""
    _may_work(persona)
    if s.scalars(select(Party.id).where(Party.id == party_id)).first() is None:
        raise EvidenceError("Customer not found")
    types = {c["code"]: c for c in reference.codes(s, "document_types")}
    if kind not in types:
        raise EvidenceError("Choose a document type from the client's list")
    if not data or len(data) > MAX_BYTES:
        raise EvidenceError(f"A document must be between 1 byte and {MAX_BYTES // (1024 * 1024)} MB")
    media_type = (media_type or "").lower().split(";")[0].strip()
    if not media_type.startswith(ALLOWED):
        raise EvidenceError("Only text, PDF, PNG and JPEG documents are accepted")
    if media_type == "application/pdf" and not data.startswith(b"%PDF-") or media_type == "image/png" and not data.startswith(b"\x89PNG") \
            or media_type == "image/jpeg" and not data.startswith(b"\xff\xd8"):
        raise EvidenceError("The file is not what its type says it is")
    if _is_text(media_type):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise EvidenceError("A text document must be UTF-8") from None
    if types[kind].get("expires") == "yes" and expires is None:
        raise EvidenceError(f"{types[kind]['label']} needs its expiry date")
    if case_id is not None:
        case = s.scalars(select(Case).where(Case.id == case_id)).first()
        if case is None or case.party_id != party_id:
            raise EvidenceError("That case is not about this customer")
    at = at or now()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename.strip())[-120:] or "document"
    eid, sha = new_id("ev"), _digest(data)
    row = Evidence(id=eid, party_id=party_id, case_id=case_id, kind=kind, title=title.strip()[:200] or name, filename=name, media_type=media_type, size=len(data),
                   sha256=sha, key=f"{party_id}/{eid}/{name}", expires=expires, uploaded_by=persona.id, uploaded_at=at, custody=[])
    store(tenant_of(s)).write(row.key, data)
    _seal(row, persona.id, "received", f"{len(data)} bytes, sha256 {sha[:16]}", at)
    if replaces:
        old = s.scalars(select(Evidence).where(Evidence.id == replaces, Evidence.party_id == party_id)).first()
        if old is None or old.status != "current":
            raise EvidenceError("The document being replaced is not a current document of this customer")
        old.status = "superseded"
        _seal(old, persona.id, "superseded", f"replaced by {eid}", at)
        _seal(row, persona.id, "replaces", old.id, at)
    s.add(row)
    s.flush()
    return row


def _get(s: Session, evidence_id: str) -> Evidence:
    e = s.scalars(select(Evidence).where(Evidence.id == evidence_id)).first()
    if e is None:
        raise EvidenceError("Document not found")
    return e


@system_work
def read(s: Session, evidence_id: str, persona: Persona, redacted: bool = False, purpose: str = "viewed",
         at: datetime | None = None) -> tuple[Evidence, bytes]:
    """Open a document. The bytes are checked against the fingerprint first, and the opening is added to the custody record."""
    e = _get(s, evidence_id)
    if redacted and not e.redacted_key:
        raise EvidenceError("This document has no redacted copy")
    key, expected = (e.redacted_key, e.redacted_sha256) if redacted else (e.key, e.sha256)
    try:
        with store(tenant_of(s)).open(key) as f:
            data = f.read()
    except Exception:
        raise EvidenceError("The document could not be read from storage") from None
    if _digest(data) != expected:
        _seal(e, "evidence-locker", "integrity_failure", "stored bytes no longer match the fingerprint")
        s.flush()
        s.commit()  # the alarm must outlive the refusal
        raise EvidenceError("This document no longer matches its fingerprint. It may have been changed in storage; it is not being shown.")
    _seal(e, persona.id, purpose, "redacted copy" if redacted else "original", at)
    s.flush()
    return e, data


@system_work
def verify(s: Session, evidence_id: str, persona: Persona, at: datetime | None = None) -> Evidence:
    """A person confirms the document against the original or its issuer. Never the person who uploaded it."""
    _may_work(persona)
    e = _get(s, evidence_id)
    if e.status != "current":
        raise EvidenceError("Only a current document can be verified")
    if e.uploaded_by == persona.id:
        raise EvidenceError("A document is verified by someone other than the person who uploaded it")
    read(s, evidence_id, persona, purpose="checked", at=at)  # verifying means having looked at exactly these bytes
    e.verified_by, e.verified_at = persona.id, at or now()
    _seal(e, persona.id, "verified", "", at)
    s.flush()
    return e


@system_work
def withdraw(s: Session, evidence_id: str, persona: Persona, reason: str) -> Evidence:
    """Take a document out of use. It stays in the locker, with the reason, because it was relied on once."""
    _may_work(persona)
    e = _get(s, evidence_id)
    if e.status != "current":
        raise EvidenceError("Only a current document can be withdrawn")
    if not reason.strip():
        raise EvidenceError("Say why the document is withdrawn")
    e.status = "withdrawn"
    _seal(e, persona.id, "withdrawn", reason.strip()[:300])
    s.flush()
    return e


def redact_text(text: str, kinds: list[str], terms: list[str]) -> tuple[str, list[dict]]:
    """Remove the chosen kinds of detail and the given words from text. Returns the result and a count of what went."""
    counts = []
    for term in sorted({t.strip() for t in terms if t and len(t.strip()) >= 3}, key=len, reverse=True):
        text, n = re.subn(re.escape(term), "[REDACTED]", text, flags=re.I)
        if n:
            counts.append({"kind": "term", "count": n})
    for kind in kinds:
        pattern, _ = PATTERNS[kind]
        text, n = pattern.subn(f"[REDACTED {kind.replace('_', ' ')}]", text)
        if n:
            counts.append({"kind": kind, "count": n})
    merged: dict[str, int] = {}
    for c in counts:
        merged[c["kind"]] = merged.get(c["kind"], 0) + c["count"]
    return text, [{"kind": k, "count": n} for k, n in merged.items()]


@system_work
def redact(s: Session, evidence_id: str, persona: Persona, kinds: list[str], terms: list[str] | None = None) -> Evidence:
    """Write a redacted copy for sharing. Text documents only: this build does not redact inside PDFs or images."""
    _may_work(persona)
    e = _get(s, evidence_id)
    if not _is_text(e.media_type):
        raise EvidenceError("Only text documents can be redacted here. Redact a PDF or image before uploading it.")
    unknown = [k for k in kinds if k not in PATTERNS]
    if unknown or not (kinds or terms):
        raise EvidenceError("Choose what to remove: " + ", ".join(PATTERNS))
    _, data = read(s, evidence_id, persona, purpose="opened for redaction")
    text, counts = redact_text(data.decode("utf-8"), kinds, terms or [])
    if not counts:
        raise EvidenceError("Nothing of that kind was found in the document, so no redacted copy was made")
    out = text.encode("utf-8")
    e.redacted_key, e.redacted_sha256, e.redactions = f"{e.key}.redacted", _digest(out), counts
    store(tenant_of(s)).write(e.redacted_key, out)
    _seal(e, persona.id, "redacted", ", ".join(f"{c['count']} {c['kind'].replace('_', ' ')}" for c in counts))
    s.flush()
    return e


def row(s: Session, e: Evidence, at: datetime | None = None) -> dict:
    at = at or now()
    return {"id": e.id, "party_id": e.party_id, "case_id": e.case_id, "kind": e.kind, "kind_label": reference.label(s, "document_types", e.kind), "title": e.title,
            "filename": e.filename, "media_type": e.media_type, "size": e.size, "sha256": e.sha256, "status": e.status,
            "expires": e.expires.date().isoformat() if e.expires else None, "expired": bool(e.expires and e.expires < at and e.status == "current"),
            "uploaded_by": e.uploaded_by, "uploaded_at": e.uploaded_at.isoformat(), "verified_by": e.verified_by,
            "verified_at": e.verified_at.isoformat() if e.verified_at else None, "redacted": bool(e.redacted_key), "redactions": e.redactions,
            "redactable": _is_text(e.media_type), "custody": e.custody, "custody_intact": custody_intact(e)}


def visible(s: Session, e: Evidence) -> bool:
    """A document filed on a case follows that case's visibility. Call this as the person, not as platform work."""
    return e.case_id is None or find_case(s, e.case_id) is not None


def guard(s: Session, evidence_id: str) -> Evidence:
    e = s.scalars(select(Evidence).where(Evidence.id == evidence_id)).first()
    if e is None or not visible(s, e):
        raise EvidenceError("Document not found")
    return e


def for_party(s: Session, party_id: str, persona: Persona) -> dict:
    rows = list(s.scalars(select(Evidence).where(Evidence.party_id == party_id).order_by(Evidence.uploaded_at.desc(), Evidence.id)))
    shown = [e for e in rows if visible(s, e)]
    return {"documents": [row(s, e) for e in shown], "hidden": len(rows) - len(shown), "types": reference.codes(s, "document_types"),
            "patterns": [{"id": k, "label": label} for k, (_, label) in PATTERNS.items()], "can_change": persona.can("work") and not persona.read_only,
            "max_bytes": MAX_BYTES}


def decode(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except Exception:
        raise EvidenceError("The file content could not be decoded") from None
