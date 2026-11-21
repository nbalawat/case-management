"""The catalogue of stream jobs and how one is rendered for a client."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..backbone import topics
from ..config import settings
from ..detection.scenarios import DEMO_WATCHLIST, HIGH_RISK_COUNTRIES, REGISTRY, subject_key

HERE = Path(__file__).parent
TENANT = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
BOOTSTRAP = re.compile(r"^[A-Za-z0-9.\-]+:\d{1,5}(,[A-Za-z0-9.\-]+:\d{1,5})*$")
COUNTRY = re.compile(r"[A-Z]{2}")
PLACEHOLDER = re.compile(r"\$\{([A-Z_]+)\}")
PLAIN = re.compile(r"[A-Za-z0-9 ]")
MAX_NAMES = 5000  # a list is written into its job, so a job has a largest list. See sanctions_name.sql
MAX_NAME = 200


class JobError(ValueError):
    pass


def _number(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _structuring(p: dict) -> dict[str, str]:
    lo, hi, count, days = float(p["min"]), float(p["max"]), int(p["count"]), int(p["days"])
    if not (0 < lo <= hi and 2 <= count <= 50 and 1 <= days <= 90):
        raise ValueError
    return {"MIN": _number(lo), "MAX": _number(hi), "MIN_TEXT": f"{lo:,.0f}", "COUNT": str(count), "DAYS": str(days)}


def _rapid_movement(p: dict) -> dict[str, str]:
    least, ratio, hours = float(p["min_in"]), float(p["out_ratio"]), int(p["hours"])
    if not (0 < least <= 1e12 and 0 < ratio <= 1 and 1 <= hours <= 720):
        raise ValueError
    return {"MIN_IN": _number(least), "RATIO": _number(ratio), "HOURS": str(hours)}


def _high_risk_geo(p: dict) -> dict[str, str]:
    total, countries = float(p["min_total"]), sorted(HIGH_RISK_COUNTRIES)
    if not (0 < total <= 1e12 and countries and all(COUNTRY.fullmatch(c) for c in countries)):
        raise ValueError
    return {"MIN_TOTAL": _number(total), "COUNTRIES": ", ".join(f"'{c}'" for c in countries), "COUNTRIES_TEXT": ", ".join(countries)}


def quoted(text: str) -> str:
    """Text as a Flink SQL string that can hold nothing but itself.

    Letters, digits and spaces are written as they are. Every other character is written as its Unicode number, so no
    quote, semicolon, comment marker or placeholder from a list can reach the SQL."""
    out = []
    for ch in text:
        if PLAIN.fullmatch(ch):
            out.append(ch)
        else:  # as UTF-16, two numbers for a character outside the basic plane: the form Flink reads back correctly
            units = ch.encode("utf-16-be", "surrogatepass")
            out.extend(f"\\{int.from_bytes(units[i:i + 2], 'big'):04X}" for i in range(0, len(units), 2))
    return "U&'" + "".join(out) + "'"


def _listed(p: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """The names to screen against as (name, list entry) and the pairs ruled out as (folded counterparty, list entry)."""
    names = [(str(n), str(e)) for n, e in p["names"]] if "names" in p else [(w, w) for w in DEMO_WATCHLIST]
    return names, [(str(k), str(e)) for k, e in p.get("exempt", [])]


def _sanctions_name(p: dict) -> dict[str, str]:
    threshold = float(p["threshold"])
    names, exempt = _listed(p)
    if not (0 < threshold <= 1 and 0 < len(names) <= MAX_NAMES and all(0 < len(n) <= MAX_NAME for n, _e in names)
            and all(0 < len(k) <= MAX_NAME for k, _e in exempt)):
        raise ValueError
    # The function is told which position on the list a ruled-out pair is about, so list entry ids stay out of the job.
    ruled_out = sorted({f"{i}:{k}" for k, entry in exempt for i, (_n, e) in enumerate(names) if e == entry})
    return {"THRESHOLD": repr(threshold), "THRESHOLD_TEXT": f"{threshold:.0%}", "NAMES": ", ".join(quoted(n) for n, _e in names),
            "NAMES_COUNT": str(len(names)), "EXEMPT": ", ".join(quoted(x) for x in ruled_out) or "''", "EXEMPT_COUNT": str(len(ruled_out))}


def _lists_in_force(s) -> dict:
    """What the client screens against now, read from its lists. Imported here: lists depend on detection, as this does."""
    from ..sanctions import lists

    w = lists.watch(s)
    out: dict = {"names": [list(pair) for pair in w.names], "exempt": sorted([k, e] for k, e in w.exempt)}
    return out | ({"threshold": w.threshold} if w.threshold else {})


def _lists_summary(p: dict) -> dict:
    """What is recorded of a start: the size of the list and a fingerprint of it, not the list."""
    names, exempt = _listed(p)
    mark = hashlib.sha256(json.dumps([names, sorted(exempt)], ensure_ascii=True).encode()).hexdigest()[:16]
    return {"threshold": p.get("threshold"), "names": len(names), "ruled_out": len(exempt), "list_fingerprint": mark}


@dataclass(frozen=True)
class Job:
    name: str
    scenario: str  # the application scenario this job is the streaming form of
    title: str
    source: str  # what its alerts carry as their source
    fill: Callable[[dict], dict[str, str]]  # the scenario's settings, checked, as the text written into the job
    more: Callable[[object], dict] | None = None  # what else the job is rendered from, read from the client's database
    summary: Callable[[dict], dict] = dict  # what is recorded of the settings a job was started on

    @property
    def template(self) -> str:
        return (HERE / f"{self.name}.sql").read_text()


def _job(name: str, fill: Callable[[dict], dict[str, str]], **also) -> Job:
    """A job is the streaming form of the scenario of the same name, and is titled as that scenario is."""
    return Job(name, name, REGISTRY[name]["title"], f"flink-{name.replace('_', '-')}", fill, **also)


JOBS = {j.name: j for j in (_job("structuring", _structuring), _job("rapid_movement", _rapid_movement), _job("high_risk_geo", _high_risk_geo),
                            _job("sanctions_name", _sanctions_name, more=_lists_in_force, summary=_lists_summary))}
COMMON = (HERE / "common.sql").read_text().split("\n\n", 1)[1].rstrip()  # without its own heading comment


def in_force(s, job: str) -> tuple[dict, bool]:
    """Everything the job is rendered from for the session's client, and whether its scenario is switched on."""
    from ..detection import tuning

    spec = JOBS[job]
    params, on, _revision = tuning.current(s, spec.scenario)
    return params | (spec.more(s) if spec.more else {}), on


def job_name(job: str, tenant_id: str) -> str:
    """What the job is called on the cluster. Status is read back by this name."""
    return f"{settings.event_topic_prefix} {job} {tenant_id}"


def bootstrap_of(url: str) -> str:
    """kafka://host:9092,host2:9092 as the host list a Kafka client wants."""
    if not url.startswith("kafka://"):
        raise JobError("A stream job reads Kafka. Set EVENT_BUS_URL to kafka://host:port, or pass the brokers.")
    return url.removeprefix("kafka://").split("?")[0].strip("/")


def render(job: str, tenant_id: str, params: dict | None = None, bootstrap: str | None = None) -> str:
    """The job's SQL for one client. `params` are the scenario's settings in force; the defaults when not given.

    Everything written into the SQL is checked first: this text is executed by the cluster.
    """
    if job not in JOBS:
        raise JobError(f"No stream job called '{job}'. There is: {', '.join(JOBS)}.")
    if not TENANT.fullmatch(tenant_id or ""):
        raise JobError("That is not a client id.")
    bootstrap = bootstrap or bootstrap_of(settings.event_bus_url)
    if not BOOTSTRAP.fullmatch(bootstrap):
        raise JobError("Brokers must be host:port, separated by commas.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", settings.event_topic_prefix):
        raise JobError("EVENT_TOPIC_PREFIX must be lower-case letters, digits and hyphens to be used in a stream job.")
    spec = JOBS[job]
    try:
        own = spec.fill(REGISTRY[spec.scenario]["params"] | (params or {}))
    except (KeyError, TypeError, ValueError, OverflowError):
        raise JobError("The scenario's settings are missing, not numbers, or outside what a stream job can run.") from None
    values = {
        "JOB_NAME": job_name(job, tenant_id), "SOURCE": spec.source, "BOOTSTRAP": bootstrap,
        "TRANSACTIONS": topics.topic(tenant_id, topics.TRANSACTIONS), "ALERTS_IN": topics.topic(tenant_id, topics.ALERTS_IN),
        "GROUP": f"{settings.event_topic_prefix}-flink-{job.replace('_', '-')}-{tenant_id}",
    } | own
    put = lambda text: PLACEHOLDER.sub(lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), text)  # noqa: E731
    sql = put(spec.template.replace("${COMMON}", COMMON))
    left = PLACEHOLDER.search(sql)
    if left:
        raise JobError(f"The job template has a placeholder nothing fills: {left.group(0)}")
    return sql
