"""Turning the record ids in an answer into sources a person can open.

Each id the answer names is looked up (knowledge-library passages, `kb:<document>#<section>`, among them): if a tool showed it during this answer, or it is a real record the asker
may see, it becomes a source with a label and a link into the app; otherwise it is reported as unverified. Cases
are handled by the answer loop itself (they carry more detail), so they are left out here.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .. import knowledge
from ..domain.access import Persona
from ..tenancy import tenant_of
from .landscape import catalog, query
from .verify import platform_ids

KIND_TO_OBJECT = {o.kind: o for o in catalog.objects().values()}
MAX_SOURCES = 30
PASSAGE_ID = re.compile(r"\bkb:[a-z0-9][a-z0-9-]*#[a-z0-9-]+")


def link(kind: str, rec: dict) -> str:
    """Where in the app a person opens this record."""
    rid, case_id, customer_id = rec.get("id", ""), rec.get("case_id"), rec.get("customer_id")
    if kind == "case":
        return f"/cases/{rid}"
    if kind == "customer":
        return f"/customers/{rid}"
    if kind == "filing":
        return f"/filings/{rid}"
    if case_id:
        return f"/cases/{case_id}"
    if kind == "transaction" and customer_id:
        return f"/customers/{customer_id}"
    return {"alert": "/alerts", "hold": "/sanctions", "dispute": "/fraud", "risk_assessment": "/kyc", "kyc_trigger": "/kyc",
            "agent_run": "/agents", "agent_action": "/agents", "detection_change": "/detection", "lookback": "/detection",
            "pipeline_run": "/data"}.get(kind, "") or (f"/customers/{customer_id}" if customer_id else "")


def _label(obj, row: dict) -> str:
    bits = [str(row[c]) for c in obj.list_columns[1:4] if row.get(c) not in (None, "")]
    return f"{obj.kind.replace('_', ' ').capitalize()} {row.get(obj.id_dim)}" + (f": {', '.join(bits)}" if bits else "")


def resolve(s: Session, asker: Persona, answer: str, seen: dict[str, dict], exclude: set[str] = frozenset()) -> tuple[list[dict], list[str]]:
    """(sources, unverified ids). `seen` maps ids a tool showed in this answer to {kind, id, case_id, customer_id}."""
    named: list[tuple[str, str]] = [(k, i) for k, i in platform_ids(answer) if i not in exclude and k != "case"]
    named += [("policy", i) for i in dict.fromkeys(PASSAGE_ID.findall(answer))]
    # Ids a tool showed count wherever the answer names them, including client ids (customers, transactions),
    # which carry no prefix and so are never claims on their own.
    for rid, rec in seen.items():
        if rec["kind"] == "case" or rid in exclude or any(rid == i for _, i in named):
            continue
        if re.search(rf"(?<![\w-]){re.escape(rid)}(?![\w-])", answer):
            named.append((rec["kind"], rid))
    sources, unverified = [], []
    for kind, rid in named[:MAX_SOURCES]:
        if kind == "policy":  # a library passage: the asker's own client's library, never another client's documents
            p = knowledge.passage(tenant_of(s), rid)
            if p is None:
                unverified.append(rid)
            else:
                sources.append({"kind": "policy", "id": rid, "label": f"{p.title}: {p.section}", "link": knowledge.link(rid),
                                "shown_by_tool": rid in seen, "excerpt": p.text[:600], "client_document": p.client is not None})
            continue
        obj = KIND_TO_OBJECT.get(kind)
        row = None
        if obj is not None and obj.permission(asker) is None:
            row = query.get_one(s, asker, obj, rid)
        if row is None:
            unverified.append(rid)  # not shown by a tool and not a record this person can see
            continue
        rec = {"id": rid, "case_id": row.get(obj.case_dim) if obj.case_dim else None,
               "customer_id": row.get(obj.customer_dim) if obj.customer_dim else None}
        sources.append({"kind": kind, "id": rid, "label": _label(obj, row), "link": link(kind, rec), "shown_by_tool": rid in seen})
    return sources, unverified
