"""python -m app.loadtest run [options]. Writes a JSON and a Markdown report and prints the Markdown."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ..config import ROOT
from .report import as_json, as_markdown
from .runner import Plan, run


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.loadtest")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="Run the load test and write its report")
    r.add_argument("--transactions", type=int, default=20_000)
    r.add_argument("--customers", type=int, default=None, help="default: one for every 50 transactions")
    r.add_argument("--client", default="loadtest", help="the client to load into; created from the full-service template if missing")
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--inline", action="store_true", help="consume in this process instead of waiting for the worker")
    r.add_argument("--api", default=None, help="base URL of the running API, for onboarding and the API load stage")
    r.add_argument("--api-seconds", type=float, default=30.0)
    r.add_argument("--api-concurrency", type=int, default=8)
    r.add_argument("--stall-seconds", type=float, default=120.0)
    r.add_argument("--out", default=str(ROOT / "data" / "loadtest"), help="directory for the reports")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    outcome = run(Plan(transactions=a.transactions, customers=a.customers, tenant=a.client, seed=a.seed, inline=a.inline, api_url=a.api,
                       api_seconds=a.api_seconds, api_concurrency=a.api_concurrency, stall_seconds=a.stall_seconds))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{outcome.started:%Y%m%d-%H%M}-{outcome.plan.run}"
    (out / f"{stem}.json").write_text(as_json(outcome))
    md = as_markdown(outcome)
    (out / f"{stem}.md").write_text(md)
    print(md)
    print(f"Reports: {out / stem}.json and .md")


if __name__ == "__main__":
    main()
