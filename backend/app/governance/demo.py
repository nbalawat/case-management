"""Synthetic governance history for the demo: last month's access recertification, closed, with one change still outstanding."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..domain.models import side_ids
from ..domain.personas import policy_of
from ..tenancy import tenant_of
from . import recert


def seed(s: Session, end: datetime) -> None:
    tenant = tenant_of(s)
    if tenant != "meridian":
        return
    certifiers = [p for p in policy_of(tenant).people.values() if p.can(recert.CERTIFY)]
    if len(certifiers) < 2:
        return
    lead, other = certifiers[0], certifiers[1]
    started = end - timedelta(days=30)
    with side_ids(f"governance:{tenant}"):
        review = recert.start(s, lead, "Access recertification, October", 14, at=started)
        asked = False
        for n, item in enumerate(list(review.items)):
            who = other if item["person"] == lead.id else lead
            at = started + timedelta(days=2 + n % 6, hours=n)
            if not asked and "work" in item["permissions"] and len(item["queues"]) > 1 and item["person"] not in (lead.id, other.id):
                recert.decide(s, review.id, item["person"], "change", "No longer covers the second queue since the team was split. Remove it.", who, at=at)
                asked = True
            else:
                recert.decide(s, review.id, item["person"], "keep", "Checked against current duties with the line manager." if item["flags"] else "", who, at=at)
        recert.close(s, review.id, lead, at=started + timedelta(days=9))
        s.flush()
