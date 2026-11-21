"""Try a workflow on a sample case before publishing it.

A trial is real: it opens a case on the draft definition and takes each step through the same case core both workflow
engines call, with every guard (roles, four-eyes, required tasks, notes, reasons, conditions) and every automatic
action. Everything happens inside a savepoint that is always rolled back, and the draft definition is visible only to
the code running the trial, so nothing a trial does is ever seen by anyone else or reaches the event backbone.

To get past a step's requirements the trial does what a careful team would: ticks the stage's tasks, writes a note,
picks the first reason code, has one person write the narrative and a different person approve it, raises or answers
information requests. If the client's people cannot satisfy a step (for example four-eyes with only one approver), the
trial stops there and says why; that is usually the most useful thing it finds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases import conditions
from ..cases.case_types import trying
from ..cases.service import (TransitionError, allowed_transitions, complete_stage_tasks, create_case, definition, reasons_for,
                             save_narrative, transition)
from ..domain.models import Account, Alert, CaseEvent, InfoRequest, Party, Transaction, new_id, now
from ..domain.access import Persona
from ..visibility import system


@dataclass(frozen=True)
class Sample:
    name: str = "Sample Customer"
    amount: float = 25_000.0
    risk_rating: str = "medium"
    pep: bool = False
    priority: str = "medium"

    @classmethod
    def from_dict(cls, d: dict | None) -> Sample:
        d = d or {}
        return cls(name=str(d.get("name") or cls.name)[:120], amount=float(d.get("amount", cls.amount)),
                   risk_rating=str(d.get("risk_rating", cls.risk_rating)), pep=bool(d.get("pep", False)),
                   priority=str(d.get("priority", cls.priority)))

    def facts(self) -> dict:
        return {"alerted_amount": self.amount, "alerts": 1, "risk_rating": self.risk_rating, "pep": self.pep, "priority": self.priority,
                "agent_recommendation": ""}


MAX_STEPS = 60
MAX_PATHS = 50


def routes(definition_: dict, facts: dict) -> dict:
    """Every route from the start to an outcome that a case with these facts could take, visiting each stage at most once."""
    states = definition_["states"]
    found: list[list[dict]] = []

    def walk(cur: str, path: list[dict], seen: set[str]):
        if len(found) >= MAX_PATHS:
            return
        if states[cur].get("kind") == "closed":
            found.append(path)
            return
        for t in definition_["transitions"]:
            if t["from"] != cur or t["to"] in seen:
                continue
            if not conditions.evaluate(t.get("when"), facts) and _decidable(t.get("when"), facts):
                continue
            walk(t["to"], [*path, {"from": cur, "to": t["to"], "label": t["label"]}], seen | {t["to"]})

    walk(definition_["initial_state"], [], {definition_["initial_state"]})
    reached = {p[-1]["to"] for p in found if p}
    outcomes = [sid for sid, st in states.items() if st.get("kind") == "closed"]
    return {"routes": [{"steps": p, "outcome": states[p[-1]["to"]]["label"] if p else ""} for p in found],
            "outcomes_reached": [states[o]["label"] for o in outcomes if o in reached],
            "outcomes_not_reached": [states[o]["label"] for o in outcomes if o not in reached], "truncated": len(found) >= MAX_PATHS}


def _decidable(when, facts: dict) -> bool:
    """Whether every fact a condition needs is known from the sample. Unknown facts (set later, such as a screening result) keep the route open."""
    names = set()
    for item in when or []:
        for expr in (item["any"] if isinstance(item, dict) else [item]):
            names.add(conditions.parse(expr)[0])
    return names <= set(facts)


def run(s: Session, tenant_id: str, policy, definition_: dict, sample: Sample, steps: list[str], at: datetime | None = None) -> dict:
    """Open a sample case on the draft, take the given steps (each the id of the stage to move to), and report what happened."""
    at = at or now()
    point = s.begin_nested()
    try:
        with trying(definition_), system(s):
            return _run(s, tenant_id, policy, definition_, sample, steps[:MAX_STEPS], at)
    finally:
        point.rollback()
        s.expire_all()


def _run(s: Session, tenant_id: str, policy, ct: dict, sample: Sample, steps: list[str], at: datetime) -> dict:
    pid = new_id("trial")[:40]
    s.add(Party(id=pid, name=sample.name, occupation="sample", pep=sample.pep, risk_rating=sample.risk_rating))
    s.add(Account(id=f"A-{pid}"[:40], party_id=pid))
    txn = Transaction(id=f"t-{pid}"[:40], account_id=f"A-{pid}"[:40], party_id=pid, ts=at - timedelta(days=2), amount=sample.amount,
                      direction="out", channel="wire")
    s.add(txn)
    s.flush()
    case = create_case(s, ct, pid, sample.priority, created=at, actor="trial")
    s.add(Alert(id=new_id("alr"), dedupe_key=f"trial:{case.id}", scenario=(ct.get("scenarios") or ["manual"])[0], scenario_version="trial",
                party_id=pid, score=0.7, explanation="Sample alert for a trial run", txn_ids=[txn.id], case_id=case.id, created=at))
    s.flush()
    taken, stopped = [], None
    clock = at
    for to in steps:
        clock += timedelta(hours=1)
        result = _take(s, case, policy, to, clock)
        taken.append(result)
        if not result["ok"]:
            stopped = result
            break
    people = {p.id: p.name for p in policy.people.values()}
    timeline = [_event(e, people) for e in s.scalars(select(CaseEvent).where(CaseEvent.case_id == case.id).order_by(CaseEvent.ts, CaseEvent.id))]
    defn = definition(case)
    options = []
    if case.closed is None and stopped is None:
        for t in [x for x in defn["transitions"] if x["from"] == case.state]:
            holds = conditions.evaluate(t.get("when"), _facts(s, case))
            if not holds:
                continue
            who = [p for p in policy.people.values() if p.holds(t["roles"]) and not p.read_only]
            options.append({"to": t["to"], "to_label": defn["states"][t["to"]]["label"], "label": t["label"],
                            "who": [p.name for p in who], "four_eyes": bool(t.get("four_eyes")), "requires": t.get("requires", []),
                            "when": conditions.describe(t.get("when"))})
    st = defn["states"][case.state]
    return {"ok": stopped is None, "case": {"id": case.id, "stage": case.state, "stage_label": st["label"], "queue": case.queue,
                                             "queue_label": policy.queues.get(case.queue or "", ""), "closed": case.closed is not None,
                                             "disposition": case.disposition, "priority": case.priority},
            "steps": taken, "stopped": stopped, "options": options, "timeline": timeline,
            "facts": _facts(s, case)}


def _facts(s: Session, case) -> dict:
    from ..cases.service import facts

    return facts(s, case)


def _take(s: Session, case, policy, to: str, ts: datetime) -> dict:
    defn = definition(case)
    candidates = [t for t in defn["transitions"] if t["from"] == case.state and t["to"] == to]
    if not candidates:
        return {"ok": False, "to": to, "why": f"There is no step from {defn['states'][case.state]['label']} to that stage."}
    f = _facts(s, case)
    t = next((x for x in candidates if conditions.evaluate(x.get("when"), f)), None)
    if t is None:
        return {"ok": False, "to": to, "label": candidates[0]["label"],
                "why": f"'{candidates[0]['label']}' does not apply to this case: {conditions.describe(candidates[0].get('when'))}"}
    req = t.get("requires", [])
    done: list[str] = []
    workers = [p for p in policy.people.values() if not p.read_only and p.can("work")]
    narrator = None
    if "narrative" in req and not (case.narrative.strip() and case.drafted_by):
        narrator = next((p for p in workers if not (t.get("four_eyes") and _only_holders(policy, t, p))), workers[0] if workers else None)
        if narrator is not None:
            save_narrative(s, case, "Trial narrative: who, what, when, where and why.", narrator, ts=ts)
            done.append(f"{narrator.name} wrote the narrative")
    actor = _actor(policy, case, t, exclude={narrator.id} if narrator and t.get("four_eyes") else set())
    if actor is None:
        holders = [p.name for p in policy.people.values() if p.holds(t["roles"]) and not p.read_only]
        why = (f"Nobody at this client holds {', '.join(r.replace('_', ' ') for r in t['roles'])}." if not holders else
               f"Four-eyes needs someone other than the last person to act, and only {', '.join(holders)} can take this step.")
        return {"ok": False, "to": to, "label": t["label"], "why": why}
    if "tasks" in req:
        complete_stage_tasks(s, case, actor, ts=ts)
        done.append("ticked the stage's tasks")
    if "open_request" in req and not _open(s, case):
        s.add(InfoRequest(id=new_id("rfi"), case_id=case.id, recipient="customer", question="Trial question", raised_by=actor.id, created=ts,
                          due=ts + timedelta(days=5)))
        s.flush()
        done.append("raised an information request")
    if "requests_answered" in req:
        for r in _open(s, case):
            r.status, r.response, r.answered, r.answered_by = "answered", "Trial answer", ts, actor.id
        s.flush()
        done.append("recorded the answers to open requests")
    reason = ""
    if "reason" in req:
        offered = reasons_for(s, defn["states"][to].get("disposition"))
        if offered:
            reason = offered[0]["code"]
            done.append(f"chose the reason '{offered[0]['label']}'")
    note = "Trial note" if "note" in req else ""
    if note:
        done.append("wrote a note")
    try:
        transition(s, case, to, actor, note, ts=ts, reason=reason)
    except TransitionError as e:
        return {"ok": False, "to": to, "label": t["label"], "by": actor.name, "why": str(e), "prepared": done}
    return {"ok": True, "to": to, "label": t["label"], "by": actor.name, "prepared": done, "stage": defn["states"][to]["label"]}


def _only_holders(policy, t: dict, p: Persona) -> bool:
    return [x.id for x in policy.people.values() if x.holds(t["roles"]) and not x.read_only] == [p.id]


def _actor(policy, case, t: dict, exclude: set[str]) -> Persona | None:
    """Who takes a step: someone holding the role, preferably in the stage's queue, never the last person to act when four-eyes applies."""
    last = (case.data or {}).get("last_actor")
    out = []
    for p in policy.people.values():
        if p.read_only or not p.holds(t["roles"]) or p.id in exclude:
            continue
        if t.get("four_eyes") and p.id in (last, case.drafted_by):
            continue
        out.append(p)
    out.sort(key=lambda p: (case.queue not in p.queues, p.id))
    return out[0] if out else None


def _open(s: Session, case) -> list[InfoRequest]:
    return list(s.scalars(select(InfoRequest).where(InfoRequest.case_id == case.id, InfoRequest.status == "open")))


WORDS = {"case.created": "Case opened", "case.state_changed": "Moved", "task.completed": "Task done", "narrative.saved": "Narrative saved",
         "request.raised": "Information request raised", "workflow.action": "Automatic action", "workflow.notified": "Notified",
         "workflow.event": "Integration event", "case.handed_off": "Handed to the queue"}


def _event(e: CaseEvent, people: dict) -> dict:
    p = e.payload or {}
    text = WORDS.get(e.kind, e.kind)
    if e.kind == "case.state_changed":
        text = f"{p.get('label')}: {p.get('from')} to {p.get('to')}"
    elif e.kind == "workflow.action":
        extra = {k: v for k, v in p.items() if k not in ("stage", "do", "label")}
        text = f"{p.get('label')}" + (f" ({', '.join(f'{k}: {v}' for k, v in extra.items() if v not in (None, '', []))})" if extra else "")
    elif e.kind == "workflow.notified":
        text = f"Notified {p.get('role', '').replace('_', ' ')}: {', '.join(people.get(x, x) for x in p.get('people', []))}"
    elif e.kind == "task.completed":
        text = f"Task done: {p.get('label') or p.get('task')}"
    return {"ts": e.ts.isoformat(), "actor": people.get(e.actor, e.actor), "actor_kind": e.actor_kind, "kind": e.kind, "text": text}
