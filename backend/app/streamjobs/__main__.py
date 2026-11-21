"""Print a stream job's SQL for one client, with that client's approved thresholds.

    python -m app.streamjobs structuring acme-bank > acme-bank.sql
    python -m app.streamjobs structuring acme-bank --bootstrap kafka:9092
    python -m app.streamjobs --list

The client's thresholds come from its database. With --defaults the scenario's shipped settings are used and no
database is opened.
"""
from __future__ import annotations

import argparse
import sys

from .jobs import JOBS, JobError, in_force, render


def _params(job: str, tenant_id: str) -> dict:
    from ..backbone.worker import reload_clients
    from ..db import ensure_schema, session_scope

    from ..tenants import registry

    ensure_schema()
    reload_clients()
    if tenant_id not in {rt.id for rt in registry.all_runtimes()}:
        raise JobError(f"There is no client called '{tenant_id}'.")
    with session_scope(tenant_id) as s:
        params, on = in_force(s, job)
    if not on:
        print(f"Note: '{JOBS[job].scenario}' is switched off in the application for {tenant_id}, so only this job will raise it.", file=sys.stderr)
    else:
        print(f"Note: '{JOBS[job].scenario}' is also switched on in the application for {tenant_id}. Both will raise it "
              "until it is switched off there, under Detection.", file=sys.stderr)
    return params


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.streamjobs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", nargs="?")
    ap.add_argument("client", nargs="?")
    ap.add_argument("--bootstrap", help="Kafka brokers as the cluster reaches them, host:port. Default: from EVENT_BUS_URL.")
    ap.add_argument("--defaults", action="store_true", help="Use the scenario's shipped thresholds, without opening the client's database.")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for j in JOBS.values():
            print(f"{j.name}\t{j.title}")
        return 0
    if not a.job or not a.client:
        ap.print_usage(sys.stderr)
        return 2
    try:
        if a.job not in JOBS:
            raise JobError(f"No stream job called '{a.job}'. There is: {', '.join(JOBS)}.")
        params = None if a.defaults else _params(a.job, a.client)
        sys.stdout.write(render(a.job, a.client, params, a.bootstrap))
    except JobError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  an unknown client, an unreachable database: say so, without a traceback
        print(f"Could not read that client's settings: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
