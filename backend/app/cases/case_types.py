"""Case types are configuration: versioned YAML definitions.

Files in `case_types/` are the shipped baseline. Versions published from the workflow editor are stored
in the database and layered on top. A case stays on the version it was opened under.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from ..config import settings
from ..domain.personas import PERMISSIONS, QUEUES
from . import conditions

REQUIREMENTS = {
    "note": "A rationale note",
    "narrative": "A saved narrative",
    "tasks": "All required tasks in the current stage done",
    "open_request": "An open information request",
    "requests_answered": "No information request still open",
}
STATE_KINDS = ("open", "wait", "closed")


class CaseTypeError(ValueError):
    pass


def validate(ct, origin: str = "definition") -> dict:
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
        if st.get("queue") and st["queue"] not in QUEUES:
            fail(f"state '{sid}' uses unknown queue '{st['queue']}'")
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
        for role in t["roles"]:
            if role not in PERMISSIONS:
                fail(f"{name} uses unknown role '{role}'")
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
def _baseline(directory: str | None = None) -> dict[str, dict]:
    d = Path(directory) if directory else settings.case_types_dir
    return {ct["case_type"]: ct for ct in (parse(p.read_text(), p.name) for p in sorted(d.glob("*.yaml")))}


# Versions published at run time: (case_type, version) -> definition, in publish order.
_published: dict[tuple[str, str], dict] = {}


def register_published(ct: dict) -> None:
    _published[(ct["case_type"], ct["version"])] = ct


def reset_published() -> None:
    _published.clear()


def load_case_types() -> dict[str, dict]:
    """The active definition of every case type: the latest published version, else the baseline file."""
    out = dict(_baseline())
    for (name, _), ct in _published.items():
        out[name] = ct
    return out


def get_definition(case_type: str, version: str | None = None) -> dict:
    """The definition a case runs on: its own version where still known, else the active one."""
    if version and (case_type, version) in _published:
        return _published[(case_type, version)]
    base = _baseline().get(case_type)
    if base is not None and (version is None or base["version"] == version) and not (version is None and _has_published(case_type)):
        return base
    return load_case_types()[case_type]


def _has_published(case_type: str) -> bool:
    return any(name == case_type for name, _ in _published)


def versions(case_type: str) -> list[dict]:
    out = []
    base = _baseline().get(case_type)
    if base:
        out.append({"version": base["version"], "origin": "baseline file"})
    out += [{"version": v, "origin": "published"} for (name, v) in _published if name == case_type]
    active = load_case_types()[case_type]["version"]
    return [o | {"active": o["version"] == active} for o in out]


def case_type_for_scenario(scenario: str) -> dict:
    types = load_case_types()
    for ct in types.values():
        if scenario in ct.get("scenarios", []):
            return ct
    return types["tm_alert_investigation"]


def version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in str(version).split("."))
