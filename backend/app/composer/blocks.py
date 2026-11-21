"""The building blocks a workflow is composed from.

Every block on the designer's palette is a `BlockType`. A block says what it is for, which settings it has (described
as fields, so the designer draws its form without knowing the block), what it starts as, and which plain-word problems
it can have. There are four kinds, and the compiler only needs to know the kind:

    start     where cases come in; exactly one per workflow
    stage     somewhere a case waits for people: a work stage, a waiting stage or an approval
    decision  a fork that routes the case by its facts; compiles into conditions on the steps around it
    outcome   a closed state with a disposition

Automatic actions (screen the customer, notify a role, ask for information...) are not blocks of their own: they are
the registered effects in `app.cases.effects`, attached to a stage. Other modules extend the palette with `register`.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..cases.effects import Field
from .model import Design, Node

Issue = dict  # {"level": "error"|"warning", "target": "node"|"edge"|"workflow", "id": str|None, "message": str, "hint": str}


def issue(level: str, target: str, id_: str | None, message: str, hint: str = "") -> Issue:
    return {"level": level, "target": target, "id": id_, "message": message, "hint": hint}


@dataclass(frozen=True)
class Preset:
    """One entry on the palette: a block with starting settings, such as 'File a SAR' for an outcome."""

    id: str
    label: str
    description: str
    config: dict = field(default_factory=dict)
    icon: str | None = None


@dataclass(frozen=True)
class BlockType:
    id: str
    kind: str  # start | stage | decision | outcome
    category: str
    label: str
    description: str
    icon: str
    fields: tuple[Field, ...] = ()
    defaults: dict = field(default_factory=dict)
    presets: tuple[Preset, ...] = ()
    state_kind: str = "open"  # for stages and outcomes: open | wait | closed
    accepts_actions: bool = False
    accepts_tasks: bool = False
    check: Callable[[Node, Design, Any], list[Issue]] = field(default=lambda node, design, ctx: [])

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "category": self.category, "label": self.label, "description": self.description,
                "icon": self.icon, "fields": [f.as_dict() for f in self.fields], "defaults": self.defaults,
                "accepts_actions": self.accepts_actions, "accepts_tasks": self.accepts_tasks,
                "presets": [{"id": p.id, "label": p.label, "description": p.description, "config": {**self.defaults, **p.config},
                             "icon": p.icon or self.icon} for p in (self.presets or (Preset(self.id, self.label, self.description),))]}


REGISTRY: dict[str, BlockType] = {}
CATEGORIES = ["Start and finish", "Stages", "Approvals", "Decisions"]


def register(block: BlockType) -> BlockType:
    """Add a block to every client's palette. A block from another module names its own category."""
    REGISTRY[block.id] = block
    if block.category not in CATEGORIES:
        CATEGORIES.append(block.category)
    return block


def get(block_id: str) -> BlockType | None:
    return REGISTRY.get(block_id)


# --- step (edge) settings ------------------------------------------------------------------------------------------------
STEP_FIELDS = (
    Field("label", "Step name", "text", True, (), "", "What the button says, such as 'Escalate to investigation'"),
    Field("roles", "Who can take it", "multiselect", True, "roles"),
    Field("requires", "Before it can be taken", "multiselect", False, "requirements"),
    Field("four_eyes", "Four-eyes: someone other than the last person to act", "bool", False, (), False),
)
BRANCH_STEP_FIELDS = (Field("label", "Step name", "text", False, (), "", "Leave empty to use the step into the decision with the branch name"),)


# --- shared checks -------------------------------------------------------------------------------------------------------
def _needs_label(node: Node, design: Design, ctx) -> list[Issue]:
    if not str(node.config.get("label", "")).strip():
        return [issue("error", "node", node.id, "This block has no name.", "Give it a short name, such as 'Investigation'.")]
    return []


def _stage_checks(node: Node, design: Design, ctx) -> list[Issue]:
    out = _needs_label(node, design, ctx)
    queue = node.config.get("queue")
    if queue and queue not in ctx.policy.queues:
        out.append(issue("error", "node", node.id, f"The queue '{queue}' does not exist at this client.", "Pick one of the client's queues."))
    if not queue:
        out.append(issue("warning", "node", node.id, "No queue works this stage, so cases here will not appear in anyone's work list.",
                         "Choose the team whose queue should work it."))
    hours = node.config.get("sla_hours")
    if hours not in (None, "") and (not isinstance(hours, (int, float)) or hours <= 0):
        out.append(issue("error", "node", node.id, "The stage clock must be a positive number of hours.", "Use 48 for two days, or leave it empty."))
    seen = set()
    for t in node.config.get("tasks") or []:
        label = str(t.get("label", "")).strip()
        if not label:
            out.append(issue("error", "node", node.id, "A task in this stage has no description.", "Describe it or remove it."))
        elif label.lower() in seen:
            out.append(issue("warning", "node", node.id, f"The task '{label}' is listed twice."))
        seen.add(label.lower())
    return out


def _approval_checks(node: Node, design: Design, ctx) -> list[Issue]:
    out = _stage_checks(node, design, ctx)
    if design.out_of(node.id) and not any(ctx.step(e).get("four_eyes") for e in design.out_of(node.id)):
        out.append(issue("warning", "node", node.id, "No step out of this approval is four-eyes, so the person who prepared the work could approve it.",
                         "Turn on four-eyes for the approving step."))
    return out


def _outcome_checks(node: Node, design: Design, ctx) -> list[Issue]:
    out = _needs_label(node, design, ctx)
    disp = str(node.config.get("disposition", "")).strip()
    if not disp:
        out.append(issue("error", "node", node.id, "This outcome has no disposition, so reports cannot count it.", "Choose one, such as no_sar."))
    elif not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", disp):
        out.append(issue("error", "node", node.id, f"The disposition '{disp}' must be lower case words joined by underscores."))
    if design.out_of(node.id):
        out.append(issue("error", "node", node.id, "An outcome closes the case, so no step can leave it.", "Remove the steps out of it, or make it a stage."))
    return out


def _start_checks(node: Node, design: Design, ctx) -> list[Issue]:
    out = []
    edges = design.out_of(node.id)
    if not edges:
        out.append(issue("error", "node", node.id, "Nothing follows the start, so cases have nowhere to go.", "Connect the start to the first stage."))
    elif len(edges) > 1:
        out.append(issue("error", "node", node.id, "The start connects to more than one block. A case starts in exactly one stage.",
                         "Keep one connection; use a decision after the first stage to route cases."))
    else:
        first = design.node(edges[0].target)
        if first is not None and (b := get(first.type)) is not None and b.kind != "stage":
            out.append(issue("error", "node", node.id, "The start must lead to a stage where people work the case, not straight to a "
                             f"{'decision' if b.kind == 'decision' else 'outcome'}.", "Put a triage stage after the start."))
    if design.into(node.id):
        out.append(issue("error", "node", node.id, "No step can lead back into the start.", "Point the step at a stage instead."))
    return out


def _decision_checks(node: Node, design: Design, ctx) -> list[Issue]:
    from .conditions import branch_issues

    out = []
    branches = node.config.get("branches") or []
    if not str(node.config.get("label", "")).strip():
        out.append(issue("warning", "node", node.id, "This decision has no question.", "Say what it decides, such as 'How much is involved?'"))
    routes = len(branches) + (1 if node.config.get("otherwise", True) else 0)
    if routes < 2:
        out.append(issue("error", "node", node.id, "A decision needs at least two ways out.", "Add a branch, or keep 'Otherwise'."))
    if not design.into(node.id):
        out.append(issue("error", "node", node.id, "Nothing leads into this decision.", "Connect a stage's step to it."))
    handles = {e.handle for e in design.out_of(node.id)}
    for b in branches:
        out += branch_issues(node.id, b, ctx)
        if b.get("id") not in handles:
            out.append(issue("error", "node", node.id, f"The branch '{b.get('label') or 'unnamed'}' goes nowhere.", "Drag from its dot to the next block."))
    if node.config.get("otherwise", True) and "otherwise" not in handles:
        out.append(issue("error", "node", node.id, "'Otherwise' goes nowhere.", "Connect it, or turn 'Otherwise' off."))
    return out


# --- the built-in blocks -------------------------------------------------------------------------------------------------
STAGE_FIELDS = (
    Field("label", "Name", "text", True),
    Field("queue", "Worked by (queue)", "select", False, "queues"),
    Field("sla_hours", "Stage clock (hours)", "number", False, (), None, "When it runs out the case is flagged late and escalated"),
    Field("description", "Notes for the team", "textarea"),
)

register(BlockType("start", "start", "Start and finish", "Start", "Where cases come in: from alerts of chosen scenarios, or opened by hand.",
                   "play", (Field("scenarios", "Alert scenarios that open a case here", "multiselect", False, "scenarios",
                                  help="Leave empty for cases that people open by hand"),),
                   {"label": "Case opened", "scenarios": []}, check=_start_checks))

register(BlockType("stage", "stage", "Stages", "Work stage", "A stage where a team works the case through a checklist of tasks.", "clipboard-list",
                   STAGE_FIELDS, {"label": "New stage", "queue": None, "sla_hours": 48, "tasks": []},
                   (Preset("stage", "Work stage", "A team works the case through a checklist."),
                    Preset("triage", "Triage", "First look: decide whether the case needs a full investigation.",
                           {"label": "Triage", "sla_hours": 48, "tasks": [{"label": "Review the alert and agent briefing", "required": True}]}),
                    Preset("investigation", "Investigation", "Full review of activity, counterparties and the customer.",
                           {"label": "Investigation", "sla_hours": 120,
                            "tasks": [{"label": "Review activity against the customer profile", "required": True},
                                      {"label": "Research counterparties", "required": True}]}),
                    Preset("drafting", "Report drafting", "Write the narrative a regulatory report needs.",
                           {"label": "Report drafting", "sla_hours": 72, "tasks": [{"label": "Narrative covers who, what, when, where and why", "required": True}]},
                           "file-pen")),
                   accepts_actions=True, accepts_tasks=True, check=_stage_checks))

register(BlockType("wait", "stage", "Stages", "Waiting stage", "The case waits on someone outside the team, such as the customer. Its clock "
                   "measures how long they take.", "hourglass", STAGE_FIELDS, {"label": "Awaiting information", "queue": None, "sla_hours": 240},
                   (Preset("wait", "Waiting stage", "Park the case while someone outside the team responds."),
                    Preset("customer_outreach", "Ask the customer", "Send the customer a question on arrival and wait for the answer.",
                           {"label": "Awaiting customer", "sla_hours": 240,
                            "actions": [{"do": "request_information", "recipient": "customer", "question": "Please explain the source of these funds.",
                                         "due_days": 10}]}, "message-square")),
                   state_kind="wait", accepts_actions=True, check=_stage_checks))

register(BlockType("approval", "stage", "Approvals", "Approval", "Someone senior approves or returns the work. Steps out of it default to "
                   "four-eyes, so nobody approves their own work.", "badge-check",
                   STAGE_FIELDS + (Field("approvers", "Approvers (roles)", "multiselect", False, "roles",
                                         help="Pre-filled as 'who can take it' on the steps you draw out of this approval"),),
                   {"label": "Approval", "queue": None, "sla_hours": 48, "approvers": [], "four_eyes": True},
                   (Preset("approval", "Four-eyes approval", "A second person approves or returns the work."),
                    Preset("senior_approval", "Senior sign-off", "A further approval for high-value or sensitive cases.",
                           {"label": "Senior sign-off", "sla_hours": 24})),
                   accepts_actions=True, accepts_tasks=True, check=_approval_checks))

register(BlockType("decision", "decision", "Decisions", "Decision", "Route the case by its facts: amount, risk, a screening result or a decision "
                   "table. People never pick the branch; the facts do.", "git-fork",
                   (Field("label", "Question", "text", False, (), "", "What it decides, such as 'How much is involved?'"),),
                   {"label": "Decision", "branches": [], "otherwise": True},
                   (Preset("decision", "Decision", "Route by any fact of the case."),
                    Preset("by_amount", "Route by amount", "High-value cases take a different path.",
                           {"label": "How much is involved?", "branches": [{"id": "b_high", "label": "$100,000 or more", "match": "all",
                                                                           "conditions": [{"fact": "alerted_amount", "op": ">=", "value": 100000}]}],
                            "otherwise_label": "Under $100,000"}, "banknote"),
                    Preset("by_risk", "Route by customer risk", "High-risk or politically exposed customers take a different path.",
                           {"label": "Is the customer high risk?", "branches": [{"id": "b_risk", "label": "High risk or PEP", "match": "any",
                                                                                "conditions": [{"fact": "risk_rating", "op": "==", "value": "high"},
                                                                                               {"fact": "pep", "op": "==", "value": True}]}],
                            "otherwise_label": "Standard risk"}, "gauge"),
                    Preset("by_screening", "Route by screening result", "Use after a stage that screens the customer.",
                           {"label": "Did screening find a match?", "branches": [{"id": "b_hit", "label": "Possible match", "match": "all",
                                                                                 "conditions": [{"fact": "screening_hit", "op": "==", "value": True}]}],
                            "otherwise_label": "No match"}, "shield")),
                   check=_decision_checks))

register(BlockType("outcome", "outcome", "Start and finish", "Outcome", "Closes the case with a disposition that reports and QA sampling count.",
                   "flag", (Field("label", "Name", "text", True), Field("disposition", "Disposition", "select", True, "dispositions",
                                                                       help="Choose a known one or type your own, such as remediated")),
                   {"label": "Closed", "disposition": "closed"},
                   (Preset("outcome", "Outcome", "Close the case."),
                    Preset("close_no_action", "Close, no action", "Nothing suspicious found.", {"label": "Closed, no action", "disposition": "no_action"}),
                    Preset("file_sar", "File a SAR", "Prepare the suspicious activity report in the client's format, check it and send it on closing.",
                           {"label": "SAR filed", "disposition": "sar_filed"}, "send"),
                    Preset("exit_customer", "Exit the customer", "End the relationship.", {"label": "Customer exited", "disposition": "exit"}, "door-open")),
                   state_kind="closed", check=_outcome_checks))

# What some dispositions do on their own, so the designer can say so.
DISPOSITION_EFFECTS = {"sar_filed": "Prepares and sends a suspicious activity report in the client's regulatory format.",
                       "qa_passed": "Needs a passing QA scorecard.", "qa_failed": "Reopens the reviewed case when the case type says so."}
