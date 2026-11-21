"""The agent roster, loaded from configuration. Code asks the catalog; it never hard-codes what an agent may do."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

LEVELS = ("L0", "L1", "L2", "L3")
LEVEL_LABELS = {"L0": "Off", "L1": "Assist", "L2": "Prepare", "L3": "Act within policy"}
LEVEL_HELP = {"L0": "The agent does not run for this case type.",
              "L1": "Runs only when a person asks for it.",
              "L2": "Prepares evidence and a recommendation on every item. A person disposes every item.",
              "L3": "May close defined low-risk alerts on its own, inside the client's policy, with sampled human QA."}
KINDS = ("case_step", "alert", "on_request", "batch")
# Never tools, at any level, for any agent. Listed so the runtime can refuse them by name and say why.
HUMAN_ONLY = {"file_report": "Filing a regulatory report", "close_case": "Closing a case", "approve_step": "Approving a workflow step",
              "exit_customer": "Exiting a customer", "change_case_state": "Moving a case to another stage"}
ACTS = {"close_alert": "Close a low-risk alert without opening a case", "discount_hit": "Discount a weak screening hit without opening a case"}
PATH = Path(__file__).resolve().parents[2] / "agents" / "catalog.yaml"


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class AgentSpec:
    id: str
    title: str
    version: str
    kind: str
    goal: str
    tools: tuple[str, ...]
    max_level: str
    evaluation: str
    acts: str | None = None
    untrusted_input: bool = False

    def allows(self, level: str) -> bool:
        return LEVELS.index(level) <= LEVELS.index(self.max_level)


def parse_catalog(raw: dict, origin: str = "agent catalog") -> dict[str, AgentSpec]:
    def fail(msg: str):
        raise CatalogError(f"{origin}: {msg}")

    if not isinstance(raw, dict) or not isinstance(raw.get("agents"), dict) or not raw["agents"]:
        fail("needs an 'agents' mapping")
    out: dict[str, AgentSpec] = {}
    for aid, a in raw["agents"].items():
        for key in ("title", "version", "kind", "goal", "tools", "max_level", "evaluation"):
            if not a.get(key):
                fail(f"agent '{aid}' is missing '{key}'")
        if a["kind"] not in KINDS:
            fail(f"agent '{aid}' has unknown kind '{a['kind']}'")
        if a["max_level"] not in LEVELS[1:]:
            fail(f"agent '{aid}' has max_level '{a['max_level']}'; use L1, L2 or L3")
        banned = [t for t in list(a["tools"]) + [a.get("acts") or ""] if t in HUMAN_ONLY]
        if banned:
            fail(f"agent '{aid}' is given '{banned[0]}', which only a person may do")
        acts = a.get("acts")
        if acts and acts not in ACTS:
            fail(f"agent '{aid}' has unknown action '{acts}'")
        if (a["max_level"] == "L3") != bool(acts):
            fail(f"agent '{aid}': L3 needs exactly one declared action, and only L3 agents declare one")
        out[aid] = AgentSpec(aid, a["title"], str(a["version"]), a["kind"], a["goal"], tuple(a["tools"]), a["max_level"], a["evaluation"],
                             acts, bool(a.get("untrusted_input", False)))
    return out


@lru_cache
def catalog() -> dict[str, AgentSpec]:
    return parse_catalog(yaml.safe_load(PATH.read_text()), PATH.name)
