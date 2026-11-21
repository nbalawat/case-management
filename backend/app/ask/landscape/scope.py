"""What one person may see, beyond cases: customers, transactions and every record that hangs off a case.

Cases follow the platform's visibility rules (app/visibility.py). For everything else Ask applies one rule here:

  * a person whose roles see every case sees every customer and their transactions and accounts, except that
    customer reach by attribute (booking jurisdiction, segment, a relationship manager's own book) always holds;
  * a person whose roles are limited sees only customers on cases or alerts they can see, within that same reach;
  * alerts, holds, disputes, filings, KYC triggers, evidence and agent work follow their case; a record with no
    case (a CTR, an alert still in intake) follows its customer;
  * figures derived from cases ("has a SAR", "open cases") count only cases the person can see, so a restricted
    case never shows through a customer's totals.

The rule is computed from the person, not read from the session, so it holds whatever the session was bound to.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import false, or_, select, true, union
from sqlalchemy.orm import Session

from ...domain.access import Persona
from ...visibility import Viewer, viewer_of, visible_ids, visible_party_ids


@dataclass
class Scope:
    s: Session
    asker: Persona

    def __post_init__(self):
        self.viewer: Viewer = viewer_of(self.asker)
        self._cases = None
        self._parties = None

    @property
    def sees_every_case(self) -> bool:
        return self.viewer.unlimited

    @property
    def sees_every_customer(self) -> bool:
        return self.viewer.views is None and self.viewer.customers is None

    def cases(self):
        """A subquery of the case ids this person may see."""
        if self._cases is None:
            self._cases = visible_ids(self.s, self.viewer)
        return self._cases

    def case_ok(self, case_col):
        return true() if self.sees_every_case else case_col.in_(self.cases())

    def parties(self):
        """A subquery of the customer ids this person may see, or None for every customer."""
        if self.sees_every_customer:
            return None
        if self._parties is None:
            from ...domain.models import Alert, Case

            reach = visible_party_ids(self.s, self.viewer) if self.viewer.customers is not None else None
            if self.viewer.views is None:
                self._parties = reach.subquery()
            else:
                on_cases = select(Case.party_id).where(Case.id.in_(self.cases()))
                on_alerts = select(Alert.party_id).where(or_(Alert.case_id.is_(None), Alert.case_id.in_(self.cases())))
                if reach is not None:
                    on_alerts = on_alerts.where(Alert.party_id.in_(reach))  # cases already respect reach
                self._parties = union(on_cases, on_alerts).subquery()
        return self._parties

    def party_ok(self, party_col):
        p = self.parties()
        return true() if p is None else party_col.in_(select(p.c[0]))

    def case_or_party_ok(self, case_col, party_col):
        """For records that may belong to no case: with a case, the case decides; without one, the customer does."""
        if self.sees_every_case:
            return true()
        return or_(case_col.in_(self.cases()), (case_col.is_(None) & self.party_ok(party_col)) if party_col is not None else false())
