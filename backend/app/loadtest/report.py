"""The report of one run: what was measured, on what, and a projection that is labelled as one."""
from __future__ import annotations

import json
import math

from .runner import Outcome

# The design target, as rates a consumer fleet must sustain on average. Peaks run several times higher.
TARGETS = (("1 billion a year", 1e9 / (365 * 86_400)), ("1 billion a month", 1e9 / (30 * 86_400)), ("1 billion a day", 1e9 / 86_400))


def projection(o: Outcome) -> dict | None:
    stream = o.stage("stream")
    if stream is None or not stream.items:
        return None
    rate = stream.detail.get("busy_per_second") or stream.rate
    consumers = stream.detail.get("stream_consumers") or 1
    rate = rate / consumers  # per consumer process, which is what the fleet is counted in
    return {
        "measured_per_second": rate,
        "consumers_measured": consumers,
        "per_day_one_consumer": round(rate * 86_400),
        "targets": [{"target": label, "average_per_second": round(need, 1), "consumers_if_linear": max(1, math.ceil(need / rate))}
                    for label, need in TARGETS],
        "assumes": ("Throughput grows in proportion to consumer processes, each with its own partitions and enough database "
                    f"capacity behind it. This run measured {consumers} consumer process{'es' if consumers > 1 else ''} on one machine; "
                    "a fleet beyond that was not measured."),
    }


def as_json(o: Outcome) -> str:
    return json.dumps({"run": o.plan.run, "started": o.started.isoformat(timespec="seconds"), "tenant": o.plan.tenant,
                       "plan": {"transactions": o.plan.transactions, "customers": o.plan.population.customers, "inline": o.plan.inline,
                                "seed": o.plan.seed},
                       "environment": o.environment, "stages": [s.as_dict() for s in o.stages], "projection": projection(o)}, indent=2)


def _n(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:,.1f}" if v < 1000 else f"{v:,.0f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def as_markdown(o: Outcome) -> str:
    e, p = o.environment, o.plan
    lines = [f"# Load test {p.run}", "",
             f"Started {o.started:%Y-%m-%d %H:%M} UTC. Client `{p.tenant}`, {p.transactions:,} transactions over {p.population.customers:,} customers"
             f" ({'consumers in the test process' if p.inline else 'consumed by the running worker'}).", "",
             f"Measured on {e['system']} ({e['machine']}, {e['cpus']} CPUs, {_n(e['memory_gb'])} GB as the process sees it), Python {e['python']},"
             f" database {e['database']}, bus {e['bus']}, lake {'yes' if e['lake'] else 'no'}.", "",
             "## What was measured", "", "| Stage | Items | Seconds | Per second | Finished |", "|---|---:|---:|---:|---|"]
    for s in o.stages:
        lines.append(f"| {s.name} | {s.items:,} | {s.seconds:,.2f} | {_n(round(s.rate, 1))} | {'yes' if s.complete else 'no'} |")
    pub, stream, lake = o.stage("publish"), o.stage("stream"), o.stage("lake")
    lines.append("")
    if pub:
        lines.append(f"- Publishing: {_n(pub.detail['send_per_second'])} messages a second while sending, generation excluded"
                     f" ({pub.detail['generate_seconds']} s generating).")
    if stream:
        d = stream.detail
        lines.append(f"- Stream consumer: {_n(d.get('busy_per_second'))} transactions a second from first load to last, {_n(d.get('loaded'))} loaded,"
                     f" {_n(d.get('duplicates'))} duplicates, {_n(d.get('rejected'))} rejected, {_n(d.get('alerts'))} alerts raised."
                     f" Each includes {d['includes']}.")
        if d.get("stream_consumers") or d.get("partitions"):
            lines.append(f"- Shape: {_n(d.get('stream_consumers'))} stream consumer process(es) in the group, {_n(d.get('partitions'))} partition(s) on the topic.")
        if not stream.complete:
            lines.append(f"- The stream stalled for {d['stalled_after_seconds']} s at {stream.items:,} of {d['target']:,}; the numbers above cover what was done.")
        tl = d.get("timeline") or []
        if len(tl) >= 4:
            half = len(tl) // 2
            def rate(a, b):
                return (b[1] - a[1]) / (b[0] - a[0]) if b[0] > a[0] else 0
            lines.append(f"- First half {rate(tl[0], tl[half]):,.0f}/s, second half {rate(tl[half], tl[-1]):,.0f}/s"
                         " (a fall shows the cost of growing tables).")
    if lake and lake.detail.get("skipped"):
        lines.append(f"- Lake: not measured, because {lake.detail['skipped']}.")
    elif lake and not lake.complete:
        lines.append(f"- Lake: stopped moving at {lake.items:,} of {lake.detail['target']:,} new rows; {_n(lake.detail.get('rows_in_lake'))} rows in the table now.")
    elif lake:
        lines.append(f"- Lake: every transaction in Iceberg {lake.seconds:,.1f} s after publishing began; {_n(lake.detail.get('rows_in_lake'))} rows in the table now.")
    lines.append(f"- Operational store now holds {_n(e.get('rows_in_store'))} transactions for this client.")
    q = o.stage("lake queries")
    if q:
        lines += ["", f"## Lake queries ({q.detail['engine']}, median of 3)", "", "| Query | Seconds | Rows scanned | Files |", "|---|---:|---:|---:|"]
        lines += [f"| {r['query']} | {r['median_seconds']} | {_n(r['rows_scanned'])} | {_n(r['files'])} |" for r in q.detail["queries"]]
    a = o.stage("api")
    if a:
        d = a.detail
        lines += ["", f"## API under load ({d['concurrency']} concurrent callers, {a.seconds:.0f} s)", "",
                  f"{a.items:,} requests, {a.rate:,.1f} a second, p50 {d['p50_ms']} ms, p95 {d['p95_ms']} ms, p99 {d['p99_ms']} ms, {d['errors']} errors"
                  f" (status codes {d['status_codes']}).", "", "| Page | Requests | p50 ms | p95 ms | p99 ms |", "|---|---:|---:|---:|---:|"]
        lines += [f"| `{r['path']}` | {r['requests']:,} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} |" for r in d["pages"]]
    proj = projection(o)
    if proj:
        lines += ["", "## Projection (arithmetic, not measured)", "",
                  f"At the measured {_n(proj['measured_per_second'])} a second per consumer process ({proj['consumers_measured']} measured together),"
                  f" one consumer process handles about {proj['per_day_one_consumer']:,} transactions a day.", "",
                  "| Design target | Average per second | Consumer processes, if scaling were linear |", "|---|---:|---:|"]
        lines += [f"| {t['target']} | {t['average_per_second']:,} | {t['consumers_if_linear']:,} |" for t in proj["targets"]]
        lines += ["", f"Assumes: {proj['assumes']}"]
    return "\n".join(lines) + "\n"
