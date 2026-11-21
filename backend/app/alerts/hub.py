"""Alert hub: turns alerts from any engine into entity-centric cases."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import case_type_for_scenario, load_case_types
from ..cases.service import create_case, record
from ..domain.models import Account, Alert, Case, Party, new_id

PRIORITY_ORDER = ["low", "medium", "high", "critical"]


def priority_for(score: float, n_alerts: int = 1) -> str:
    base = 3 if score >= 0.9 else 2 if score >= 0.75 else 1 if score >= 0.6 else 0
    return PRIORITY_ORDER[min(base + (1 if n_alerts >= 3 else 0), 3)]


def route_alerts(s: Session, alerts: list[Alert]) -> list[Case]:
    """Attach each alert to the party's open case of the right type, or open a new case."""
    touched: dict[str, Case] = {}
    types = load_case_types()
    for a in sorted(alerts, key=lambda x: x.created):
        ct = case_type_for_scenario(a.scenario)
        open_states = [k for k, v in ct["states"].items() if v.get("kind") == "open"]
        case = s.scalars(select(Case).where(Case.party_id == a.party_id, Case.case_type == ct["case_type"],
                                            Case.state.in_(open_states))).first()
        if case is None:
            case = create_case(s, types[ct["case_type"]], a.party_id, priority_for(a.score), created=a.created)
            s.flush()
        a.case_id, a.status = case.id, "in_case"
        n = len(s.scalars(select(Alert.id).where(Alert.case_id == case.id)).all()) + 1
        new_pri = priority_for(a.score, n)
        if PRIORITY_ORDER.index(new_pri) > PRIORITY_ORDER.index(case.priority):
            case.priority = new_pri
        record(s, case.id, "alert-hub", "system", "alert.attached",
               {"alert_id": a.id, "scenario": a.scenario, "score": a.score}, ts=a.created)
        touched[case.id] = case
    s.flush()
    return list(touched.values())


def ingest_external_alert(s: Session, *, source: str, external_id: str, scenario: str, party_id: str, score: float,
                          explanation: str, party_name: str | None = None, txn_ids: list[str] | None = None) -> tuple[Alert, Case | None]:
    """Overlay mode: accept an alert from an incumbent engine in the canonical format.

    Idempotent on (source, external_id): a replayed alert returns the stored one and opens nothing.
    """
    key = f"ext:{source}:{external_id}"
    existing = s.scalars(select(Alert).where(Alert.dedupe_key == key)).first()
    if existing:
        return existing, None
    if s.get(Party, party_id) is None:
        s.add(Party(id=party_id, name=party_name or f"Customer {party_id}", risk_rating="medium"))
        s.add(Account(id=f"A-{party_id}", party_id=party_id))
        s.flush()
    alert = Alert(id=new_id("alr"), dedupe_key=key, source=source, scenario=scenario, scenario_version="external",
                  party_id=party_id, score=round(max(0.0, min(score, 1.0)), 2), explanation=explanation, txn_ids=txn_ids or [])
    s.add(alert)
    s.flush()
    return alert, route_alerts(s, [alert])[0]
