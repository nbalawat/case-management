"""Which case events tell whom what. Pure functions: an event in, the people to tell and the words out.

One rule per event kind. A rule never tells people about their own actions. Adding a notification is adding a rule:
nothing that writes events needs to know notifications exist.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Note:
    people: tuple[str, ...]
    title: str
    body: str = ""


@dataclass(frozen=True)
class Context:
    """What a rule may know beyond the event: the case's owner now, and who holds a role at the client."""
    owner: str | None
    holders: Callable[[str], list[str]]


Rule = Callable[[str, dict, Context], Note | None]
RULES: dict[str, Rule] = {}


def rule(kind: str):
    def wrap(fn: Rule) -> Rule:
        RULES[kind] = fn
        return fn
    return wrap


@rule("case.assigned")
def _assigned(case_id: str, p: dict, ctx: Context) -> Note | None:
    if p.get("how") == "claimed" or not p.get("owner"):
        return None
    return Note((p["owner"],), f"Case {case_id} was assigned to you", p.get("reason") or "")


@rule("workflow.notified")
def _workflow(case_id: str, p: dict, ctx: Context) -> Note | None:
    return Note(tuple(p.get("people") or ()), p.get("message") or f"Case {case_id} needs attention", f"Sent to every {str(p.get('role', '')).replace('_', ' ')}")


@rule("sla.stage_breached")
def _breached(case_id: str, p: dict, ctx: Context) -> Note | None:
    people = set(ctx.holders(p.get("notify") or "team_lead")) | ({ctx.owner} if ctx.owner else set())
    raised = f" Priority raised to {p['priority_to']}." if p.get("priority_to") != p.get("priority_from") else ""
    return Note(tuple(sorted(people)), f"Case {case_id} is past its {str(p.get('stage', '')).replace('_', ' ')} deadline", raised.strip())


@rule("request.answered")
def _answered(case_id: str, p: dict, ctx: Context) -> Note | None:
    return Note((ctx.owner,), f"The {p.get('recipient', 'information')} request on case {case_id} was answered") if ctx.owner else None


@rule("case.merged_into")
def _merged(case_id: str, p: dict, ctx: Context) -> Note | None:
    owner = (p.get("before") or {}).get("owner") or ctx.owner  # the merge has already taken the case off them
    return Note((owner,), f"Your case {case_id} was merged into {p.get('into')}", p.get("note") or "") if owner else None


@rule("case.split_from")
def _split(case_id: str, p: dict, ctx: Context) -> Note | None:
    return Note((ctx.owner,), f"Case {case_id} was split off from {p.get('from')} and is yours", p.get("note") or "") if ctx.owner else None


@rule("case.unmerged")
def _unmerged(case_id: str, p: dict, ctx: Context) -> Note | None:
    return Note((ctx.owner,), f"Case {case_id} is open again: the merge into {p.get('from')} was undone", p.get("note") or "") if ctx.owner else None


def notes_for(kind: str, case_id: str, payload: dict, actor: str, ctx: Context) -> Note | None:
    fn = RULES.get(kind)
    note = fn(case_id, payload or {}, ctx) if fn else None
    if note is None:
        return None
    people = tuple(p for p in dict.fromkeys(note.people) if p and p != actor)
    return Note(people, note.title, note.body) if people else None
