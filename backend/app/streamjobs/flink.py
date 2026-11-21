"""What the Flink cluster says about a client's stream jobs, over its REST API. Read-only."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime

from ..config import settings
from .jobs import JOBS, job_name

ACTIVE = {"RUNNING", "CREATED", "INITIALIZING", "RESTARTING", "RECONCILING"}


class FlinkError(RuntimeError):
    pass


def overview(url: str | None = None, timeout: float = 2.0) -> list[dict]:
    """Every job the cluster knows, newest first."""
    base = (url or settings.flink_url).rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise FlinkError("FLINK_URL must be the cluster's REST address, for example http://flink-jobmanager:8081")
    try:
        with urllib.request.urlopen(f"{base}/jobs/overview", timeout=timeout) as r:  # noqa: S310  scheme checked above
            jobs = json.load(r).get("jobs", [])
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise FlinkError("The Flink cluster did not answer.") from e
    return sorted(jobs, key=lambda j: j.get("start-time", 0), reverse=True)


def status(tenant_id: str, scenarios_on: dict[str, bool] | None = None) -> dict:
    """One client's stream jobs. A cluster that cannot be reached is reported, not raised: the page still loads.

    `scenarios_on` says which application scenarios are switched on, so a rule running in both places is called out.
    """
    if not settings.flink_url:
        return {"engine": "none", "connected": False, "jobs": [],
                "note": "No stream processor is connected. Scenarios run in the application's stream consumer."}
    try:
        seen = overview()
    except FlinkError as e:
        return {"engine": "flink", "connected": False, "note": str(e), "jobs": []}
    out = []
    for spec in JOBS.values():
        runs = [j for j in seen if j.get("name") == job_name(spec.name, tenant_id)]
        live = next((j for j in runs if j.get("state") in ACTIVE), None)
        j = live or (runs[0] if runs else None)
        running = bool(j and j.get("state") == "RUNNING")
        out.append({
            "job": spec.name, "title": spec.title, "scenario": spec.scenario, "source": spec.source,
            "state": j["state"] if j else "NOT SUBMITTED",
            "since": datetime.fromtimestamp(j["start-time"] / 1000, UTC).isoformat() if j and j.get("start-time", 0) > 0 else None,
            # Both engines raising the same rule is not wrong (the alerts land in one case) but it is double work.
            "also_in_application": running and bool((scenarios_on or {}).get(spec.scenario, False)),
        })
    return {"engine": "flink", "connected": True, "note": "Apache Flink, one job per client and rule.", "jobs": out}
