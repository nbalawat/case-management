"""Reports to the sanctions authority, built from a hold that ended in a block or a rejection."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import holds, reference
from ..domain.models import Filing, Hold, Party, Transaction, Watchlist, WatchlistEntry, new_id, now
from ..domain.personas import Persona
from ..filing import report as filing_report
from ..filing import service as filing
from ..filing.formats import SANCTIONS_REGIME, Format, format_for
from ..visibility import system_work
from ..workdays import add_business_days
from . import screening

KIND = {"blocked": "BLOCK", "rejected": "REJECT"}
DESCRIBE = {"payment": "Funds of a payment", "account": "Funds held in an account", "card": "Card and the account behind it", "online_access": "Access to accounts"}


class ReportError(ValueError):
    pass


def _body(s: Session, h: Hold, prepared_at: datetime) -> dict:
    party = s.scalars(select(Party).where(Party.id == h.party_id)).one()
    txn = s.scalars(select(Transaction).where(Transaction.id == h.target_id, Transaction.party_id == h.party_id)).first() if h.target == "payment" else None
    hit = next(iter(sorted(screening.candidates(s, h.case_id), key=lambda c: -c["score"])), None)
    target = {}
    if hit:
        e = s.scalars(select(WatchlistEntry).where(WatchlistEntry.id == hit["entry_id"])).one()
        target = {"name": e.name, "reference": e.ref, "programs": e.programs, "countries": e.countries, "matched_name": hit["subject"], "similarity": hit["score"],
                  "list": s.scalars(select(Watchlist.name).where(Watchlist.id == e.list_id)).one()}
    inst = filing_report.profile(h.tenant_id)["institution"]
    day = (h.decided_at or prepared_at).isoformat()
    return {"kind": KIND[h.status], "case_id": h.case_id, "hold_id": h.id, "prepared_at": prepared_at.isoformat(), "institution": inst,
            "subject": filing_report._subject(party), "target": target,
            "property": {"description": DESCRIBE[h.target], "kind": h.target, "identifier": h.target_id or "all accounts", "value": round(h.amount, 2),
                         "currency": txn.currency if txn else "USD", "date": day, "location": ", ".join(x for x in (inst.get("city"), inst.get("country")) if x)},
            "activity": {"from": day[:10], "to": day[:10], "total": round(h.amount, 2), "currency": txn.currency if txn else "USD", "count": 1 if txn else 0,
                         "categories": ["sanctions"], "channels": [txn.channel] if txn else []},
            "transactions": [filing_report._txn(txn)] if txn else [], "transactions_omitted": 0, "narrative": h.decision_note, "prior_reference": ""}


def _format(tenant_id: str, kind: str) -> Format:
    return format_for(SANCTIONS_REGIME.get(filing_report.profile(tenant_id)["regime"], "sanctions"), kind)


def _rebuild(s: Session, f: Filing, ts: datetime) -> tuple[Format, dict]:
    h = s.scalars(select(Hold).where(Hold.id == f.payload["hold_id"])).one()
    return _format(f.tenant_id, f.kind), _body(s, h, ts)


filing.REBUILD.update({"BLOCK": _rebuild, "REJECT": _rebuild})


@system_work
def owed(s: Session, at: datetime | None = None) -> list[dict]:
    """Holds that ended in a block or a rejection and have no report yet, with the day each report is due."""
    at = at or now()
    days = int(reference.setting(s, "screening_rules", "report_business_days", "value", 10))
    covered = {f.payload.get("hold_id") for f in s.scalars(select(Filing).where(Filing.kind.in_(tuple(KIND.values())), Filing.status != "superseded"))}
    names = dict(s.execute(select(Party.id, Party.name)).all())
    out = []
    for h in s.scalars(select(Hold).where(Hold.status.in_(tuple(KIND))).order_by(Hold.decided_at)):
        if h.id in covered or holds.kind(s, h) != "sanctions":
            continue  # a fraud or legal hold that was kept is not sanctions property
        due = add_business_days(h.decided_at, days)
        out.append({"hold_id": h.id, "case_id": h.case_id, "party_id": h.party_id, "party_name": names.get(h.party_id, h.party_id), "kind": KIND[h.status],
                    "amount": h.amount, "target": h.target, "decided_at": h.decided_at.isoformat(), "due": due.isoformat(), "overdue": due < at})
    return out


@system_work
def prepare(s: Session, hold_id: str, persona: Persona, ts: datetime | None = None) -> Filing:
    """Write the report for one blocked or rejected hold. It is checked, and waits for a person to send it."""
    if persona.read_only or not persona.can(filing_report.SUBMIT):
        raise ReportError("Preparing a report to the authority needs the permission to send regulatory reports")
    h = s.scalars(select(Hold).where(Hold.id == hold_id)).first()
    if h is None:
        raise ReportError("Hold not found")
    if h.status not in KIND or holds.kind(s, h) != "sanctions":
        raise ReportError("Only a sanctions hold that ended blocked or rejected is reported")
    if any(f.payload.get("hold_id") == h.id for f in s.scalars(select(Filing).where(Filing.kind.in_(tuple(KIND.values())), Filing.status != "superseded"))):
        raise ReportError("A report for this hold already exists")
    ts = ts or now()
    kind, fmt = KIND[h.status], _format(h.tenant_id, KIND[h.status])
    days = int(reference.setting(s, "screening_rules", "report_business_days", "value", 10))
    f = Filing(id=new_id(kind.lower()), case_id=h.case_id, party_id=h.party_id, kind=kind, filed_by=persona.id, filed_at=ts, due=add_business_days(h.decided_at, days),
               sha256="", history=[], problems=[])
    filing._write(f, fmt, _body(s, h, ts))
    s.add(f)
    s.flush()
    filing._note(s, f, persona.id, "prepared", ts, format=fmt.id, problems=len(f.problems))
    return f
