"""Publishing a new version of a case type from the workflow editor. Versions belong to one client."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.models import WorkflowVersion
from ..domain.personas import Persona, policy_of
from ..tenancy import tenant_of
from . import case_types as registry
from .case_types import CaseTypeError
from ..visibility import system_work


def check(tenant_id: str, source: str) -> dict:
    """Validate a draft against the client's own access policy and say what publishing it would change."""
    ct = registry.parse(source, "draft")
    registry.check_policy(ct, policy_of(tenant_id), "draft")
    current = registry.load_case_types(tenant_id).get(ct["case_type"])
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


@system_work
def publish(s: Session, source: str, persona: Persona, note: str = "") -> dict:
    if not persona.can("publish_workflow"):
        raise CaseTypeError("Publishing a workflow needs the 'publish workflow' permission")
    tenant = tenant_of(s)
    result = check(tenant, source)
    ct = result["definition"]
    s.add(WorkflowVersion(case_type=ct["case_type"], version=ct["version"], source=source, published_by=persona.id, note=note))
    s.flush()
    registry.register_published(tenant, ct)
    return result
