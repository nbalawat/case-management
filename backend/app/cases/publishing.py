"""Publishing a new version of a case type from the workflow editor."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import WorkflowVersion
from ..domain.personas import Persona
from . import case_types as registry
from .case_types import CaseTypeError


def hydrate(s: Session) -> None:
    """Load published versions from the database into the registry at start-up."""
    registry.reset_published()
    for row in s.scalars(select(WorkflowVersion).order_by(WorkflowVersion.id)):
        registry.register_published(registry.parse(row.source, f"{row.case_type} {row.version}"))


def check(source: str) -> dict:
    """Validate a draft and say what publishing it would change."""
    ct = registry.parse(source, "draft")
    current = registry.load_case_types().get(ct["case_type"])
    if current is None:
        return {"definition": ct, "is_new": True, "changes": [f"New case type '{ct['case_type']}'"]}
    if registry.version_key(ct["version"]) <= registry.version_key(current["version"]):
        raise CaseTypeError(f"draft: version must be higher than the active {current['version']}")
    return {"definition": ct, "is_new": False, "changes": diff(current, ct)}


def diff(old: dict, new: dict) -> list[str]:
    out = []
    for sid in new["states"]:
        if sid not in old["states"]:
            out.append(f"Adds stage '{new['states'][sid]['label']}'")
        elif new["states"][sid] != old["states"][sid]:
            out.append(f"Changes stage '{new['states'][sid]['label']}'")
    out += [f"Removes stage '{old['states'][sid]['label']}'" for sid in old["states"] if sid not in new["states"]]
    key = lambda t: (t["from"], t["to"], str(t.get("when")))  # noqa: E731
    olds, news = {key(t): t for t in old["transitions"]}, {key(t): t for t in new["transitions"]}
    out += [f"Adds step '{t['label']}'" for k, t in news.items() if k not in olds]
    out += [f"Changes step '{t['label']}'" for k, t in news.items() if k in olds and olds[k] != t]
    out += [f"Removes step '{t['label']}'" for k, t in olds.items() if k not in news]
    for section in ("sla", "qa", "escalation", "agents"):
        if old.get(section) != new.get(section):
            out.append(f"Changes {section.upper() if len(section) <= 3 else section} settings")
    return out or ["No structural change"]


def publish(s: Session, source: str, persona: Persona, note: str = "") -> dict:
    if not persona.can("publish_workflow"):
        raise CaseTypeError("Publishing a workflow needs the platform admin role")
    result = check(source)
    ct = result["definition"]
    s.add(WorkflowVersion(case_type=ct["case_type"], version=ct["version"], source=source, published_by=persona.id, note=note))
    s.flush()
    registry.register_published(ct)
    return result
