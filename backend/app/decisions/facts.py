"""What a table's inputs may name. A table declares its context; each context has a fixed vocabulary of facts and the
code that reads them, so an input can never reach into the database by itself.

  case       a case and what is attached to it (the facts transition conditions use, and a few more)
  customer   a customer as the risk model sees them: its score, and which risk factors apply
  inputs     nothing is read: the caller passes the inputs (for tables other components call by id)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Alert, Case, Party
from ..visibility import system_work
from .feel import BOOL, NUMBER, TEXT


@dataclass(frozen=True)
class Fact:
    name: str
    label: str
    kind: str
    values: tuple = ()


CASE_FACTS = (
    Fact("alerted_amount", "Total value of alerted transactions", NUMBER),
    Fact("alerts", "Number of alerts on the case", NUMBER),
    Fact("max_score", "Highest alert score on the case (0 to 1)", NUMBER),
    Fact("scenario", "Scenario of the first alert", TEXT),
    Fact("case_type", "Case type", TEXT),
    Fact("risk_rating", "Customer risk rating", TEXT, ("low", "medium", "high")),
    Fact("pep", "Customer is a politically exposed person", BOOL),
    Fact("customer_kind", "Customer kind: person or business", TEXT, ("person", "business")),
    Fact("customer_country", "Customer country (ISO code)", TEXT),
    Fact("priority", "Case priority", TEXT, ("low", "medium", "high", "critical")),
    Fact("agent_recommendation", "Agent recommendation: escalate or close", TEXT),
)
CUSTOMER_FACTS = (
    Fact("score", "Risk model score", NUMBER),
    Fact("model_band", "Band the scoring model gives", TEXT),
    Fact("pep", "Politically exposed person", BOOL),
    Fact("customer_kind", "Person or business", TEXT, ("person", "business")),
    Fact("country", "Country (ISO code)", TEXT),
    Fact("high_risk_country", "Linked to a high-risk country", BOOL),
    Fact("high_risk_business", "High-risk line of business", BOOL),
    Fact("adverse_media", "Adverse media found", BOOL),
    Fact("opaque_ownership", "Ownership cannot be traced", BOOL),
    Fact("prior_report", "A regulatory report was filed before", BOOL),
    Fact("cash_intensive", "Mostly cash", BOOL),
)
CONTEXTS: dict[str, tuple[Fact, ...]] = {"case": CASE_FACTS, "customer": CUSTOMER_FACTS, "inputs": ()}


@system_work
def case_facts(s: Session, case: Case) -> dict:
    from ..cases.service import facts as condition_facts

    out = condition_facts(s, case)
    alerts = list(s.scalars(select(Alert).where(Alert.case_id == case.id).order_by(Alert.created)))
    party: Party = case.party
    out |= {"max_score": max((a.score for a in alerts), default=0.0), "scenario": alerts[0].scenario if alerts else "",
            "case_type": case.case_type, "customer_kind": party.kind, "customer_country": party.country}
    return out


def customer_facts(result: dict, party: Party) -> dict:
    """From a risk model result (kyc.risk.score) and the customer."""
    applies = {f["code"]: f["applies"] for f in result.get("factors", [])}
    out = {"score": float(result["score"]), "model_band": result["band"], "pep": bool(party.pep), "customer_kind": party.kind, "country": party.country}
    for fact in CUSTOMER_FACTS:
        if fact.kind == BOOL and fact.name not in out:
            out[fact.name] = bool(applies.get(fact.name, False))
    return out


READERS: dict[str, Callable] = {"case": case_facts}


def vocabulary(context: str) -> list[dict]:
    return [{"name": f.name, "label": f.label, "kind": f.kind, "values": list(f.values)} for f in CONTEXTS.get(context, ())]
