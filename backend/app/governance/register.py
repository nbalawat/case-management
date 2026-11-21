"""The change register: every change to how the platform is set up for a client, from wherever it was made, in one list.

Nothing is stored here. Each kind of configuration keeps its own versions; this reads them all and puts them in order,
so an auditor does not have to know that workflows, access, agents, lists and detection each have a history of their own.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cases.case_types import library, load_case_types
from ..detection.scenarios import describe
from ..domain.models import (AccessPolicyVersion, AccessReview, AgentPolicyVersion, CodeList, Lookback, ScenarioVersion, ScreeningExemption, Watchlist,
                             WorkflowVersion)
from ..tenancy import tenant_of
from ..visibility import system_work

AREAS = ("Workflows", "Access", "Agents", "Detection", "Screening", "Reference lists")


def _entry(at: datetime, area: str, subject: str, what: str, by: str, second: str | None = None, note: str = "", link: str = "") -> dict:
    return {"at": at.isoformat(), "area": area, "subject": subject, "what": what, "by": by, "second": second, "note": note, "link": link}


@system_work
def entries(s: Session, area: str | None = None, since: datetime | None = None) -> list[dict]:
    """Every recorded change, newest first."""
    types = library() | load_case_types(tenant_of(s))
    out: list[dict] = []
    for w in s.scalars(select(WorkflowVersion)):
        out.append(_entry(w.published, "Workflows", types.get(w.case_type, {}).get("title", w.case_type), f"Version {w.version} published", w.published_by, note=w.note, link="/workflows"))
    for a in s.scalars(select(AccessPolicyVersion)):
        out.append(_entry(a.published, "Access", "Access policy", f"Version {a.version} published", a.published_by, note=a.note, link="/access"))
    for r in s.scalars(select(AccessReview)):
        out.append(_entry(r.started, "Access", r.name, f"Recertification started for {len(r.items)} people", r.started_by, link="/governance"))
        if r.closed_at:
            changes = sum(i.get("decision") in ("change", "remove") for i in r.items)
            out.append(_entry(r.closed_at, "Access", r.name, f"Recertification closed: {changes} change(s) asked for", r.closed_by or "", link="/governance"))
    for g in s.scalars(select(AgentPolicyVersion)):
        out.append(_entry(g.published, "Agents", "Agent policy", f"Version {g.version} published", g.published_by, note=g.note, link="/agents"))
    for v in s.scalars(select(ScenarioVersion)):
        title = describe(v.scenario)["title"]
        out.append(_entry(v.proposed_at, "Detection", title, f"Settings revision {v.number} proposed" + ("" if v.enabled else " (switch off)"), v.proposed_by, note=v.reason, link="/detection"))
        if v.decided_at and v.status != "pending":
            what = {"live": "put live", "superseded": "put live", "refused": "refused", "withdrawn": "withdrawn"}.get(v.status, v.status)
            second = v.decided_by if v.status in ("live", "superseded", "refused") else None
            out.append(_entry(v.decided_at, "Detection", title, f"Settings revision {v.number} {what}", v.proposed_by, second, v.decision_note, "/detection"))
    for lb in s.scalars(select(Lookback)):
        if lb.run_at:
            out.append(_entry(lb.run_at, "Detection", lb.name, f"Lookback run: {lb.preview.get('raised', 0)} alert(s) raised", lb.run_by or "", note=lb.reason, link="/detection"))
        if lb.closed_at:
            out.append(_entry(lb.closed_at, "Detection", lb.name, "Lookback signed off", lb.run_by or "", lb.closed_by, lb.conclusion, "/detection"))
    for wl in s.scalars(select(Watchlist)):
        for h in wl.history:
            moved = ", ".join(f"{h[k]} {k}" for k in ("added", "changed", "removed") if h.get(k))
            out.append(_entry(datetime.fromisoformat(h["at"]), "Screening", wl.name, f"Version {h['version']}" + (f": {moved}" if moved else ""), h.get("by", ""), note=h.get("note", ""), link="/sanctions"))
    for x in s.scalars(select(ScreeningExemption)):
        if x.decided_at and x.status in ("active", "revoked", "refused"):
            what = {"active": "False-match exemption approved", "refused": "False-match exemption refused", "revoked": "False-match exemption ended"}[x.status]
            out.append(_entry(x.decided_at, "Screening", x.subject, what, x.proposed_by, x.decided_by, x.decision_note or x.reason, "/sanctions"))
    for c in s.scalars(select(CodeList)):
        out.append(_entry(c.updated_at, "Reference lists", c.id.replace("_", " ").capitalize(), f"Saved with {len(c.codes)} entries (the latest save; earlier ones are not kept)",
                          c.updated_by, link="/reference"))
    if area:
        out = [e for e in out if e["area"] == area]
    if since:
        out = [e for e in out if e["at"] >= since.isoformat()]
    return sorted(out, key=lambda e: e["at"], reverse=True)


def as_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["when", "area", "subject", "change", "by", "second person", "note"])
    for e in rows:
        w.writerow([e["at"], e["area"], e["subject"], e["what"], e["by"], e["second"] or "", e["note"]])
    return buf.getvalue()
