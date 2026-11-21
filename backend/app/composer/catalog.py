"""What the designer's palette and forms offer one client: the blocks, the automatic actions, and every list a setting
can choose from (its roles, queues, facts, agents...). The designer draws itself from this; it hard-codes no block."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..agents.catalog import LEVEL_HELP, LEVEL_LABELS, LEVELS, catalog as agent_catalog
from ..cases import conditions
from ..cases.case_types import REQUIREMENTS, library
from ..cases.effects import REGISTRY as EFFECTS
from ..cases.requests import RECIPIENTS
from ..detection.scenarios import REGISTRY as SCENARIOS
from . import blocks
from .blocks import BRANCH_STEP_FIELDS, DISPOSITION_EFFECTS, STEP_FIELDS
from .compiler import Context
from .interchange import FORMATS, queue_roles


def _optional(module: str, fn: str, arg) -> list[dict]:
    """Lists from modules that may not be installed yet (forms, decision tables): empty when absent."""
    try:
        mod = __import__(f"app.{module}", fromlist=[fn])
        return list(getattr(mod, fn)(arg))
    except (ImportError, AttributeError):
        return []


def reason_counts(s: Session) -> dict[str, int]:
    from .. import reference

    out: dict[str, int] = {}
    for c in reference.codes(s, "disposition_reasons"):
        for d in c.get("applies_to", []):
            out[d] = out.get(d, 0) + 1
    return out


def build(s: Session, ctx: Context) -> dict:
    pol = ctx.policy
    dispositions = {d: d.replace("_", " ").capitalize() for ct in [*library().values(), *ctx.active.values()]
                    for st in ct["states"].values() if (d := st.get("disposition"))}
    dispositions |= {d: d.replace("_", " ").capitalize() for d in reason_counts(s)}
    for extra in ("no_action", "actioned", "closed", "satisfied"):
        dispositions.setdefault(extra, extra.replace("_", " ").capitalize())
    scenarios = {k: v.get("title", k) for k, v in SCENARIOS.items()}
    for ct in library().values():
        for sc in ct.get("scenarios", []):
            scenarios.setdefault(sc, sc.replace("_", " ").capitalize())
    owners = {sc: ct["title"] for ct in ctx.active.values() for sc in ct.get("scenarios", [])}
    return {
        "categories": blocks.CATEGORIES + ["Automatic actions"],
        "blocks": [b.as_dict() for b in blocks.REGISTRY.values()],
        "actions": [e.as_dict() for e in EFFECTS.values()],
        "step_fields": [f.as_dict() for f in STEP_FIELDS],
        "branch_step_fields": [f.as_dict() for f in BRANCH_STEP_FIELDS],
        "formats": [f.as_dict() for f in FORMATS.values()],
        "reference": {
            "roles": [{"id": r.id, "label": r.title, "people": [p.name for p in pol.holders(r.id)]} for r in pol.roles.values()],
            "queues": [{"id": q, "label": label, "roles": queue_roles(ctx, q), "people": [p.name for p in pol.queue_members(q)]}
                       for q, label in pol.queues.items()],
            "requirements": [{"id": k, "label": v} for k, v in REQUIREMENTS.items()],
            "facts": conditions.catalog(),
            "agents": [{"id": a.id, "label": a.title} for a in agent_catalog().values() if a.kind == "case_step"],
            "recipients": [{"id": k, "label": v} for k, v in RECIPIENTS.items()],
            "scenarios": [{"id": k, "label": v, "owner": owners.get(k)} for k, v in sorted(scenarios.items())],
            "dispositions": [{"id": k, "label": v, "does": DISPOSITION_EFFECTS.get(k, ""), "reasons": reason_counts(s).get(k, 0)}
                             for k, v in sorted(dispositions.items())],
            "autonomy": [{"id": lv, "label": f"{lv} · {LEVEL_LABELS[lv]}", "help": LEVEL_HELP[lv]} for lv in LEVELS],
            "forms": _optional("forms", "list_forms", s),
            "decision_tables": _optional("decisions", "list_tables", s),
            "live": [{"id": k, "title": ct["title"], "version": ct["version"]} for k, ct in sorted(ctx.active.items())],
        },
    }
