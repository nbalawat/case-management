"""The same step on many cases at once. Each case goes through exactly the checks it would alone, and reports its own outcome."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.models import new_id
from ..domain.personas import Persona, policy_of
from ..visibility import find_case
from . import assignments
from .service import TransitionError, allowed_transitions, complete_stage_tasks, record, transition

ACTIONS = ("claim", "reassign", "transition")
LIMIT = 200


def run(s: Session, case_ids: list[str], action: str, persona: Persona, *, owner: str = "", to: str = "", note: str = "", reason: str = "",
        tick_tasks: bool = False) -> dict:
    """Apply one action to each case. A case that refuses is left exactly as it was, and says why."""
    if action not in ACTIONS:
        raise TransitionError(f"Unknown bulk action '{action}'")
    ids = list(dict.fromkeys(case_ids))
    if not ids or len(ids) > LIMIT:
        raise TransitionError(f"Choose between 1 and {LIMIT} cases")
    batch, results = new_id("bulk"), []
    for cid in ids:
        case = find_case(s, cid)
        if case is None:
            results.append({"id": cid, "ok": False, "message": "Not found"})
            continue
        point = s.begin_nested()
        try:
            if action == "claim":
                assignments.claim(s, case, persona)
            elif action == "reassign":
                assignments.reassign(s, case, owner, persona, note)
            else:
                if tick_tasks:
                    complete_stage_tasks(s, case, persona)
                transition(s, case, to, persona, note, reason=reason)
            record(s, case.id, persona.id, "human", "bulk.applied", {"batch": batch, "action": action, "of": len(ids)})
            s.flush()
            point.commit()
            results.append({"id": cid, "ok": True, "message": ""})
        except TransitionError as e:
            point.rollback()
            s.expire(case)
            results.append({"id": cid, "ok": False, "message": str(e)})
    return {"batch": batch, "action": action, "done": sum(r["ok"] for r in results), "refused": sum(not r["ok"] for r in results), "results": results}


def options(s: Session, case_ids: list[str], persona: Persona) -> dict:
    """What can be done to all of these cases together: the steps and the owners they have in common."""
    cases = [c for c in (find_case(s, cid) for cid in list(dict.fromkeys(case_ids))[:LIMIT]) if c is not None and c.closed is None]
    steps: dict[str, dict] | None = None
    owners: set[str] | None = None
    for c in cases:
        mine = {t["to"]: t for t in allowed_transitions(s, c, persona)}
        if steps is None:
            steps = {to: {"to": to, "label": t["label"], "needs_note": t["needs_note"], "reasons": t["reasons"], "needs_reason": bool(t["reasons"])} for to, t in mine.items()}
        else:
            for to in list(steps):
                if to not in mine or mine[to]["label"] != steps[to]["label"]:
                    del steps[to]
                    continue
                offered = {r["code"] for r in mine[to]["reasons"]}
                steps[to]["needs_note"] = steps[to]["needs_note"] or mine[to]["needs_note"]
                steps[to]["reasons"] = [r for r in steps[to]["reasons"] if r["code"] in offered]
        members = {p.id for p in policy_of(c.tenant_id).queue_members(c.queue)}
        owners = members if owners is None else owners & members
    people = policy_of(cases[0].tenant_id).people if cases else {}
    return {"cases": len(cases), "can_claim": persona.can("work") and not persona.read_only, "can_reassign": persona.can("reassign") and not persona.read_only,
            "transitions": [] if persona.read_only else list((steps or {}).values()),
            "owners": [{"id": o, "name": people[o].name} for o in sorted(owners or ())]}
