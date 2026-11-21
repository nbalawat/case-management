"""Detection scenarios. Each is a pure function over one party's transactions.

The same definitions are meant to run in batch here and, later, in a stream processor.
"""
from __future__ import annotations

import contextlib
import contextvars
import difflib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from ..domain.models import Transaction

HIGH_RISK_COUNTRIES = {"IR", "KP", "SY", "MM", "AF"}
# Fictional names for the demo watchlist. Not a real sanctions list.
DEMO_WATCHLIST = ["Volkov Maritime Trading", "Northgate Petrochem FZE", "Darya Shipping Lines", "Zarin Exchange House"]


@dataclass(frozen=True)
class Watch:
    """What one run screens against: the names on a client's lists in force, and the pairs a person has ruled out."""

    names: tuple[tuple[str, str], ...]  # (name or alias, list entry id)
    exempt: frozenset[tuple[str, str]] = frozenset()  # (folded counterparty name, list entry id)
    threshold: float | None = None


_watch: contextvars.ContextVar[Watch | None] = contextvars.ContextVar("watch", default=None)


def subject_key(name: str) -> str:
    """A screened name as exemptions are keyed: case and spacing ignored."""
    return " ".join(name.lower().split())


def watch() -> Watch:
    """The lists in force for this run. Outside a run, the names every client starts with."""
    return _watch.get() or Watch(tuple((w, w) for w in DEMO_WATCHLIST))


@contextlib.contextmanager
def screening_against(w: Watch):
    token = _watch.set(w)
    try:
        yield
    finally:
        _watch.reset(token)


@dataclass
class Hit:
    scenario: str
    version: str
    window_key: str
    score: float
    explanation: str
    txn_ids: list[str]


Scenario = Callable[[list[Transaction]], list[Hit]]
REGISTRY: dict[str, dict] = {}


def scenario(name: str, version: str, title: str, typology: str, params: dict):
    def deco(fn: Scenario) -> Scenario:
        REGISTRY[name] = {"name": name, "version": version, "title": title, "typology": typology, "params": params, "fn": fn}
        return fn
    return deco


@scenario("structuring", "1.2.0", "Cash structuring below reporting threshold", "Structuring",
          {"min": 8000, "max": 9999.99, "count": 3, "days": 7})
def structuring(txns: list[Transaction]) -> list[Hit]:
    p = REGISTRY["structuring"]["params"]
    cash = [t for t in txns if t.channel == "cash" and t.direction == "in" and p["min"] <= t.amount <= p["max"]]
    hits, i = [], 0
    while i < len(cash):
        win = [t for t in cash[i:] if t.ts - cash[i].ts <= timedelta(days=p["days"])]
        if len(win) >= p["count"]:
            total = sum(t.amount for t in win)
            hits.append(Hit("structuring", "1.2.0", cash[i].ts.strftime("%G-W%V"), min(0.55 + 0.08 * len(win), 0.97),
                            f"{len(win)} cash deposits between ${p['min']:,} and $10,000 within {p['days']} days, totalling ${total:,.0f}.",
                            [t.id for t in win]))
            i += len(win)
        else:
            i += 1
    return hits


@scenario("rapid_movement", "1.0.3", "Rapid movement of funds", "Pass-through / mule account",
          {"min_in": 20000, "out_ratio": 0.8, "hours": 48})
def rapid_movement(txns: list[Transaction]) -> list[Hit]:
    p = REGISTRY["rapid_movement"]["params"]
    hits = []
    for t in txns:
        if t.direction != "in" or t.amount < p["min_in"] or t.channel == "cash":
            continue
        outs = [o for o in txns if o.direction == "out" and timedelta(0) <= o.ts - t.ts <= timedelta(hours=p["hours"])]
        moved = sum(o.amount for o in outs)
        if moved >= p["out_ratio"] * t.amount:
            hits.append(Hit("rapid_movement", "1.0.3", t.id, min(0.6 + moved / t.amount * 0.2, 0.95),
                            f"${t.amount:,.0f} received from {t.counterparty or 'unknown'}; ${moved:,.0f} "
                            f"({moved / t.amount:.0%}) sent out within {p['hours']} hours.",
                            [t.id, *[o.id for o in outs]]))
    return hits


@scenario("high_risk_geo", "2.1.0", "Wires to high-risk jurisdictions", "High-risk geography",
          {"min_total": 10000, "days": 30})
def high_risk_geo(txns: list[Transaction]) -> list[Hit]:
    p = REGISTRY["high_risk_geo"]["params"]
    wires = [t for t in txns if t.channel == "wire" and t.direction == "out" and t.counterparty_country in HIGH_RISK_COUNTRIES]
    by_month: dict[str, list[Transaction]] = {}
    for t in wires:
        by_month.setdefault(t.ts.strftime("%Y-%m"), []).append(t)
    hits = []
    for month, ws in by_month.items():
        total = sum(t.amount for t in ws)
        if total >= p["min_total"]:
            countries = ", ".join(sorted({t.counterparty_country for t in ws}))
            hits.append(Hit("high_risk_geo", "2.1.0", month, min(0.5 + total / 200000, 0.9),
                            f"{len(ws)} outbound wires totalling ${total:,.0f} to high-risk jurisdictions ({countries}) in {month}.",
                            [t.id for t in ws]))
    return hits


@scenario("sanctions_name", "1.1.0", "Counterparty name matches watchlist", "Sanctions exposure", {"threshold": 0.86})
def sanctions_name(txns: list[Transaction]) -> list[Hit]:
    lists = watch()
    th = lists.threshold or REGISTRY["sanctions_name"]["params"]["threshold"]
    hits = []
    for t in txns:
        if not t.counterparty:
            continue
        seen, folded = t.counterparty.lower(), subject_key(t.counterparty)
        for w, entry in lists.names:
            if (folded, entry) in lists.exempt:
                continue  # a person decided this name is not this entry
            r = difflib.SequenceMatcher(None, seen, w.lower()).ratio()
            if r >= th:
                hits.append(Hit("sanctions_name", "1.1.0", t.id, round(r, 2),
                                f"Counterparty '{t.counterparty}' matches watchlist entry '{w}' at {r:.0%} similarity on a ${t.amount:,.0f} {t.channel}.",
                                [t.id]))
                break
    return hits


# Scenarios with no per-transaction function here: raised by engines outside this platform (overlay mode), or by
# screening customers themselves against the lists.
EXTERNAL_SCENARIOS = {
    "sanctions_customer": {"title": "Customer name matches watchlist", "typology": "Sanctions exposure"},
    "account_takeover": {"title": "Account takeover", "typology": "Fraud"},
    "card_fraud": {"title": "Unauthorised card use", "typology": "Fraud"},
}


def describe(scenario: str) -> dict:
    """Title and typology for any scenario id, including ones this platform does not run itself."""
    known = REGISTRY.get(scenario) or EXTERNAL_SCENARIOS.get(scenario)
    if known:
        return {"title": known["title"], "typology": known["typology"]}
    return {"title": scenario.replace("_", " ").capitalize(), "typology": "External"}
