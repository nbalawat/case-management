"""Detection scenarios. Each is a pure function over one party's transactions.

The same definitions are meant to run in batch here and, later, in a stream processor.
"""
from __future__ import annotations

import contextlib
import contextvars
import difflib
import functools
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class Tuning:
    """What one run detects with: a client's own settings per scenario, and the scenarios it has switched off.

    A scenario the client has not touched runs on the defaults declared beside it."""

    params: dict[str, dict] = field(default_factory=dict)
    revisions: dict[str, int] = field(default_factory=dict)  # which of the client's versions each set of settings is
    off: frozenset[str] = frozenset()


_tuning: contextvars.ContextVar[Tuning | None] = contextvars.ContextVar("tuning", default=None)


def tuning() -> Tuning:
    return _tuning.get() or Tuning()


@contextlib.contextmanager
def tuned(t: Tuning):
    token = _tuning.set(t)
    try:
        yield
    finally:
        _tuning.reset(token)


def settings(name: str) -> dict:
    """The settings a scenario runs on right now: its defaults, with the client's own values over them."""
    return REGISTRY[name]["params"] | tuning().params.get(name, {})


def version_of(name: str) -> str:
    """The version an alert is stamped with: the scenario's own, then the client's revision of its settings if it has one."""
    n = tuning().revisions.get(name, 0)
    return f"{REGISTRY[name]['version']}/{n}" if n else REGISTRY[name]["version"]


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


def scenario(name: str, version: str, title: str, typology: str, params: dict, limits: dict | None = None):
    """Register a scenario. `limits` says, per setting, what it means and the range a client may move it within."""
    def deco(fn: Scenario) -> Scenario:
        REGISTRY[name] = {"name": name, "version": version, "title": title, "typology": typology, "params": params, "limits": limits or {}, "fn": fn}
        return fn
    return deco


@scenario("structuring", "1.2.0", "Cash structuring below reporting threshold", "Structuring",
          {"min": 8000, "max": 9999.99, "count": 3, "days": 7},
          {"min": ("Smallest cash deposit counted, in dollars", 1000, 9900), "count": ("Deposits needed to raise an alert", 2, 10),
           "days": ("Days the deposits must fall within", 1, 30)})
def structuring(txns: list[Transaction]) -> list[Hit]:
    p = settings("structuring")
    cash = [t for t in txns if t.channel == "cash" and t.direction == "in" and p["min"] <= t.amount <= p["max"]]
    hits, i = [], 0
    while i < len(cash):
        win = [t for t in cash[i:] if t.ts - cash[i].ts <= timedelta(days=p["days"])]
        if len(win) >= p["count"]:
            total = sum(t.amount for t in win)
            hits.append(Hit("structuring", version_of("structuring"), cash[i].ts.strftime("%G-W%V"), min(0.55 + 0.08 * len(win), 0.97),
                            f"{len(win)} cash deposits between ${p['min']:,} and $10,000 within {p['days']} days, totalling ${total:,.0f}.",
                            [t.id for t in win]))
            i += len(win)
        else:
            i += 1
    return hits


@scenario("rapid_movement", "1.0.3", "Rapid movement of funds", "Pass-through / mule account",
          {"min_in": 20000, "out_ratio": 0.8, "hours": 48},
          {"min_in": ("Smallest incoming payment counted, in dollars", 1000, 500000), "out_ratio": ("Share that must leave again", 0.3, 1.0),
           "hours": ("Hours within which it must leave", 6, 240)})
def rapid_movement(txns: list[Transaction]) -> list[Hit]:
    p = settings("rapid_movement")
    hits = []
    for t in txns:
        if t.direction != "in" or t.amount < p["min_in"] or t.channel == "cash":
            continue
        outs = [o for o in txns if o.direction == "out" and timedelta(0) <= o.ts - t.ts <= timedelta(hours=p["hours"])]
        moved = sum(o.amount for o in outs)
        if moved >= p["out_ratio"] * t.amount:
            hits.append(Hit("rapid_movement", version_of("rapid_movement"), t.id, min(0.6 + moved / t.amount * 0.2, 0.95),
                            f"${t.amount:,.0f} received from {t.counterparty or 'unknown'}; ${moved:,.0f} "
                            f"({moved / t.amount:.0%}) sent out within {p['hours']} hours.",
                            [t.id, *[o.id for o in outs]]))
    return hits


@scenario("high_risk_geo", "2.1.0", "Wires to high-risk jurisdictions", "High-risk geography",
          {"min_total": 10000, "days": 30},
          {"min_total": ("Smallest monthly total that raises an alert, in dollars", 1000, 250000)})
def high_risk_geo(txns: list[Transaction]) -> list[Hit]:
    p = settings("high_risk_geo")
    wires = [t for t in txns if t.channel == "wire" and t.direction == "out" and t.counterparty_country in HIGH_RISK_COUNTRIES]
    by_month: dict[str, list[Transaction]] = {}
    for t in wires:
        by_month.setdefault(t.ts.strftime("%Y-%m"), []).append(t)
    hits = []
    for month, ws in by_month.items():
        total = sum(t.amount for t in ws)
        if total >= p["min_total"]:
            countries = ", ".join(sorted({t.counterparty_country for t in ws}))
            hits.append(Hit("high_risk_geo", version_of("high_risk_geo"), month, min(0.5 + total / 200000, 0.9),
                            f"{len(ws)} outbound wires totalling ${total:,.0f} to high-risk jurisdictions ({countries}) in {month}.",
                            [t.id for t in ws]))
    return hits


class _Screen:
    """Name screening against one set of lists, made cheap to repeat.

    Each list name's matcher is built once (difflib indexes its second sequence, which is the costly part), and
    a counterparty name screened before is answered from memory: a customer's history names the same payees again
    and again, and detection re-reads that history on every batch. Answers are exactly those of comparing afresh.
    One per thread, because a matcher holds the sequence it is comparing.
    """

    MEMO = 200_000

    def __init__(self, w: Watch, threshold: float) -> None:
        self.w, self.th = w, threshold
        self.matchers = [(name, entry, difflib.SequenceMatcher(None, "", name.lower())) for name, entry in w.names]
        self.memo: dict[tuple[str, str], tuple[float, str] | None] = {}

    def best(self, seen: str, folded: str) -> tuple[float, str] | None:
        """The first list name, in list order, at or above the threshold and not ruled out for this name."""
        key = (seen, folded)
        if key in self.memo:
            return self.memo[key]
        found = None
        for name, entry, sm in self.matchers:
            if (folded, entry) in self.w.exempt:
                continue  # a person decided this name is not this entry
            sm.set_seq1(seen)
            r = sm.ratio()
            if r >= self.th:
                found = (r, name)
                break
        if len(self.memo) >= self.MEMO:
            self.memo.clear()
        self.memo[key] = found
        return found


@functools.lru_cache(maxsize=64)
def _screen(w: Watch, threshold: float, _thread: int) -> _Screen:
    return _Screen(w, threshold)


@scenario("sanctions_name", "1.1.0", "Counterparty name matches watchlist", "Sanctions exposure", {"threshold": 0.86})  # its threshold is the client's screening rule, on Lists
def sanctions_name(txns: list[Transaction]) -> list[Hit]:
    lists = watch()
    th = lists.threshold or settings("sanctions_name")["threshold"]
    screen = _screen(lists, th, threading.get_ident())
    hits = []
    for t in txns:
        if not t.counterparty:
            continue
        found = screen.best(t.counterparty.lower(), subject_key(t.counterparty))
        if found is not None:
            r, w = found
            hits.append(Hit("sanctions_name", version_of("sanctions_name"), t.id, round(r, 2),
                            f"Counterparty '{t.counterparty}' matches watchlist entry '{w}' at {r:.0%} similarity on a ${t.amount:,.0f} {t.channel}.",
                            [t.id]))
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
