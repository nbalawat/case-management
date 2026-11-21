"""Automatic actions a stage takes when a case enters it: the `on_enter` list of a case type's state.

    states:
      triage:
        label: Triage
        on_enter:
          - {do: screen_party}
          - {do: notify, role: team_lead, message: A new case is waiting}

Each action is a small, registered `Effect`. They run inside the step that moves the case, in the same transaction,
so they commit or roll back with it. That holds on both workflow engines: the built-in engine applies a step in the
caller's transaction and the Temporal engine applies it inside one activity, once per step id. Nothing here talks to
the outside world directly: notifications and integration events are case events, which the outbox publishes to the
event backbone after commit.

Other modules add their own actions with `register(Effect(...))`; the workflow designer offers every registered one.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..domain.models import Case, InfoRequest, new_id

ACTOR = "workflow"
PRIORITIES = ("low", "medium", "high", "critical")


class EffectError(ValueError):
    """An `on_enter` entry that could never run: unknown action or bad settings. Raised at publish time."""


@dataclass(frozen=True)
class Field:
    """One setting of an action, described well enough for the designer to draw a form for it."""

    key: str
    label: str
    kind: str = "text"  # text | textarea | number | select | multiselect | bool
    required: bool = False
    options: str | tuple[tuple[str, str], ...] = ()  # a fixed list, or the name of a reference list: roles, queues, agents, recipients
    default: Any = None
    help: str = ""

    def as_dict(self) -> dict:
        opts = self.options if isinstance(self.options, str) else [{"id": k, "label": v} for k, v in self.options]
        return {"key": self.key, "label": self.label, "kind": self.kind, "required": self.required, "options": opts,
                "default": self.default, "help": self.help}


@dataclass(frozen=True)
class Effect:
    id: str
    label: str
    description: str
    run: Callable[[Session, Case, dict, datetime], dict | None]
    fields: tuple[Field, ...] = ()
    icon: str = "zap"
    check: Callable[[dict], str | None] = field(default=lambda cfg: None)

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "description": self.description, "icon": self.icon,
                "fields": [f.as_dict() for f in self.fields]}


REGISTRY: dict[str, Effect] = {}


def register(effect: Effect) -> Effect:
    REGISTRY[effect.id] = effect
    return effect


def validate(entries, where: str) -> None:
    """Check an `on_enter` list. Raises EffectError naming the stage and the problem in plain words."""
    if entries is None:
        return
    if not isinstance(entries, list):
        raise EffectError(f"{where}: on_enter must be a list of actions")
    for e in entries:
        if not isinstance(e, dict) or "do" not in e:
            raise EffectError(f"{where}: each automatic action needs 'do'")
        eff = REGISTRY.get(e["do"])
        if eff is None:
            raise EffectError(f"{where}: unknown automatic action '{e['do']}'. Known: {', '.join(REGISTRY)}")
        for f in eff.fields:
            if f.required and e.get(f.key) in (None, "", []):
                raise EffectError(f"{where}: '{eff.label}' needs {f.label.lower()}")
        why = eff.check(e)
        if why:
            raise EffectError(f"{where}: '{eff.label}' {why}")


def run_on_enter(s: Session, case: Case, defn: dict, state: str, ts: datetime) -> list[dict]:
    """Apply the actions a stage declares, in order, and record each one on the case's audit trail."""
    from .service import record

    done = []
    for entry in defn["states"][state].get("on_enter") or []:
        eff = REGISTRY[entry["do"]]
        result = eff.run(s, case, entry, ts) or {}
        record(s, case.id, ACTOR, "system", "workflow.action", {"stage": state, "do": eff.id, "label": eff.label, **result}, ts=ts)
        done.append({"do": eff.id, "label": eff.label, **result})
    return done


# --- the built-in actions ----------------------------------------------------------------------------------------------
def _notify(s: Session, case: Case, cfg: dict, ts: datetime) -> dict:
    from ..domain.personas import policy_of
    from .service import record

    role = cfg["role"]
    people = sorted(p.id for p in policy_of(case.tenant_id).holders(role))
    message = (cfg.get("message") or f"Case {case.id} needs attention").strip()
    record(s, case.id, ACTOR, "system", "workflow.notified", {"role": role, "people": people, "message": message}, ts=ts)
    return {"role": role, "people": people, "message": message}


def _raise_priority(s: Session, case: Case, cfg: dict, ts: datetime) -> dict:
    before = case.priority
    to = cfg.get("to") or "one_level"
    if to == "one_level":
        i = PRIORITIES.index(before) if before in PRIORITIES else 1
        to = PRIORITIES[min(i + 1, len(PRIORITIES) - 1)]
    if PRIORITIES.index(to) > (PRIORITIES.index(before) if before in PRIORITIES else 0):
        case.priority = to  # an automatic action only ever raises priority
    return {"from": before, "to": case.priority}


def _emit(s: Session, case: Case, cfg: dict, ts: datetime) -> dict:
    from .service import record

    name = cfg["event"].strip()
    record(s, case.id, ACTOR, "system", "workflow.event", {"event": name, "state": case.state, "case_type": case.case_type,
                                                          "party_id": case.party_id, "priority": case.priority}, ts=ts)
    return {"event": name}


def _request(s: Session, case: Case, cfg: dict, ts: datetime) -> dict:
    from .requests import RECIPIENTS
    from .service import record

    recipient = cfg.get("recipient") or "customer"
    if recipient not in RECIPIENTS:
        raise EffectError(f"Unknown recipient '{recipient}'")
    days = max(1, min(int(cfg.get("due_days") or 5), 60))
    r = InfoRequest(id=new_id("rfi"), case_id=case.id, recipient=recipient, question=cfg["question"].strip(), raised_by=ACTOR,
                    created=ts, due=ts + timedelta(days=days))
    s.add(r)
    s.flush()
    record(s, case.id, ACTOR, "system", "request.raised", {"request_id": r.id, "recipient": recipient, "due": r.due.isoformat()}, ts=ts)
    return {"request_id": r.id, "recipient": recipient, "due_days": days}


def _screen(s: Session, case: Case, cfg: dict, ts: datetime) -> dict:
    from ..sanctions import lists
    from ..sanctions.screening import best_match

    w = lists.watch(s, ts)
    name = case.party.name if case.party is not None else ""
    hit = best_match(name, w.names, w.threshold or 0.86, w.exempt) if name else None
    result = {"hit": hit is not None, "name": name, "entry": hit[1] if hit else None, "score": round(hit[2], 2) if hit else None,
              "at": ts.isoformat()}
    case.data = dict(case.data or {}) | {"screening": result}
    return {"hit": result["hit"], "entry": result["entry"], "score": result["score"]}


def _agents(s: Session, case: Case, cfg: dict, ts: datetime) -> dict:
    """Refresh the agent briefing. Uses the workflow engine's brief when it has one, so Temporal can run it as its own job."""
    try:
        from ..workflow import engine  # type: ignore[attr-defined]

        brief = engine().brief  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        from ..agents.runtime import brief
    s.flush()
    run = brief(s, case, ts=ts)
    return {"run_id": getattr(run, "id", None)}


def _check_event(cfg: dict) -> str | None:
    import re

    return None if re.fullmatch(r"[a-z][a-z0-9_.-]{1,60}", str(cfg.get("event", ""))) else "needs an event name in lower case, such as case.escalated"


register(Effect("notify", "Notify a role", "Tell everyone holding a role that the case has arrived. Sent as an event on the backbone.", _notify,
                (Field("role", "Role to notify", "select", True, "roles"),
                 Field("message", "Message", "textarea", False, (), "", "Shown to the people notified")), icon="bell"))
register(Effect("raise_priority", "Raise priority", "Raise the case's priority on arrival. Never lowers it.", _raise_priority,
                (Field("to", "Raise to", "select", False, (("one_level", "One level up"), ("high", "High"), ("critical", "Critical")), "one_level"),),
                icon="arrow-up"))
register(Effect("emit_event", "Send an integration event", "Publish a named event to the event backbone for another system to act on.", _emit,
                (Field("event", "Event name", "text", True, (), "case.stage_reached", "Lower case, dots allowed"),), icon="radio",
                check=_check_event))
register(Effect("request_information", "Ask for information", "Raise an information request with a due date, as an investigator would.",
                _request,
                (Field("recipient", "Ask", "select", True, "recipients", "customer"),
                 Field("question", "Question", "textarea", True),
                 Field("due_days", "Due in (days)", "number", False, (), 5)), icon="message-square"))
register(Effect("screen_party", "Screen against watchlists", "Screen the customer's name against the client's lists. The result becomes "
                "the fact 'screening_hit' for decisions.", _screen, (), icon="shield-search"))
register(Effect("run_agents", "Refresh the agent briefing", "Run the case type's agents again so the briefing reflects this stage.", _agents,
                (), icon="bot"))


def _screening_hit(s: Session, case: Case):
    result = (case.data or {}).get("screening")
    return None if result is None else bool(result.get("hit"))


def _register_facts() -> None:
    from .conditions import register_fact

    register_fact("screening_hit", "The last watchlist screening of the customer found a match", _screening_hit, "bool")


_register_facts()
