"""Alert hub: turns alerts from any engine into entity-centric cases."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import case_type_for_scenario, load_case_types
from ..cases.service import create_case, record
from ..tenancy import tenant_of
from ..domain.models import Account, Alert, Case, Party, new_id, now
from ..visibility import system_work

PRIORITY_ORDER = ["low", "medium", "high", "critical"]


def priority_for(score: float, n_alerts: int = 1) -> str:
    base = 3 if score >= 0.9 else 2 if score >= 0.75 else 1 if score >= 0.6 else 0
    return PRIORITY_ORDER[min(base + (1 if n_alerts >= 3 else 0), 3)]


@system_work
def hibernation_rule(s: Session, a: Alert) -> dict | None:
    """The client's rule that lets this alert wait, if any: weak enough, and of a scenario the rule names."""
    from .. import reference

    for rule in reference.codes(s, "alert_hibernation"):
        if rule.get("scenario", "*") in ("*", a.scenario) and a.score < float(rule.get("below", 0)):
            return rule
    return None


@system_work
def sweep_hibernated(s: Session, at: datetime | None = None) -> int:
    """Close the alerts that waited their full time with nothing else arriving for the customer."""
    at, n = at or now(), 0
    for a in s.scalars(select(Alert).where(Alert.status == "hibernated", Alert.wake_by < at)):
        a.status = "expired"
        a.policy_note = f"{a.policy_note} No further alert arrived, so it was closed on {at.date().isoformat()}."
        n += 1
    s.flush()
    return n


@system_work
def wake(s: Session, alert: Alert, persona_id: str) -> Case | None:
    """A person decides a waiting alert deserves a case now."""
    if alert.status not in ("hibernated", "expired"):
        return None
    alert.status, alert.wake_by = "new", None
    alert.policy_note = f"{alert.policy_note} Woken by {persona_id}."
    return next(iter(route_alerts(s, [alert], screened=True, woken=True)), None)


@system_work
def route_alerts(s: Session, alerts: list[Alert], screened: bool = False, woken: bool = False) -> list[Case]:
    """Attach each alert to the party's open case of the right type, or open a new case.

    Alerts pass the triage gate first, where an agent may close one if the client's policy lets it. `screened` skips
    the gate for an alert a person has just reopened.
    """
    if not screened:
        from ..agents.triage import gate

        alerts = gate(s, alerts)
    touched: dict[str, Case] = {}
    tenant = tenant_of(s)
    types = load_case_types(tenant)
    for a in sorted(alerts, key=lambda x: x.created):
        ct = case_type_for_scenario(tenant, a.scenario)
        open_states = [k for k, v in ct["states"].items() if v.get("kind", "open") != "closed"]
        case = s.scalars(select(Case).where(Case.party_id == a.party_id, Case.case_type == ct["case_type"],
                                            Case.state.in_(open_states))).first()
        if case is None:
            rule = None if woken else hibernation_rule(s, a)
            if rule is not None:
                days = int(rule.get("days", 30))
                a.status, a.wake_by = "hibernated", a.created + timedelta(days=days)
                a.policy_note = f"Held by the rule '{rule['label']}': score {a.score:.2f} is below {float(rule['below']):.2f}, so it waits {days} days for another alert."
                continue
            case = create_case(s, types[ct["case_type"]], a.party_id, priority_for(a.score), created=a.created)
            s.flush()
            # Anything that was waiting for this customer joins the case that has now opened.
            scenarios = {x for x in {h.scenario for h in s.scalars(select(Alert).where(Alert.party_id == a.party_id, Alert.status == "hibernated"))}
                         if case_type_for_scenario(tenant, x)["case_type"] == ct["case_type"]}
            for h in s.scalars(select(Alert).where(Alert.party_id == a.party_id, Alert.status == "hibernated", Alert.scenario.in_(scenarios))) if scenarios else []:
                h.case_id, h.status, h.wake_by = case.id, "in_case", None
                h.policy_note = f"{h.policy_note} Joined {case.id} when a further alert arrived."
                record(s, case.id, "alert-hub", "system", "alert.woken", {"alert_id": h.id, "scenario": h.scenario, "score": h.score}, ts=a.created)
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


@system_work
def ingest_external_alert(s: Session, *, source: str, external_id: str, scenario: str, party_id: str, score: float,
                          explanation: str, party_name: str | None = None, txn_ids: list[str] | None = None,
                          created: datetime | None = None) -> tuple[Alert, Case | None]:
    """Overlay mode: accept an alert from an incumbent engine in the canonical format.

    Idempotent on (source, external_id): a replayed alert returns the stored one and opens nothing.
    """
    key = f"ext:{source}:{external_id}"
    existing = s.scalars(select(Alert).where(Alert.dedupe_key == key)).first()
    if existing:
        return existing, None
    if s.get(Party, {"tenant_id": tenant_of(s), "id": party_id}) is None:
        s.add(Party(id=party_id, name=party_name or f"Customer {party_id}", risk_rating="medium"))
        s.add(Account(id=f"A-{party_id}", party_id=party_id))
        s.flush()
    alert = Alert(id=new_id("alr"), dedupe_key=key, source=source, scenario=scenario, scenario_version="external",
                  party_id=party_id, score=round(max(0.0, min(score, 1.0)), 2), explanation=explanation, txn_ids=txn_ids or [], created=created or now())
    s.add(alert)
    s.flush()
    return alert, next(iter(route_alerts(s, [alert])), None)  # no case when an agent closed the alert inside policy
