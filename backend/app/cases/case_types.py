"""Case types are configuration: versioned YAML definitions.

Files in `case_types/` are the shared library every client starts from. Each client chooses which of them to
use and can publish its own versions, which are stored per client and never seen by another. A case stays on
the version it was opened under.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import settings
from ..domain.access import AccessPolicy
from ..tenants.registry import runtime
from . import conditions, effects

REQUIREMENTS = {
    "note": "A rationale note",
    "reason": "A reason code from the client's list",
    "scorecard": "A completed QA scorecard, at or above the pass mark to pass",
    "narrative": "A saved narrative",
    "tasks": "All required tasks in the current stage done",
    "open_request": "An open information request",
    "requests_answered": "No information request still open",
}
STATE_KINDS = ("open", "wait", "closed")


class CaseTypeError(ValueError):
    pass


def validate(ct, origin: str = "definition") -> dict:
    """Structural rules that hold for any client. Roles and queues are checked against a policy separately."""
    def fail(msg: str):
        raise CaseTypeError(f"{origin}: {msg}")

    if not isinstance(ct, dict):
        fail("must be a YAML mapping")
    for key in ("case_type", "title", "version", "initial_state", "states", "transitions"):
        if key not in ct:
            fail(f"missing '{key}'")
    states = ct["states"]
    if not isinstance(states, dict) or ct["initial_state"] not in states:
        fail("initial_state must be one of the states")
    for sid, st in states.items():
        if not isinstance(st, dict) or "label" not in st:
            fail(f"state '{sid}' needs a label")
        if st.get("kind", "open") not in STATE_KINDS:
            fail(f"state '{sid}' kind must be one of {', '.join(STATE_KINDS)}")
        try:
            effects.validate(st.get("on_enter"), f"state '{sid}'")
        except effects.EffectError as e:
            fail(str(e))
        seen = set()
        for task in st.get("tasks", []):
            if not isinstance(task, dict) or "id" not in task or "label" not in task:
                fail(f"state '{sid}' has a task without id and label")
            if task["id"] in seen:
                fail(f"state '{sid}' repeats task id '{task['id']}'")
            seen.add(task["id"])
    if not isinstance(ct["transitions"], list):
        fail("transitions must be a list")
    for t in ct["transitions"]:
        name = f"transition {t.get('from')} -> {t.get('to')}"
        if t.get("from") not in states or t.get("to") not in states:
            fail(f"{name} references an unknown state")
        if not t.get("label"):
            fail(f"{name} needs a label")
        if not t.get("roles"):
            fail(f"{name} needs roles")
        for req in t.get("requires", []):
            if req not in REQUIREMENTS:
                fail(f"{name} requires unknown '{req}'. Known: {', '.join(REQUIREMENTS)}")
        if states[t["from"]].get("kind", "open") == "closed":
            fail(f"{name} starts from a closed state")
        try:
            conditions.check(t.get("when"))
        except conditions.ConditionError as e:
            fail(f"{name}: {e}")
    reachable, frontier = {ct["initial_state"]}, [ct["initial_state"]]
    while frontier:
        cur = frontier.pop()
        for t in ct["transitions"]:
            if t["from"] == cur and t["to"] not in reachable:
                reachable.add(t["to"])
                frontier.append(t["to"])
    orphans = [sid for sid in states if sid not in reachable]
    if orphans:
        fail(f"unreachable state(s): {', '.join(orphans)}")
    for sid, st in states.items():
        if st.get("kind", "open") != "closed" and not any(t["from"] == sid for t in ct["transitions"]):
            fail(f"state '{sid}' is a dead end: open states need a way out")
    if not any(st.get("kind") == "closed" for st in states.values()):
        fail("at least one closed state is needed")
    qa = ct.get("qa")
    if qa:
        if not 0 <= float(qa.get("sample_rate", 0)) <= 1:
            fail("qa.sample_rate must be between 0 and 1")
        if qa.get("reopen_to") and qa["reopen_to"] not in states:
            fail("qa.reopen_to must be one of the states")
    return ct


def check_policy(ct: dict, policy: AccessPolicy, origin: str = "definition") -> None:
    """Rules that depend on the client: every role and queue a definition names must exist in its access policy,
    and someone in each working queue must be able to move the case on."""
    def fail(msg: str):
        raise CaseTypeError(f"{origin}: {msg}")

    for sid, st in ct["states"].items():
        queue = st.get("queue")
        if queue and queue not in policy.queues:
            fail(f"state '{sid}' uses unknown queue '{queue}'")
    for t in ct["transitions"]:
        for role in t["roles"]:
            if role not in policy.roles:
                fail(f"transition {t['from']} -> {t['to']} uses unknown role '{role}'")
    for sid, st in ct["states"].items():
        if st.get("kind", "open") == "closed" or not st.get("queue"):
            continue
        roles = {r for t in ct["transitions"] if t["from"] == sid for r in t["roles"]}
        if not any(m.holds(roles) for m in policy.queue_members(st["queue"])):
            fail(f"nobody in queue '{st['queue']}' holds a role that can move a case out of '{st['label']}'")


def parse(source: str, origin: str = "definition") -> dict:
    try:
        raw = yaml.safe_load(source)
    except yaml.YAMLError as e:
        raise CaseTypeError(f"{origin}: not valid YAML ({str(e).splitlines()[0]})") from e
    ct = validate(raw, origin)
    ct["version"] = str(ct["version"])
    ct["source"] = source
    return ct


@lru_cache
def library(directory: str | None = None) -> dict[str, dict]:
    """The shared, read-only case types every client can start from."""
    d = Path(directory) if directory else settings.case_types_dir
    return {ct["case_type"]: ct for ct in (parse(p.read_text(), p.name) for p in sorted(d.glob("*.yaml")))}


def register_published(tenant_id: str, ct: dict) -> None:
    runtime(tenant_id).published[(ct["case_type"], ct["version"])] = ct


def load_case_types(tenant_id: str) -> dict[str, dict]:
    """The active definition of every case type this client uses: its latest published version, else the library one."""
    rt = runtime(tenant_id)
    out = {name: library()[name] for name in rt.case_types if name in library()}
    for (name, _), ct in rt.published.items():
        out[name] = ct
    return out


# Definitions visible only to the code running inside `trying(...)`: a draft under trial, never seen by anyone else.
_TRIAL: ContextVar[dict[tuple[str, str], dict] | None] = ContextVar("trial_definitions", default=None)


@contextmanager
def trying(ct: dict) -> Iterator[None]:
    """Make an unpublished definition resolvable for the duration of a trial run in this context only."""
    token = _TRIAL.set({**(_TRIAL.get() or {}), (ct["case_type"], ct["version"]): ct})
    try:
        yield
    finally:
        _TRIAL.reset(token)


def get_definition(tenant_id: str, case_type: str, version: str | None = None) -> dict:
    """The definition a case runs on: its own version where still known, else the client's active one."""
    trial = _TRIAL.get()
    if trial and (case_type, version) in trial:
        return trial[(case_type, version)]
    rt = runtime(tenant_id)
    if version and (case_type, version) in rt.published:
        return rt.published[(case_type, version)]
    base = library().get(case_type)
    if base is not None and version is not None and base["version"] == version:
        return base
    return load_case_types(tenant_id)[case_type]


def versions(tenant_id: str, case_type: str) -> list[dict]:
    rt = runtime(tenant_id)
    out = []
    base = library().get(case_type)
    if base:
        out.append({"version": base["version"], "origin": "platform library"})
    out += [{"version": v, "origin": "published by this client"} for (name, v) in rt.published if name == case_type]
    active = load_case_types(tenant_id)[case_type]["version"]
    return [o | {"active": o["version"] == active} for o in out]


def case_type_for_scenario(tenant_id: str, scenario: str) -> dict:
    """Which of the client's case types handles alerts of this scenario; its default type for anything unrecognised."""
    types = load_case_types(tenant_id)
    for ct in types.values():
        if scenario in ct.get("scenarios", []):
            return ct
    default = runtime(tenant_id).settings.get("default_case_type")
    if default in types:
        return types[default]
    with_scenarios = [ct for ct in types.values() if ct.get("scenarios")]
    if not with_scenarios:
        raise CaseTypeError(f"Client '{tenant_id}' has no case type that accepts alerts")
    return with_scenarios[0]


def version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in str(version).split("."))
