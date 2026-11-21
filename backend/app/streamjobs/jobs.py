"""The catalogue of stream jobs and how one is rendered for a client."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..backbone import topics
from ..config import settings
from ..detection.scenarios import REGISTRY

HERE = Path(__file__).parent
TENANT = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
BOOTSTRAP = re.compile(r"^[A-Za-z0-9.\-]+:\d{1,5}(,[A-Za-z0-9.\-]+:\d{1,5})*$")
PLACEHOLDER = re.compile(r"\$\{([A-Z_]+)\}")


class JobError(ValueError):
    pass


@dataclass(frozen=True)
class Job:
    name: str
    scenario: str  # the application scenario this job is the streaming form of
    title: str
    source: str  # what its alerts carry as their source

    @property
    def template(self) -> str:
        return (HERE / f"{self.name}.sql").read_text()


JOBS = {j.name: j for j in (Job("structuring", "structuring", "Cash structuring below reporting threshold", "flink-structuring"),)}


def job_name(job: str, tenant_id: str) -> str:
    """What the job is called on the cluster. Status is read back by this name."""
    return f"{settings.event_topic_prefix} {job} {tenant_id}"


def bootstrap_of(url: str) -> str:
    """kafka://host:9092,host2:9092 as the host list a Kafka client wants."""
    if not url.startswith("kafka://"):
        raise JobError("A stream job reads Kafka. Set EVENT_BUS_URL to kafka://host:port, or pass the brokers.")
    return url.removeprefix("kafka://").split("?")[0].strip("/")


def _number(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


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
    p = REGISTRY[spec.scenario]["params"] | (params or {})
    try:
        lo, hi, count, days = float(p["min"]), float(p["max"]), int(p["count"]), int(p["days"])
    except (KeyError, TypeError, ValueError):
        raise JobError("The scenario's settings are not numbers.") from None
    if not (0 < lo <= hi and 2 <= count <= 50 and 1 <= days <= 90):
        raise JobError("The scenario's settings are outside what a stream job can run.")
    values = {
        "JOB_NAME": job_name(job, tenant_id), "SOURCE": spec.source, "BOOTSTRAP": bootstrap,
        "TRANSACTIONS": topics.topic(tenant_id, topics.TRANSACTIONS), "ALERTS_IN": topics.topic(tenant_id, topics.ALERTS_IN),
        "GROUP": f"{settings.event_topic_prefix}-flink-{job}-{tenant_id}",
        "MIN": _number(lo), "MAX": _number(hi), "MIN_TEXT": f"{lo:,.0f}", "COUNT": str(count), "DAYS": str(days),
    }
    sql = PLACEHOLDER.sub(lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), spec.template)
    left = PLACEHOLDER.search(sql)
    if left:
        raise JobError(f"The job template has a placeholder nothing fills: {left.group(0)}")
    return sql
