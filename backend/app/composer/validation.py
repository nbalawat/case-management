"""Every problem with a design, in plain words, pinned to the block or step it is about.

The designer calls this on every change, so it never raises: it collects. Errors stop publishing; warnings are worth a
look but do not. As a last line of defence the compiled definition is put through exactly the checks a hand-written
one gets, so the designer can never publish something the platform would refuse.
"""
from __future__ import annotations

from ..cases import case_types as registry
from ..cases.case_types import CaseTypeError
from ..cases.effects import REGISTRY as EFFECTS, EffectError
from ..cases.effects import validate as validate_effects
from . import blocks
from . import conditions as cond
from .blocks import issue
from .compiler import CompileError, Context, compile_design, kind_of, state_ids
from .model import Design

REQ_HINTS = {"tasks": "the stage's required tasks are ticked", "note": "a rationale note is written", "reason": "a reason code is chosen",
             "narrative": "a narrative is saved", "open_request": "an information request is open", "requests_answered": "every request is answered"}


def validate(design: Design, ctx: Context) -> tuple[list[dict], dict | None]:
    """(issues, compiled definition or None)."""
    ctx.design = design
    out: list[dict] = []
    ids = {n.id for n in design.nodes}
    if len(ids) != len(design.nodes):
        out.append(issue("error", "workflow", None, "Two blocks share an id. Re-import the file or delete the duplicate."))
    starts = [n for n in design.nodes if n.type == "start"]
    if not starts:
        out.append(issue("error", "workflow", None, "There is no start, so no case can enter this workflow.", "Drag 'Start' from the palette."))
    elif len(starts) > 1:
        for n in starts[1:]:
            out.append(issue("error", "node", n.id, "A workflow has one start.", "Delete this one."))
    for n in design.nodes:
        b = blocks.get(n.type)
        if b is None:
            out.append(issue("error", "node", n.id, f"'{n.type}' is not a block this platform offers.", "Delete it, or replace it with a palette block."))
            continue
        out += b.check(n, design, ctx)
        for i, a in enumerate(n.config.get("actions") or []):
            if not b.accepts_actions:
                out.append(issue("error", "node", n.id, f"A {b.label.lower()} cannot take automatic actions."))
                break
            try:
                validate_effects([a], "action")
            except EffectError as e:
                out.append(issue("error", "node", n.id, f"Automatic action {i + 1}: {str(e).removeprefix('action: ')}."))
            if a.get("do") == "notify" and a.get("role") and a["role"] not in ctx.policy.roles:
                out.append(issue("error", "node", n.id, f"'Notify a role' names '{a['role']}', which is not a role at this client."))
    out += _edges(design, ctx)
    out += _graph(design)
    out += _people(design, ctx)
    out += _settings(design, ctx)
    out += _facts(design)
    definition = None
    if not any(i["level"] == "error" for i in out):
        try:
            definition = compile_design(design, ctx)
            registry.validate(definition, "workflow")
            registry.check_policy(definition, ctx.policy, "workflow")
        except (CompileError, CaseTypeError) as e:
            out.append(issue("error", "workflow", None, str(e).removeprefix("workflow: ")))
            definition = None
    return _dedupe(out), definition


def _edges(design: Design, ctx: Context) -> list[dict]:
    out = []
    seen = set()
    for e in design.edges:
        src, dst = design.node(e.source), design.node(e.target)
        if src is None or dst is None:
            out.append(issue("error", "edge", e.id, "This step is connected to a block that no longer exists.", "Delete the step."))
            continue
        key = (e.source, e.handle, e.target)
        if key in seen:
            out.append(issue("warning", "edge", e.id, "Two steps join the same blocks the same way.", "Delete one of them."))
        seen.add(key)
        if e.source == e.target:
            out.append(issue("error", "edge", e.id, "A step cannot lead back into the same block.", "To repeat a stage, return to it from a later one."))
        if kind_of(src) != "stage":
            continue  # steps out of the start and out of decisions are routes, not something a person does
        step = ctx.step(e)
        if not str(step.get("label", "")).strip():
            out.append(issue("error", "edge", e.id, f"The step from '{src.config.get('label', '')}' has no name.",
                             "Name it for what the person does, such as 'Escalate' or 'Approve'."))
        roles = step.get("roles") or []
        if not roles:
            out.append(issue("error", "edge", e.id, f"Nobody can take the step '{step.get('label') or 'unnamed'}'.", "Choose who can take it."))
        for r in roles:
            if r not in ctx.policy.roles:
                out.append(issue("error", "edge", e.id, f"'{r}' is not a role at this client.", "Pick one of the client's roles."))
        for req in step.get("requires") or []:
            if req not in registry.REQUIREMENTS:
                out.append(issue("error", "edge", e.id, f"'{req}' is not something a step can require."))
        if "tasks" in (step.get("requires") or []) and not src.config.get("tasks"):
            out.append(issue("warning", "edge", e.id, f"'{step.get('label')}' waits for the stage's tasks, but '{src.config.get('label')}' has none.",
                             "Add tasks to the stage or drop the requirement."))
        if "reason" in (step.get("requires") or []) and kind_of(dst) == "outcome":
            disp = dst.config.get("disposition")
            if ctx.reasons and not ctx.reasons.get(disp):
                out.append(issue("warning", "edge", e.id, f"No reason codes are set up for the outcome '{disp}', so none can be chosen.",
                                 "Add them under Code lists, or drop the requirement."))
        for p in cond.group_problems(step.get("when")):
            out.append(issue("error", "edge", e.id, p))
    return out


def _graph(design: Design) -> list[dict]:
    out = []
    starts = [n for n in design.nodes if n.type == "start"]
    if not starts:
        return out
    reach, todo = {starts[0].id}, [starts[0].id]
    while todo:
        cur = todo.pop()
        for e in design.out_of(cur):
            if e.target not in reach and design.node(e.target) is not None:
                reach.add(e.target)
                todo.append(e.target)
    outcomes = [n for n in design.nodes if kind_of(n) == "outcome"]
    if not outcomes:
        out.append(issue("error", "workflow", None, "There is no outcome, so a case could never close.", "Drag an outcome such as 'Close, no action'."))
    for n in design.nodes:
        if n.id not in reach and n.type != "start":
            out.append(issue("error", "node", n.id, f"No path from the start reaches '{n.config.get('label') or n.type}'.",
                             "Connect a step into it, or delete it."))
        if kind_of(n) == "stage" and not design.out_of(n.id):
            out.append(issue("error", "node", n.id, f"'{n.config.get('label')}' is a dead end: cases could get stuck there.",
                             "Draw a step from it to the next stage or an outcome."))
    # every stage must be able to finish: some path from it reaches an outcome
    finishing = {n.id for n in outcomes}
    changed = True
    while changed:
        changed = False
        for e in design.edges:
            if e.target in finishing and e.source not in finishing:
                finishing.add(e.source)
                changed = True
    for n in design.nodes:
        if n.id in reach and kind_of(n) in ("stage", "decision") and n.id not in finishing and design.out_of(n.id):
            out.append(issue("error", "node", n.id, f"Cases in '{n.config.get('label')}' can go round in circles but never reach an outcome.",
                             "Add a step towards an outcome."))
    # decisions chained into a loop
    for n in design.nodes:
        if kind_of(n) != "decision":
            continue
        seen, todo = set(), [e.target for e in design.out_of(n.id)]
        while todo:
            cur = todo.pop()
            if cur == n.id:
                out.append(issue("error", "node", n.id, "This decision leads back into itself without passing through a stage.",
                                 "Route one branch through a stage first."))
                break
            node = design.node(cur)
            if cur in seen or kind_of(node) != "decision":
                continue
            seen.add(cur)
            todo += [e.target for e in design.out_of(cur)]
    return out


def _people(design: Design, ctx: Context) -> list[dict]:
    """Checks against the client's people: someone in each stage's queue can move it on, and four-eyes has two people."""
    out = []
    pol = ctx.policy
    for n in design.nodes:
        if kind_of(n) != "stage" or not n.config.get("queue") or n.config["queue"] not in pol.queues:
            continue
        roles = {r for e in design.out_of(n.id) for r in ctx.step(e).get("roles") or []}
        if roles and not any(m.holds(roles) for m in pol.queue_members(n.config["queue"])):
            out.append(issue("error", "node", n.id, f"Nobody in the {pol.queues[n.config['queue']]} queue can take any step out of "
                             f"'{n.config.get('label')}'.", "Let one of the queue's roles take a step, or choose another queue."))
        for e in design.out_of(n.id):
            step = ctx.step(e)
            if step.get("four_eyes") and step.get("roles"):
                holders = {p.id for r in step["roles"] for p in pol.holders(r) if not p.read_only}
                if len(holders) < 2:
                    out.append(issue("warning", "edge", e.id, f"Only {len(holders)} person can take '{step.get('label')}', so four-eyes may "
                                     "block it when they also did the work before.", "Add a second role, or give the role to another person."))
    return out


def _settings(design: Design, ctx: Context) -> list[dict]:
    out = []
    st = design.settings
    if st.escalation_notify and st.escalation_notify not in ctx.policy.roles:
        out.append(issue("warning", "workflow", None, f"Late cases notify '{st.escalation_notify}', which is not a role at this client.",
                         "Choose who hears about late cases in the workflow settings."))
    if st.qa_reopen_to and design.node(st.qa_reopen_to) is None:
        out.append(issue("error", "workflow", None, "QA reopens failed cases at a stage that no longer exists.", "Choose another stage."))
    from ..agents.catalog import catalog, LEVELS

    known = catalog()
    for a in st.agents:
        if a not in known:
            out.append(issue("error", "workflow", None, f"'{a}' is not an agent in the catalog."))
    if st.autonomy not in LEVELS:
        out.append(issue("error", "workflow", None, f"Agent autonomy must be one of {', '.join(LEVELS)}."))
    active = ctx.active.get(design.key)
    if active is not None and registry.version_key(design.version) <= registry.version_key(active["version"]):
        nxt = next_version(active["version"])
        out.append(issue("error", "workflow", None, f"Version {design.version} is not newer than the live {active['version']}.",
                         f"Use {nxt}. Open cases keep the version they started on."))
    start = next((n for n in design.nodes if n.type == "start"), None)
    for sc in (start.config.get("scenarios") or []) if start else []:
        owner = next((ct for k, ct in ctx.active.items() if k != design.key and sc in ct.get("scenarios", [])), None)
        if owner:
            out.append(issue("warning", "node", start.id, f"Alerts of '{sc}' already open '{owner['title']}' cases and will keep going there.",
                             f"Remove '{sc}' here, or remove it from {owner['title']} first."))
    return out


def _facts(design: Design) -> list[dict]:
    """A decision on the screening result only makes sense if the customer was screened on the way there."""
    out = []
    screens = {n.id for n in design.nodes if any(a.get("do") == "screen_party" for a in n.config.get("actions") or [])}
    for n in design.nodes:
        used = set()
        if kind_of(n) == "decision":
            for b in n.config.get("branches") or []:
                used |= cond.facts_used(b)
        if "screening_hit" in used and not _upstream(design, n.id) & screens:
            out.append(issue("warning", "node", n.id, "This decision uses the screening result, but no stage before it screens the customer.",
                             "Add 'Screen against watchlists' to an earlier stage."))
    return out


def _upstream(design: Design, node_id: str) -> set[str]:
    seen, todo = set(), [node_id]
    while todo:
        cur = todo.pop()
        for e in design.into(cur):
            if e.source not in seen:
                seen.add(e.source)
                todo.append(e.source)
    return seen


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for i in items:
        k = (i["level"], i["target"], i["id"], i["message"])
        if k not in seen:
            seen.add(k)
            out.append(i)
    return sorted(out, key=lambda i: i["level"] != "error")


def next_version(version: str) -> str:
    """The next minor version: 4.0.0 becomes 4.1.0."""
    parts = ([int(p) if p.isdigit() else 0 for p in str(version).split(".")] + [0, 0, 0])[:3]
    return f"{parts[0]}.{parts[1] + 1}.0"


__all__ = ["validate", "next_version", "state_ids", "EFFECTS"]
