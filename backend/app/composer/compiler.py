"""Compile a design into a case-type definition, the one format both workflow engines run.

Stages and outcomes become states, steps become transitions, and decisions disappear: a step into a decision becomes
one transition per branch, guarded by that branch's conditions (and by the negation of all the others for 'Otherwise').
The result is checked by the same rules as a hand-written definition before anything is published.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from ..cases.case_types import load_case_types
from ..domain.access import AccessPolicy
from . import blocks
from . import conditions as cond
from .model import Design, Edge, Node


class CompileError(ValueError):
    pass


@dataclass
class Context:
    """What the compiler and the checks need to know about the client a design is for."""

    tenant_id: str
    policy: AccessPolicy
    active: dict = field(default_factory=dict)  # the client's active case types by key
    reasons: dict[str, int] = field(default_factory=dict)  # disposition -> number of reason codes offered for it
    design: Design | None = None
    forms: dict[str, str] | None = None  # form id -> title, when the form builder is known; None skips the form checks

    @classmethod
    def for_tenant(cls, tenant_id: str, policy: AccessPolicy, reasons: dict[str, int] | None = None,
                   forms: dict[str, str] | None = None) -> Context:
        return cls(tenant_id, policy, load_case_types(tenant_id), reasons or {}, forms=forms)

    def step(self, edge: Edge) -> dict:
        """A step's settings with the defaults its source block gives it: an approval's approvers and four-eyes."""
        cfg = dict(edge.config)
        src = self.design.node(edge.source) if self.design else None
        if src is not None and src.type == "approval":
            if not cfg.get("roles") and src.config.get("approvers"):
                cfg["roles"] = list(src.config["approvers"])
            if "four_eyes" not in cfg:
                cfg["four_eyes"] = bool(src.config.get("four_eyes", True))
        return cfg


def slug(text: str, fallback: str = "stage") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    s = s[:40] or fallback
    return s if s[0].isalpha() else f"s_{s}"


def kind_of(node: Node | None) -> str | None:
    b = blocks.get(node.type) if node is not None else None
    return b.kind if b else None


def state_ids(design: Design) -> dict[str, str]:
    """A stable, readable state id for every stage and outcome: the one it was imported with, else one from its name."""
    out, used = {}, set()
    for n in design.nodes:
        if kind_of(n) not in ("stage", "outcome"):
            continue
        base = n.config.get("state_id") or slug(n.config.get("label", ""), "stage")
        sid, i = base, 2
        while sid in used:
            sid, i = f"{base}_{i}", i + 1
        used.add(sid)
        out[n.id] = sid
    return out


def _tasks(node: Node) -> list[dict]:
    out, used = [], set()
    for t in node.config.get("tasks") or []:
        label = str(t.get("label", "")).strip()
        if not label:
            continue
        tid = t.get("id") or slug(label, "task")
        base, i = tid, 2
        while tid in used:
            tid, i = f"{base}_{i}", i + 1
        used.add(tid)
        task = {"id": tid, "label": label}
        if t.get("required") is False:
            task["required"] = False
        out.append(task)
    return out


def _state(node: Node) -> dict:
    b = blocks.get(node.type)
    c = node.config
    st: dict = {"label": str(c.get("label", "")).strip() or node.id}
    if b.state_kind != "open":
        st["kind"] = b.state_kind
    if b.kind == "outcome":
        st["disposition"] = str(c.get("disposition") or "closed").strip()
        return st
    if c.get("queue"):
        st["queue"] = c["queue"]
    if c.get("sla_hours") not in (None, ""):
        st["sla_hours"] = c["sla_hours"]
    if c.get("description"):
        st["description"] = str(c["description"])
    tasks = _tasks(node)
    if tasks:
        st["tasks"] = tasks
    actions = [dict(a) for a in c.get("actions") or [] if a.get("do")]
    if actions:
        st["on_enter"] = actions
    return st


def _transition(src: str, dst: str, step: dict, when: list) -> dict:
    t: dict = {"from": src, "to": dst, "label": str(step.get("label", "")).strip(), "roles": list(step.get("roles") or [])}
    forms = [f for f in step.get("forms") or [] if f]
    requires = list(step.get("requires") or [])
    if forms and "form" not in requires:
        requires.append("form")  # naming forms means waiting for them
    if requires:
        t["requires"] = requires
    if forms:
        t["forms"] = forms
    if step.get("four_eyes"):
        t["four_eyes"] = True
    if when:
        t["when"] = when
    return t


def _routes(design: Design, decision: Node, seen: frozenset[str]) -> list[tuple[Node, list, str, Edge]]:
    """Every (final block, conditions, branch name, last edge) a case can reach through a decision, following chained decisions."""
    if decision.id in seen:
        raise CompileError(f"The decision '{decision.config.get('label') or decision.id}' leads back into itself")
    branches = decision.config.get("branches") or []
    by_handle: dict[str | None, list[Edge]] = {}
    for e in design.out_of(decision.id):
        by_handle.setdefault(e.handle, []).append(e)
    out = []
    groups = [(b.get("id"), cond.compile_group(b), b.get("label") or "") for b in branches]
    if decision.config.get("otherwise", True):
        negated: list = []
        for b in branches:
            negated += cond.negate_group(b)
        groups.append(("otherwise", negated, decision.config.get("otherwise_label") or "Otherwise"))
    for handle, when, name in groups:
        for e in by_handle.get(handle, []):
            nxt = design.node(e.target)
            if kind_of(nxt) == "decision":
                for final, more, inner, last in _routes(design, nxt, seen | {decision.id}):
                    out.append((final, when + more, f"{name}, {inner}" if name and inner else name or inner, last))
            elif nxt is not None:
                out.append((nxt, when, name, e))
    return out


def compile_design(design: Design, ctx: Context | None = None) -> dict:
    """The case-type definition for a design. Raises CompileError only for designs that cannot be translated at all;
    everything else is left to `validate` so the designer can show every problem at once."""
    if ctx is not None:
        ctx.design = design
    ids = state_ids(design)
    starts = [n for n in design.nodes if n.type == "start"]
    if len(starts) != 1:
        raise CompileError("A workflow needs exactly one start")
    first = design.out_of(starts[0].id)
    if not first or first[0].target not in ids:
        raise CompileError("Connect the start to the first stage")
    states = {ids[n.id]: _state(n) for n in design.nodes if n.id in ids}
    transitions = []
    for n in design.nodes:
        if n.id not in ids or kind_of(n) != "stage":
            continue
        for e in design.out_of(n.id):
            step = ctx.step(e) if ctx else dict(e.config)
            own = cond.compile_group(step.get("when"))
            nxt = design.node(e.target)
            if kind_of(nxt) == "decision":
                for final, when, name, last in _routes(design, nxt, frozenset()):
                    if final.id not in ids:
                        continue
                    label = str(last.config.get("label", "")).strip() or (f"{step.get('label', '')} ({name})" if name else step.get("label", ""))
                    transitions.append(_transition(ids[n.id], ids[final.id], {**step, "label": label}, own + when))
            elif nxt is not None and nxt.id in ids:
                transitions.append(_transition(ids[n.id], ids[nxt.id], step, own))
    st = design.settings
    out: dict = {"case_type": design.key, "title": design.title, "domain": design.domain or "Custom", "version": design.version}
    if design.description:
        out["description"] = design.description
    scenarios = list(starts[0].config.get("scenarios") or [])
    if scenarios:
        out["scenarios"] = scenarios
    out |= {"initial_state": ids[first[0].target], "states": states, "transitions": transitions,
            "sla": {"clock": st.deadline_label, "due_days": st.deadline_days},
            "escalation": {"raise_priority": st.escalation_raise_priority, "notify": st.escalation_notify}}
    if st.qa_sample_rate > 0:
        qa: dict = {"sample_rate": st.qa_sample_rate}
        if st.qa_dispositions:
            qa["dispositions"] = list(st.qa_dispositions)
        if st.qa_reopen_to and st.qa_reopen_to in ids:
            qa["reopen_to"] = ids[st.qa_reopen_to]
        out["qa"] = qa
    out["agents"] = {"on_create": list(st.agents), "autonomy": st.autonomy}
    return out


HEADER = "# Composed in the CaseForge workflow designer. Edit it there, or by hand: both publish the same way.\n"


def to_yaml(definition: dict) -> str:
    return HEADER + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True, width=120)
