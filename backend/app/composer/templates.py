"""Starting points for a new workflow, so nobody starts from an empty canvas unless they want to.

Templates name the kind of team they need ("first-line review", "approver") rather than a client's queue ids, and are
fitted to the client when used: each slot takes the client's best-matching queue, and the steps out of a stage default
to the roles held by the people in that queue. Every client's own case types are offered as starting points too.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..domain.access import AccessPolicy
from .compiler import Context, slug
from .interchange import from_definition, queue_roles
from .layout import auto_layout
from .model import Design, Edge, Node, Settings

# Words that identify a kind of team in a client's queue ids and names, best first.
SLOTS = {
    "first_line": ("l1", "triage", "alert", "screening", "kyc", "fraud"),
    "kyc": ("kyc", "due diligence", "onboarding", "l1"),
    "second_line": ("l2", "investigation", "edd", "officer", "decisions", "fraud"),
    "approver": ("mlro", "approval", "sign-off", "senior", "committee"),
    "senior": ("senior", "head", "chief", "committee", "approval"),
}


def pick_queue(policy: AccessPolicy, slot: str, avoid: set[str] = frozenset()) -> str | None:
    words = SLOTS.get(slot, (slot,))
    for w in words:
        for qid, label in policy.queues.items():
            if qid not in avoid and (w in qid.lower() or w in label.lower()):
                return qid
    rest = [q for q in policy.queues if q not in avoid]
    return rest[0] if rest else None


class Builder:
    """A small, readable way to write a template: stages, steps between them, and decisions."""

    def __init__(self, ctx: Context, key: str, title: str, domain: str, description: str):
        self.ctx, self.policy = ctx, ctx.policy
        self.design = Design(key=key, title=title, domain=domain, description=description, settings=Settings())
        self.used: set[str] = set()
        self.node("start", "start", label="Case opened", scenarios=[])

    def node(self, nid: str, btype: str, **config) -> str:
        self.design.nodes.append(Node(id=nid, type=btype, config=config))
        return nid

    def stage(self, nid: str, btype: str, label: str, slot: str, sla: float | None, tasks: tuple[str, ...] = (), **extra) -> str:
        queue = pick_queue(self.policy, slot, self.used)
        if queue:
            self.used.add(queue)
        cfg = {"label": label, "queue": queue, "sla_hours": sla, "tasks": [{"label": t, "required": True} for t in tasks], "actions": [], **extra}
        if btype == "approval":
            cfg.setdefault("approvers", queue_roles(self.ctx, queue))
            cfg.setdefault("four_eyes", True)
        return self.node(nid, btype, **cfg)

    def step(self, src: str, dst: str, label: str = "", requires: tuple[str, ...] = (), four_eyes: bool | None = None, handle: str | None = None):
        cfg: dict = {}
        source = next(n for n in self.design.nodes if n.id == src)
        if source.type not in ("start", "decision"):
            roles = source.config.get("approvers") or queue_roles(self.ctx, source.config.get("queue"))
            cfg = {"label": label, "roles": roles, "requires": list(requires)}
            if four_eyes is not None:
                cfg["four_eyes"] = four_eyes
        elif label:
            cfg = {"label": label}
        self.design.edges.append(Edge(id=f"e_{len(self.design.edges)}", source=src, target=dst, handle=handle, config=cfg))

    def done(self) -> Design:
        return auto_layout(self.design)


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    summary: str
    domain: str
    build: Callable[[Context], Design]
    tags: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "summary": self.summary, "domain": self.domain, "tags": list(self.tags)}


def _blank(ctx: Context) -> Design:
    b = Builder(ctx, "new_workflow", "New workflow", "Custom", "")
    b.stage("work", "stage", "Review", "first_line", 48, ("Review the case",))
    b.node("closed", "outcome", label="Closed", disposition="closed")
    b.step("start", "work")
    b.step("work", "closed", "Close", ("tasks", "note"))
    return b.done()


def _review(ctx: Context) -> Design:
    b = Builder(ctx, "two_level_review", "Two-level review", "Custom",
                "A first-line review that either closes the case or escalates it to a second line, whose decision a senior approves.")
    b.stage("review", "stage", "First-line review", "first_line", 48, ("Review the case and its evidence",))
    b.stage("second", "stage", "Second-line review", "second_line", 120, ("Assess the evidence", "Record a recommendation"))
    b.stage("approve", "approval", "Approval", "approver", 48)
    b.node("closed", "outcome", label="Closed, no action", disposition="no_action")
    b.node("actioned", "outcome", label="Actioned", disposition="actioned")
    b.step("start", "review")
    b.step("review", "closed", "Close, no concern", ("tasks", "note"))
    b.step("review", "second", "Escalate", ("tasks",))
    b.step("second", "approve", "Submit for approval", ("tasks", "note"))
    b.step("approve", "actioned", "Approve", (), True)
    b.step("approve", "second", "Return for rework", ("note",), False)
    return b.done()


def _filing(ctx: Context) -> Design:
    b = Builder(ctx, "investigation_to_filing", "Investigation to regulatory filing", "AML",
                "Triage, investigate, draft the narrative, then an MLRO approves. High-value or PEP cases need a senior second approval "
                "before the SAR is filed.")
    b.stage("triage", "stage", "Triage", "first_line", 48, ("Review the alert and agent briefing", "Check prior cases"),
            actions=[{"do": "screen_party"}])
    b.stage("investigate", "stage", "Investigation", "second_line", 120, ("Review activity against the profile", "Research counterparties"))
    b.used.discard(next(n.config["queue"] for n in b.design.nodes if n.id == "investigate"))  # the investigators draft too
    b.stage("draft", "stage", "SAR drafting", "second_line", 72, ("Narrative covers who, what, when, where and why",))
    b.stage("mlro", "approval", "MLRO review", "approver", 48)
    b.node("route", "decision", label="High value or PEP?", otherwise=True, otherwise_label="Standard",
           branches=[{"id": "b_high", "label": "$100,000 or more, or a PEP", "match": "any",
                      "conditions": [{"fact": "alerted_amount", "op": ">=", "value": 100000}, {"fact": "pep", "op": "==", "value": True}]}])
    b.stage("senior", "approval", "Senior approval", "senior", 24)
    b.node("filed", "outcome", label="SAR filed", disposition="sar_filed")
    b.node("no_sar", "outcome", label="Closed, no SAR", disposition="no_sar")
    b.step("start", "triage")
    b.step("triage", "investigate", "Escalate to investigation", ("tasks",))
    b.step("triage", "no_sar", "Close as not suspicious", ("tasks", "note", "reason"))
    b.step("investigate", "draft", "Recommend SAR", ("tasks",))
    b.step("investigate", "no_sar", "Close, no SAR", ("tasks", "note", "reason"))
    b.step("draft", "mlro", "Submit for MLRO review", ("tasks", "narrative"))
    b.step("mlro", "route", "Approve", ("narrative",), True)
    b.step("route", "senior", handle="b_high")
    b.step("route", "filed", "Approve and file SAR", handle="otherwise")
    b.step("mlro", "draft", "Return for rework", ("note",), False)
    b.step("senior", "filed", "Final approval and file SAR", ("narrative",), True)
    b.step("senior", "mlro", "Return to MLRO", ("note",), False)
    d = b.done()
    d.settings = Settings(deadline_label="SAR filing deadline", deadline_days=30, qa_sample_rate=0.2, qa_dispositions=["no_sar"],
                          qa_reopen_to="investigate", agents=["data_assembly", "entity_research", "narrative_writer"])
    return d


def _outreach(ctx: Context) -> Design:
    b = Builder(ctx, "customer_outreach", "Customer outreach", "KYC",
                "Ask the customer for information on arrival, wait for the answer, then review it. No answer in time escalates the case.")
    b.stage("review", "stage", "Initial review", "kyc", 24, ("Decide what to ask the customer",))
    b.used.clear()  # the same team asks, waits and assesses
    b.stage("waiting", "wait", "Awaiting the customer", "kyc", 240,
            actions=[{"do": "request_information", "recipient": "customer", "question": "Please send evidence of the source of these funds.",
                      "due_days": 10}])
    b.used.clear()
    b.stage("assess", "stage", "Assess the response", "kyc", 48, ("Check the response against the activity",))
    b.node("satisfied", "outcome", label="Satisfied", disposition="satisfied")
    b.node("exit", "outcome", label="Customer exited", disposition="exit")
    b.step("start", "review")
    b.step("review", "waiting", "Contact the customer", ("tasks",))
    b.step("waiting", "assess", "Response received", ("requests_answered",))
    b.step("assess", "satisfied", "Close, satisfied", ("tasks", "note"))
    b.step("assess", "exit", "Recommend exit", ("tasks", "note"))
    return b.done()


def _screening(ctx: Context) -> Design:
    b = Builder(ctx, "screening_triage", "Screening triage", "Sanctions",
                "Screen the customer on arrival. A possible match goes to an officer with a raised priority; no match is confirmed and closed.")
    b.stage("check", "stage", "Screening check", "first_line", 4, ("Confirm the customer's details",), actions=[{"do": "screen_party"}])
    b.node("route", "decision", label="Did screening find a match?", otherwise=True, otherwise_label="No match",
           branches=[{"id": "b_hit", "label": "Possible match", "match": "all", "conditions": [{"fact": "screening_hit", "op": "==", "value": True}]}])
    b.stage("officer", "approval", "Officer review", "second_line", 24,
            actions=[{"do": "raise_priority", "to": "high"}, {"do": "notify", "role": next(iter(ctx.policy.roles)), "message": "Possible watchlist match"}])
    b.node("clear", "outcome", label="No match", disposition="false_positive")
    b.node("match", "outcome", label="True match", disposition="true_match")
    b.step("start", "check")
    b.step("check", "route", "Complete check", ("tasks",))
    b.step("route", "officer", handle="b_hit")
    b.step("route", "clear", "Confirm no match", handle="otherwise")
    b.step("officer", "match", "Confirm true match", ("note",), True)
    b.step("officer", "clear", "Discount as false positive", ("note",), True)
    d = b.done()
    roles = [r for r in ctx.policy.roles if "team_lead" == r] or list(ctx.policy.roles)[:1]
    officer = next(n for n in d.nodes if n.id == "officer")
    officer.config["actions"][1]["role"] = roles[0]
    return d


TEMPLATES = {t.id: t for t in (
    Template("blank", "Start simple", "One review stage and an outcome, ready to grow.", "Custom", _blank, ("simple",)),
    Template("two_level_review", "Two-level review", "First line, second line, four-eyes approval, rework loop.", "Custom", _review,
             ("four-eyes", "rework")),
    Template("investigation_to_filing", "Investigation to SAR filing", "Screening on arrival, MLRO approval, value-based senior sign-off, "
             "SAR filed on close, QA sampling.", "AML", _filing, ("decision", "filing", "qa", "agents")),
    Template("customer_outreach", "Customer outreach", "Ask the customer automatically, wait, assess the answer.", "KYC", _outreach,
             ("waiting", "information request")),
    Template("screening_triage", "Screening triage", "Automatic watchlist screening routes possible matches to an officer.", "Sanctions",
             _screening, ("screening", "decision", "notify")),
)}


def build(template_id: str, ctx: Context) -> Design:
    if template_id.startswith("case_type:"):
        name = template_id.split(":", 1)[1]
        if name not in ctx.active:
            raise KeyError(name)
        from .validation import next_version

        d = from_definition(ctx.active[name])
        d.version = next_version(d.version)
        return d
    t = TEMPLATES[template_id]
    d = t.build(ctx)
    taken = set(ctx.active)
    base, i = d.key, 2
    while d.key in taken:
        d.key, i = f"{base}_{i}"[:58], i + 1
    return d


def catalog(ctx: Context) -> list[dict]:
    out = [t.as_dict() | {"kind": "template"} for t in TEMPLATES.values()]
    out += [{"id": f"case_type:{k}", "title": ct["title"], "summary": f"Your live {ct['title']} workflow, v{ct['version']}. Changes publish as "
             "a new version.", "domain": ct.get("domain", ""), "tags": ["live"], "kind": "case_type"} for k, ct in sorted(ctx.active.items())]
    return out


__all__ = ["TEMPLATES", "build", "catalog", "pick_queue", "slug"]
