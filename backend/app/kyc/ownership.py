"""Beneficial ownership: who ultimately owns and controls a customer, worked out from the recorded structure.

A structure is a set of parties (nodes) and holdings (links). `owned: customer` is a holding in the customer itself.
Effective ownership multiplies shares down every chain; a person reaching the client's threshold by any combination
of chains, or holding control, is a beneficial owner. Whatever is not traced to a person is reported as unidentified.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..detection.scenarios import HIGH_RISK_COUNTRIES
from ..domain.models import OwnershipStructure, Party, new_id, now
from ..domain.personas import Persona
from ..visibility import system_work

CUSTOMER = "customer"
KINDS = ("person", "company", "trust", "nominee", "government", "listed_company", "unknown")
NATURAL = {"person"}  # where a chain can end with the owner identified
TRANSPARENT = {"government", "listed_company"}  # ends a chain without a person, and without being opaque
_key = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_NODES = 60


class OwnershipError(ValueError):
    pass


def check(nodes: list, links: list) -> tuple[list[dict], list[dict]]:
    """Validate a structure and return it cleaned. Refuses anything that cannot be a real shareholding."""
    if not isinstance(nodes, list) or not isinstance(links, list) or not nodes or not links:
        raise OwnershipError("A structure needs at least one owner and one holding")
    if len(nodes) > MAX_NODES:
        raise OwnershipError(f"A structure can hold at most {MAX_NODES} parties")
    clean_nodes, keys = [], set()
    for n in nodes:
        if not isinstance(n, dict) or not _key.match(str(n.get("key") or "")) or n["key"] == CUSTOMER:
            raise OwnershipError("Each party needs a short key: lower-case letters, digits and underscores (and not 'customer')")
        if n["key"] in keys:
            raise OwnershipError(f"Party '{n['key']}' appears twice")
        if not str(n.get("name") or "").strip():
            raise OwnershipError(f"Party '{n['key']}' needs a name")
        if n.get("kind") not in KINDS:
            raise OwnershipError(f"Party '{n['key']}': kind must be one of {', '.join(KINDS)}")
        country = str(n.get("country") or "").upper()
        if country and not re.fullmatch(r"[A-Z]{2}", country):
            raise OwnershipError(f"Party '{n['key']}': country is a two-letter code")
        keys.add(n["key"])
        clean_nodes.append({"key": n["key"], "name": str(n["name"]).strip()[:200], "kind": n["kind"], "country": country,
                            "pep": bool(n.get("pep")), "verified": bool(n.get("verified"))})
    clean_links, seen, held = [], set(), {}
    for x in links:
        if not isinstance(x, dict) or x.get("owner") not in keys or x.get("owned") not in keys | {CUSTOMER}:
            raise OwnershipError("Each holding names an owner and what it owns, both from the list of parties (or 'customer')")
        if x["owner"] == x["owned"]:
            raise OwnershipError(f"'{x['owner']}' cannot own itself")
        if (x["owner"], x["owned"]) in seen:
            raise OwnershipError(f"The holding of '{x['owner']}' in '{x['owned']}' appears twice")
        pct, control = x.get("percent", 0), bool(x.get("control"))
        if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not 0 <= pct <= 100 or (pct == 0 and not control):
            raise OwnershipError(f"The holding of '{x['owner']}' in '{x['owned']}' needs a share between 0 and 100, or control without shares")
        seen.add((x["owner"], x["owned"]))
        held[x["owned"]] = held.get(x["owned"], 0.0) + pct
        clean_links.append({"owner": x["owner"], "owned": x["owned"], "percent": round(float(pct), 4), "control": control})
    over = sorted(k for k, v in held.items() if v > 100.0001)
    if over:
        raise OwnershipError(f"More than 100% of {', '.join(over)} is held")
    if CUSTOMER not in held and not any(x["owned"] == CUSTOMER for x in clean_links):
        raise OwnershipError("No holding in the customer itself is recorded")
    orphans = sorted(keys - {x["owner"] for x in clean_links})
    if orphans:
        raise OwnershipError(f"These parties hold nothing: {', '.join(orphans)}")
    return clean_nodes, clean_links


def analyse(nodes: list[dict], links: list[dict], threshold: float = 25.0, complex_layers: int = 3) -> dict:
    """Effective ownership of the customer, the beneficial owners, and what makes the structure a concern."""
    by_key = {n["key"]: n for n in nodes}
    owners_of: dict[str, list[dict]] = {}
    for x in links:
        owners_of.setdefault(x["owned"], []).append(x)
    effective: dict[str, float] = {}
    controls: set[str] = set()
    depth_of: dict[str, int] = {}
    state = {"circular": False, "unidentified": 0.0, "layers": 0}

    def walk(target: str, share: float, controlled: bool, path: tuple[str, ...]) -> None:
        """Follow every holding in `target` upward. `controlled` stays true only while each step so far carried control."""
        depth = len(path)  # 1 for a direct holder of the customer
        holdings = owners_of.get(target, [])
        # Shares in this party that nobody is named for are owned by someone, and that someone is not identified.
        state["unidentified"] += share * max(0.0, 100.0 - sum(x["percent"] for x in holdings)) / 100
        for x in holdings:
            owner, part, ctl = x["owner"], share * x["percent"] / 100, controlled and x["control"]
            if owner in path:
                state["circular"] = True  # companies owning each other in a circle: that share is never traced to anyone
                state["unidentified"] += part
                continue
            depth_of[owner] = max(depth_of.get(owner, 0), depth)
            kind = by_key[owner]["kind"]
            if kind in NATURAL or kind in TRANSPARENT:
                effective[owner] = effective.get(owner, 0.0) + part
                if ctl:
                    controls.add(owner)
                state["layers"] = max(state["layers"], depth - 1)
            elif owner in owners_of:
                walk(owner, part, ctl, path + (owner,))
            else:
                state["unidentified"] += part  # the chain stops at a company, trust or nominee with nobody behind it
                effective[owner] = effective.get(owner, 0.0) + part
                state["layers"] = max(state["layers"], depth)

    walk(CUSTOMER, 100.0, True, (CUSTOMER,))
    unidentified = round(state["unidentified"], 2)
    rows = []
    for key, pct in sorted(effective.items(), key=lambda kv: -kv[1]):
        n = by_key[key]
        person = n["kind"] in NATURAL
        rows.append({"key": key, "name": n["name"], "kind": n["kind"], "country": n["country"], "pep": n["pep"], "verified": n["verified"],
                     "effective": round(pct, 2), "control": key in controls, "layers": depth_of.get(key, 1) - 1,
                     "beneficial_owner": person and (pct >= threshold - 1e-9 or key in controls)})
    for key in sorted(controls - set(effective)):
        n = by_key[key]
        rows.append({"key": key, "name": n["name"], "kind": n["kind"], "country": n["country"], "pep": n["pep"], "verified": n["verified"],
                     "effective": 0.0, "control": True, "layers": depth_of.get(key, 1) - 1, "beneficial_owner": n["kind"] in NATURAL})
    owners = [r for r in rows if r["beneficial_owner"]]
    risky = sorted({n["country"] for n in nodes if n["country"] in HIGH_RISK_COUNTRIES})
    flags = []
    if unidentified >= threshold - 1e-9:
        flags.append({"code": "opaque", "text": f"{unidentified:g}% of the customer is not traced to a person"})
    if state["layers"] >= complex_layers:
        flags.append({"code": "complex", "text": f"Ownership runs through {state['layers']} layers of companies"})
    if any(n["kind"] in ("nominee", "trust") for n in nodes):
        flags.append({"code": "nominee_or_trust", "text": "A nominee or trust sits in the structure: " + ", ".join(n["name"] for n in nodes if n["kind"] in ("nominee", "trust"))})
    if state["circular"]:
        flags.append({"code": "circular", "text": "Companies in the structure own each other in a circle"})
    if any(r["pep"] for r in owners):
        flags.append({"code": "pep", "text": "A beneficial owner is a politically exposed person: " + ", ".join(r["name"] for r in owners if r["pep"])})
    if risky:
        flags.append({"code": "high_risk_country", "text": "Parties in higher-risk countries: " + ", ".join(risky)})
    if any(not r["verified"] for r in owners):
        flags.append({"code": "unverified", "text": "Identity not yet verified for: " + ", ".join(r["name"] for r in owners if not r["verified"])})
    if not owners:
        flags.append({"code": "no_owner", "text": f"No person reaches {threshold:g}% or holds control, so no beneficial owner is identified"})
    return {"owners": rows, "beneficial_owners": owners, "unidentified": unidentified, "layers": state["layers"], "threshold": threshold, "flags": flags}


def _rules(s: Session) -> tuple[float, int]:
    return (float(reference.setting(s, "ownership_rules", "beneficial_owner_percent", "value", 25)),
            int(reference.setting(s, "ownership_rules", "complex_layers", "value", 3)))


@system_work
def current(s: Session, party_id: str) -> OwnershipStructure | None:
    return s.scalars(select(OwnershipStructure).where(OwnershipStructure.party_id == party_id, OwnershipStructure.current.is_(True))).first()


@system_work
def view(s: Session, party_id: str) -> dict:
    """The structure in force with its analysis, and the versions before it."""
    rows = list(s.scalars(select(OwnershipStructure).where(OwnershipStructure.party_id == party_id).order_by(OwnershipStructure.version.desc())))
    cur = next((r for r in rows if r.current), None)
    threshold, layers = _rules(s)
    return {"structure": None if cur is None else {"id": cur.id, "version": cur.version, "nodes": cur.nodes, "links": cur.links, "source": cur.source,
                                                    "note": cur.note, "recorded_by": cur.recorded_by, "recorded_at": cur.recorded_at.isoformat()},
            "analysis": None if cur is None else analyse(cur.nodes, cur.links, threshold, layers), "kinds": list(KINDS),
            "history": [{"version": r.version, "recorded_by": r.recorded_by, "recorded_at": r.recorded_at.isoformat(), "source": r.source, "note": r.note,
                         "beneficial_owners": [o["name"] for o in analyse(r.nodes, r.links, threshold, layers)["beneficial_owners"]]} for r in rows]}


@system_work
def record(s: Session, party_id: str, nodes: list, links: list, persona: Persona, source: str = "", note: str = "", ts: datetime | None = None) -> OwnershipStructure:
    """Record the structure as now understood. The previous version is kept; a change of beneficial owners triggers a review."""
    if persona.read_only or not persona.can("work"):
        raise OwnershipError("Recording ownership needs someone who works cases")
    party = s.scalars(select(Party).where(Party.id == party_id)).first()
    if party is None:
        raise OwnershipError("Customer not found")
    clean_nodes, clean_links = check(nodes, links)
    ts = ts or now()
    before = current(s, party_id)
    threshold, layers = _rules(s)
    row = OwnershipStructure(id=new_id("own"), party_id=party_id, version=(before.version + 1) if before else 1, nodes=clean_nodes, links=clean_links,
                             source=str(source)[:80], note=str(note)[:1000], recorded_by=persona.id, recorded_at=ts)
    if before is not None:
        before.current = False
    s.add(row)
    s.flush()
    if before is not None:
        was = {(o["name"], o["kind"]) for o in analyse(before.nodes, before.links, threshold, layers)["beneficial_owners"]}
        now_ = {(o["name"], o["kind"]) for o in analyse(clean_nodes, clean_links, threshold, layers)["beneficial_owners"]}
        if was != now_:
            from . import triggers

            gained, lost = sorted(n for n, _ in now_ - was), sorted(n for n, _ in was - now_)
            detail = "; ".join(x for x in (f"new: {', '.join(gained)}" if gained else "", f"no longer: {', '.join(lost)}" if lost else "") if x)
            triggers.fire(s, party_id, "ownership_changed", f"ownership:{row.id}", f"Beneficial owners changed ({detail})", ts)
    return row
