"""Synthetic fraud work for the demo: holds with step-up checks, and disputes at each point from claim to loss.

Everything here is invented. It goes through the same functions a person's actions do, as the client's own people.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import holds
from ..cases.case_types import load_case_types
from ..domain.models import Account, Alert, Case, Transaction
from ..domain.personas import policy_of
from ..tenancy import tenant_of
from . import disputes


def seed(s: Session, end: datetime) -> dict:
    tenant = tenant_of(s)
    if "fraud_claim" not in load_case_types(tenant):
        return {"disputes": 0}
    analysts = [p for p in policy_of(tenant).people.values() if p.can("work") and not p.read_only and "fraud" in p.queues]
    if not analysts:
        return {"disputes": 0}
    fran, made = analysts[0], 0
    for case in s.scalars(select(Case).where(Case.case_type == "fraud_claim").order_by(Case.created, Case.id)):
        ids = [t for a in s.scalars(select(Alert).where(Alert.case_id == case.id).order_by(Alert.created)) for t in a.txn_ids]
        txns = list(s.scalars(select(Transaction).where(Transaction.id.in_(ids or [""])).order_by(Transaction.ts)))
        if not txns:
            continue
        rail, total, t0 = txns[0].channel, sum(t.amount for t in txns), case.created + timedelta(minutes=30)
        reason = "card_not_present" if rail == "card" else "account_takeover"
        account = s.scalars(select(Account.id).where(Account.party_id == case.party_id).order_by(Account.id)).first()
        if case.disposition == "fraud_confirmed":  # refunded: the money is chased after the case closed
            d = disputes.open_dispute(s, case, [t.id for t in txns], reason, fran, reported_at=case.created)
            disputes.credit(s, d.id, total, fran, final=True, note="Refunded in full when the claim was upheld.", at=case.closed)
            ref = f"CB-{d.id[-6:].upper()}" if rail == "card" else f"RCL-{d.id[-6:].upper()}"
            disputes.start_recovery(s, d.id, ref, fran, at=case.closed + timedelta(hours=3))
            if rail == "card":
                disputes.record_recovery(s, d.id, total, fran, "Merchant's bank accepted the chargeback.", at=end - timedelta(hours=30))
                disputes.close(s, d.id, fran, at=end - timedelta(hours=29))
            else:
                disputes.record_recovery(s, d.id, round(total * 0.37, 2), fran, "The receiving bank froze what was left in the account.", at=end - timedelta(hours=20))
                disputes.close(s, d.id, fran, note="The rest had been withdrawn in cash before the recall arrived.", at=end - timedelta(hours=18))
            made += 1
        elif case.closed is None and case.state == "refund_approval":
            d = disputes.open_dispute(s, case, [t.id for t in txns], reason, fran, reported_at=case.created)
            disputes.start_recovery(s, d.id, f"RCL-{d.id[-6:].upper()}", fran, "Recall sent to the receiving bank the same day.", at=t0 + timedelta(hours=1))
            made += 1
        elif case.closed is None and case.state == "customer_contact" and account:
            h = holds.place(s, case, "account", account, "account_takeover", fran, "Frozen until the customer confirms who made the payments.", at=t0)
            holds.challenge(s, h.id, "call_back", fran, at=t0 + timedelta(minutes=10))
        elif case.closed is None and case.state == "triage" and rail == "p2p":
            h = holds.place(s, case, "online_access", "", "account_takeover", fran, "New device, then two payments to a new payee within minutes.", at=end - timedelta(minutes=20))
            holds.challenge(s, h.id, "one_time_code", fran, at=end - timedelta(minutes=18))
            holds.challenge_result(s, h.id, False, fran, "Code sent to the phone on file was entered wrongly three times.", at=end - timedelta(minutes=12))
    s.flush()
    return {"disputes": made}
