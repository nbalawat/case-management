"""The life of a report: prepared, checked, sent, answered, and corrected if it must be.

Once a report has been sent, what it said is fixed: the document is kept with its hash, the narrative it quotes is
locked, and the only way to change either is an amendment that points back at the original.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Case, Filing, new_id, now
from ..domain.personas import Persona, policy_of
from ..tenancy import tenant_of
from ..visibility import system_work
from . import report as reports
from . import validation
from .formats import FORMATS, SUSPICION_KIND, Format, format_for
from .transmit import CHANNELS, TransmitError

SENT = ("submitted", "acknowledged")
CTR_DAYS = 15  # a CTR is due fifteen days after the cash moved
CONTINUING_DAYS = 120  # activity that carries on is reported again within 120 days of the last report


class FilingError(Exception):
    pass


class SendFailed(FilingError):
    """The report was ready but the channel did not take it. Unlike a refusal, trying again later may work."""


# Kinds of report built outside this module register how to rebuild one: kind -> (session, filing, timestamp) -> (format, body).
REBUILD: dict[str, Callable[[Session, Filing, datetime], tuple[Format, dict]]] = {}
SUSPICION_KINDS = tuple(SUSPICION_KIND.values())


def _note(s: Session, f: Filing, persona_id: str, what: str, ts: datetime, **detail: object) -> None:
    f.history = [*f.history, {"at": ts.isoformat(), "by": persona_id, "what": what, **detail}]
    if f.case_id:
        from ..cases.service import record

        record(s, f.case_id, persona_id, "human", f"filing.{what}", {"filing_id": f.id, **detail}, ts=ts)


def _write(f: Filing, fmt: Format, report: dict) -> None:
    """Render the report, then check it twice: against what the regulator says in words, and against its published schema."""
    f.payload, f.format = report, fmt.id
    f.document = fmt.render(report)
    checked = validation.validate(fmt.id, report.get("institution") or {}, f.document)
    f.validation = checked.to_dict()
    f.problems = fmt.check(report) + validation.problems(checked)
    f.sha256 = hashlib.sha256(f.document.encode()).hexdigest()


def locked(s: Session, case_id: str) -> bool:
    """True once a report quoting this case's narrative has gone to the regulator."""
    return s.scalars(select(Filing.id).where(Filing.case_id == case_id, Filing.kind.in_(SUSPICION_KINDS), Filing.status.in_(SENT))).first() is not None


@system_work
def prepare_suspicion(s: Session, case: Case, persona: Persona, ts: datetime | None = None, amends: Filing | None = None) -> Filing:
    """Write the SAR or STR for a case in the client's own regime, check it, and send it if nothing stands in the way."""
    ts = ts or now()
    prof = reports.profile(case.tenant_id)
    kind = SUSPICION_KIND.get(prof["regime"], "SAR")
    fmt = format_for(prof["regime"], kind)
    f = Filing(id=new_id(kind.lower()), case_id=case.id, party_id=case.party_id, kind=kind, filed_by=persona.id, filed_at=ts,
               due=case.sla_due, amends_id=amends.id if amends else "", sha256="", history=[], problems=[])
    _write(f, fmt, reports.suspicious_activity(s, case, kind, ts, prior_reference=amends.reference if amends else ""))
    s.add(f)
    s.flush()
    _note(s, f, persona.id, "prepared", ts, format=fmt.id, problems=len(f.problems), **({"amends": amends.id} if amends else {}))
    if not f.problems:
        _send(s, f, persona, ts)
    return f


@system_work
def prepare_ctr(s: Session, party_id: str, day: str, persona: Persona, ts: datetime | None = None) -> Filing:
    ts = ts or now()
    tenant_id = tenant_of(s)
    if s.scalars(select(Filing.id).where(Filing.kind == "CTR", Filing.party_id == party_id, Filing.status != "superseded")
                 .where(Filing.payload["activity"]["from"].as_string() == day)).first():
        raise FilingError("A CTR for this customer and day already exists")
    prof = reports.profile(tenant_id)
    fmt = format_for(prof["regime"], "CTR")
    body = reports.currency_transaction(s, tenant_id, party_id, day, ts)
    if body is None:
        raise FilingError("No cash transactions for that customer on that day")
    f = Filing(id=new_id("ctr"), case_id=None, party_id=party_id, kind="CTR", filed_by=persona.id, filed_at=ts,
               due=datetime.fromisoformat(day) + timedelta(days=CTR_DAYS), sha256="", history=[], problems=[])
    _write(f, fmt, body)
    s.add(f)
    s.flush()
    _note(s, f, persona.id, "prepared", ts, format=fmt.id, problems=len(f.problems))
    return f


def _send(s: Session, f: Filing, persona: Persona, ts: datetime) -> None:
    s.flush()  # the report must be saved, and so carry its client, before it goes anywhere
    channel = CHANNELS.get(reports.profile(f.tenant_id)["channel"])
    if channel is None:
        f.problems = [*f.problems, "The client's profile names a sending channel this platform does not have"]
        return
    try:
        f.receipt = channel.send(f, FORMATS[f.format].extension)
    except TransmitError as e:
        _note(s, f, persona.id, "send_failed", ts, reason=str(e))
        return
    f.status, f.submitted_by, f.submitted_at, f.channel = "submitted", persona.id, ts, channel.id
    _note(s, f, persona.id, "submitted", ts, channel=channel.id, sha256=f.sha256)


def _get(s: Session, filing_id: str) -> Filing:
    f = s.scalars(select(Filing).where(Filing.id == filing_id)).first()
    if f is None:
        raise FilingError("No such filing")
    return f


@system_work
def recheck(s: Session, filing_id: str, persona: Persona, ts: datetime | None = None) -> Filing:
    """Rebuild a report that has not been sent, for example after the institution's details were completed."""
    f, ts = _get(s, filing_id), ts or now()
    if f.status != "prepared":
        raise FilingError("Only a report that has not been sent can be rebuilt")
    prior = f.payload.get("prior_reference", "")
    if f.kind in REBUILD:
        fmt, body = REBUILD[f.kind](s, f, ts)
        _write(f, fmt, body | {"prior_reference": prior})
        _note(s, f, persona.id, "rechecked", ts, problems=len(f.problems))
        return f
    if f.case_id:
        case = s.scalars(select(Case).where(Case.id == f.case_id)).one()
        body = reports.suspicious_activity(s, case, f.kind, ts, prior_reference=prior)
    else:
        body = reports.currency_transaction(s, f.tenant_id, f.party_id, f.payload["activity"]["from"], ts, prior_reference=prior)
    _write(f, format_for(reports.profile(f.tenant_id)["regime"], f.kind), body)
    _note(s, f, persona.id, "rechecked", ts, problems=len(f.problems))
    return f


@system_work
def submit(s: Session, filing_id: str, persona: Persona, ts: datetime | None = None) -> Filing:
    f, ts = _get(s, filing_id), ts or now()
    if f.status != "prepared":
        raise FilingError(f"This report is already {f.status}")
    if f.problems:
        raise FilingError("This report cannot be sent yet: " + "; ".join(f.problems))
    ms = policy_of(f.tenant_id).managed_service
    if persona.external and ms and "submit_filings" in ms.client_signoff:
        raise FilingError(f"Client sign-off: reports are sent to the regulator by the client's own officers, not the {ms.provider} team")
    _send(s, f, persona, ts)
    if f.status != "submitted":
        if f.history and f.history[-1].get("what") == "send_failed":
            raise SendFailed(f.history[-1].get("reason") or "The channel did not take the report")
        raise FilingError("; ".join(f.problems) or "The report was not sent")
    return f


@system_work
def acknowledge(s: Session, filing_id: str, reference: str, persona: Persona, ts: datetime | None = None) -> Filing:
    """The regulator accepted it and gave it a number."""
    f, ts = _get(s, filing_id), ts or now()
    if f.status != "submitted":
        raise FilingError("Only a report that has been sent can be acknowledged")
    if not reference.strip():
        raise FilingError("Enter the reference the regulator gave the report")
    f.status, f.reference, f.answered_at = "acknowledged", reference.strip()[:60], ts
    _note(s, f, persona.id, "acknowledged", ts, reference=f.reference)
    return f


@system_work
def reject(s: Session, filing_id: str, reason: str, persona: Persona, ts: datetime | None = None) -> Filing:
    """The regulator refused it. The narrative unlocks so the report can be corrected and sent again."""
    f, ts = _get(s, filing_id), ts or now()
    if f.status != "submitted":
        raise FilingError("Only a report that has been sent can be rejected")
    if not reason.strip():
        raise FilingError("Enter what the regulator said was wrong")
    f.status, f.rejection, f.answered_at = "rejected", reason.strip(), ts
    _note(s, f, persona.id, "rejected", ts, reason=f.rejection)
    return f


@system_work
def amend(s: Session, filing_id: str, persona: Persona, narrative: str | None = None, ts: datetime | None = None) -> Filing:
    """Correct a report the regulator has answered. The original is kept and marked as replaced."""
    old, ts = _get(s, filing_id), ts or now()
    if old.status not in ("acknowledged", "rejected"):
        raise FilingError("Only a report the regulator has answered can be amended")
    old.status = "superseded"
    if old.kind in REBUILD:
        fmt, body = REBUILD[old.kind](s, old, ts)
        new = Filing(id=new_id(old.kind.lower()), case_id=old.case_id, party_id=old.party_id, kind=old.kind, filed_by=persona.id, filed_at=ts, due=old.due,
                     amends_id=old.id, sha256="", history=[], problems=[])
        _write(new, fmt, body | {"prior_reference": old.reference})
        s.add(new)
        s.flush()
        _note(s, new, persona.id, "prepared", ts, format=fmt.id, problems=len(new.problems), amends=old.id)
    elif old.case_id:
        case = s.scalars(select(Case).where(Case.id == old.case_id)).one()
        if narrative is not None and narrative.strip():
            case.narrative, case.drafted_by = narrative.strip(), persona.id
        new = prepare_suspicion(s, case, persona, ts, amends=old)
    else:
        s.flush()
        new = prepare_ctr(s, old.party_id, old.payload["activity"]["from"], persona, ts)
        new.amends_id = old.id
    _note(s, old, persona.id, "superseded", ts, by_filing=new.id)
    return new


def row(f: Filing, names: dict[str, str] | None = None) -> dict:
    fmt = FORMATS.get(f.format)
    act = (f.payload or {}).get("activity") or {}
    continuing = f.answered_at + timedelta(days=CONTINUING_DAYS) if f.status == "acknowledged" and f.kind in ("SAR", "STR") and f.answered_at else None
    return {"id": f.id, "kind": f.kind, "status": f.status, "format": f.format, "format_title": fmt.title if fmt else "Record only",
            "case_id": f.case_id, "party_id": f.party_id, "subject": ((f.payload or {}).get("subject") or {}).get("name") or (names or {}).get(f.party_id, ""),
            "amount": act.get("total"), "from": act.get("from"), "to": act.get("to"),
            "prepared_by": f.filed_by, "prepared_at": f.filed_at.isoformat(), "due": f.due.isoformat() if f.due else None,
            "submitted_by": f.submitted_by, "submitted_at": f.submitted_at.isoformat() if f.submitted_at else None, "channel": f.channel,
            "receipt": f.receipt, "reference": f.reference, "answered_at": f.answered_at.isoformat() if f.answered_at else None,
            "rejection": f.rejection, "amends_id": f.amends_id, "problems": f.problems, "sha256": f.sha256,
            "continuing_review_due": continuing.isoformat() if continuing else None, "validation": f.validation or {}}


def detail(s: Session, filing_id: str) -> dict:
    f = _get(s, filing_id)
    return row(f) | {"document": f.document, "history": f.history, "report": f.payload,
                     "intact": hashlib.sha256(f.document.encode()).hexdigest() == f.sha256}


def overview(s: Session, tenant_id: str) -> dict:
    """Every report this person may see, what is waiting on whom, and the cash days no CTR covers yet."""
    filings = list(s.scalars(select(Filing).order_by(Filing.filed_at.desc())))
    covered = {(f.party_id, (f.payload.get("activity") or {}).get("from")) for f in filings if f.kind == "CTR" and f.status != "superseded"}
    prof = reports.profile(tenant_id)
    channel = CHANNELS.get(prof["channel"])
    counts = {k: sum(1 for f in filings if f.status == k) for k in ("prepared", "submitted", "acknowledged", "rejected", "superseded")}
    missing = [x.replace("_", " ") for x in reports.INSTITUTION_FIELDS if not str(prof["institution"].get(x) or "").strip()]
    return {"profile": {"regime": prof["regime"], "institution": prof["institution"], "missing": missing,
                        "channel": prof["channel"], "channel_title": channel.title if channel else "Unknown channel",
                        "formats": [{"id": f.id, "title": f.title, "kinds": list(f.kinds)} for f in FORMATS.values() if f.regime == prof["regime"]]},
            "counts": counts, "filings": [row(f) for f in filings],
            "cash_days": [d for d in reports.cash_days(s) if (d["party_id"], d["day"]) not in covered]}
