"""python -m app.ask.evals [--only id,id] [--offline-only] [--out results.json]

Asks the evaluation set against the configured database (run `make seed` first) and prints a scorecard. The model
is whatever each client's policy resolves to: the offline planner with no key, a model once ANTHROPIC_API_KEY or
OPEN_MODEL_URL is set in the repository's .env. Every question is recorded in each client's question log.
"""
from __future__ import annotations

import argparse
import json
import sys

from ...db import session_scope
from ...domain.personas import get_persona
from ...tenants.onboarding import hydrate
from .runner import run, scorecard


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ask.evals", description="Score Ask on hard questions, per persona")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--offline-only", action="store_true", help="only the questions the offline planner should answer")
    ap.add_argument("--out", help="write every answer and the scorecard to this JSON file")
    a = ap.parse_args(argv)
    with session_scope(platform=True) as p:
        hydrate(p)
    results = run(session_scope, get_persona, only=set(a.only.split(",")) if a.only else None, offline_only=a.offline_only)
    card = scorecard(results)
    for r in results:
        mark = "PASS" if r.passed else ("UNSAFE" if not r.safe else "fail")
        print(f"{mark:6} {r.id:32} {r.tenant}:{r.person:8} {r.provider:28} {'; '.join(r.problems)}")
    o = card["overall"]
    print(f"\n{o['answers']} answers by {', '.join(card['providers'])}: passed {o['passed']}%, safe {o['safe']}%, grounded {o['grounded']}%, "
          f"correct {o['correct']}%, covered {o['covered']}%. Tokens: {card['tokens']:,}")
    for tag, b in card["by_tag"].items():
        print(f"  {tag:10} {b['answers']:3} answers, passed {b['passed']}%, correct {b['correct']}%")
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"scorecard": card, "results": [r.as_dict() for r in results]}, f, indent=2, default=str)
    return 0 if o["safe"] == 100.0 else 1  # unsafe answers fail the run, whatever else does


if __name__ == "__main__":
    sys.exit(main())
