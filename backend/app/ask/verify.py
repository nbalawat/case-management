"""Checking an answer against what the tools returned: every record id it names, and every figure it states.

A record id in the answer is verified when a tool showed it in this answer, or it is a real record the asker may
see (one they typed themselves, say). A figure is verified when it matches a number some tool returned, as
written or rounded the way the answer rounds it ($1.2M for 1,234,567; 34% for 33.96). Anything else is reported
to the reader, never hidden: a model that does arithmetic in its head, or remembers a figure from somewhere
else, gets flagged. The `calculate` tool exists so that derived figures can be checked too.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

# Record ids the platform makes, by prefix. Client ids (customers, transactions) carry no prefix and are checked
# only against what the tools showed.
ID_PREFIXES = {"case": "case", "alr": "alert", "hld": "hold", "dsp": "dispute", "sar": "filing", "ctr": "filing", "str": "filing",
               "risk": "risk_assessment", "trg": "kyc_trigger", "run": "agent_run", "scv": "detection_change", "lbk": "lookback"}
PLATFORM_ID = re.compile(r"\b(" + "|".join(ID_PREFIXES) + r")-[0-9a-f]{10}\b")

NUMBER = re.compile(r"""(?<![\w.\-/])            # not inside an id, a date or a path
    (?P<cur>\$)?\s?
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s?(?P<unit>%|percent\b|k\b|K\b|m\b|M\b|mn\b|million\b|bn\b|billion\b|thousand\b)?
    (?![\w\-/]*\d)                                # not the start of an id or a date
    """, re.X)
DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}:\d{2}\b")
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}


def numbers_in(value: Any, out: set[float] | None = None) -> set[float]:
    """Every number anywhere in a tool result."""
    out = set() if out is None else out
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        if math.isfinite(value):
            out.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            numbers_in(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            numbers_in(v, out)
    elif isinstance(value, str):
        for m in NUMBER.finditer(DATE.sub(" ", value)):
            try:
                out.add(float(m.group("num").replace(",", "")))
            except ValueError:
                pass
    return out


def figures_in(answer: str) -> list[dict]:
    """The figures an answer states, with how precisely each is written."""
    text = PLATFORM_ID.sub(" ", DATE.sub(" ", answer))
    out = []
    for m in NUMBER.finditer(text):
        raw = m.group("num")
        digits = len(raw.split(".")[1]) if "." in raw else 0
        value = float(raw.replace(",", ""))
        unit = (m.group("unit") or "").lower()
        out.append({"text": m.group(0).strip(), "value": value, "digits": digits, "scale": SCALE.get(unit, 1.0),
                    "percent": unit in ("%", "percent"), "money": bool(m.group("cur"))})
    return out


def _matches(f: dict, known: Iterable[float]) -> bool:
    v, d, scale = f["value"], f["digits"], f["scale"]
    tol = 0.5 * 10 ** (-d)  # half a unit in the last place the answer wrote
    for k in known:
        k = abs(k)
        if abs(k / scale - v) <= tol + 1e-9:
            return True
        if f["percent"] and abs(k * 100 - v) <= tol + 1e-9:  # a share given as a fraction, written as a percent
            return True
    return False


def check_figures(answer: str, results: list[Any], question: str = "", history: Iterable[str] = ()) -> dict:
    """Which figures in the answer came from a tool result. Numbers the person used in the question (or the
    conversation so far) count as known, as do whole numbers up to 10 used as ordinary words ("the top 3")."""
    known: set[float] = set()
    for r in results:
        numbers_in(r, known)
    for text in (question, *history):
        numbers_in(text, known)
    stated = figures_in(answer)
    verified, unverified = [], []
    for f in stated:
        small_word = f["value"] <= 10 and f["digits"] == 0 and not (f["money"] or f["percent"] or f["scale"] != 1)
        (verified if small_word or _matches(f, known) else unverified).append(f["text"])
    return {"stated": len(stated), "verified": verified, "unverified": list(dict.fromkeys(unverified))}


def platform_ids(answer: str) -> list[tuple[str, str]]:
    """(kind, id) for every platform record id in the answer, in order, once each."""
    seen = dict.fromkeys((ID_PREFIXES[m.group(1)], m.group(0)) for m in PLATFORM_ID.finditer(answer))
    return list(seen)
