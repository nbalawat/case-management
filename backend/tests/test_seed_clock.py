"""The demo comes out the same whenever it is seeded: at midnight, in the afternoon, today or a month from now.

Its history ends at a fixed time of day (seed.demo_clock), so only the dates move. Before that, seeding in the first
hours after midnight UTC pushed the last day's events into the future and changed which cases closed or escalated,
and planted wires that crossed a month end raised a different number of monthly alerts.
"""
from collections import Counter
from datetime import datetime, timedelta

import pytest
import time_machine
from sqlalchemy import select

from app.domain import models as m
from app.seed import DEMO_HOUR, PROFILES, demo_clock, seed_tenant
from app.tenants import registry

CLOCKS = ["2026-11-22 00:20", "2026-11-21 15:00", "2026-12-31 23:30", "2027-03-01 21:59"]


def test_the_history_ends_at_the_same_time_of_day_and_never_in_the_future():
    for at in CLOCKS + ["2026-11-22 22:01", "2026-03-01 06:00"]:
        t = datetime.fromisoformat(at)
        end = demo_clock(t)
        assert end <= t and t - end < timedelta(days=1) and (end.hour, end.minute, end.second) == (DEMO_HOUR, 0, 0)


def _outcomes(s, end):
    """Everything a demo shows that could depend on the clock, with times as offsets from the end of its history."""
    cases = s.scalars(select(m.Case).order_by(m.Case.created, m.Case.id)).all()
    return {
        "cases": Counter((c.case_type, c.state, c.disposition, c.closed is not None, c.priority) for c in cases),
        "created": sorted(end - c.created for c in cases),
        "alerts": Counter((a.scenario, a.status) for a in s.scalars(select(m.Alert))),
        "filings": Counter((f.kind, f.status) for f in s.scalars(select(m.Filing))),
        "transactions": sorted(end - t.ts for t in s.scalars(select(m.Transaction))),
        "future": [t.id for t in s.scalars(select(m.Transaction).where(m.Transaction.ts > end))],
    }


@pytest.mark.parametrize("profile", ["starter", "small"])
def test_seeding_under_different_clocks_gives_the_same_demo(db, profile):
    seen = []
    for at in CLOCKS:
        fresh = type(db)()  # a database of its own each time: ids are drawn per client, so the client is the same one
        registry.swap({})
        with time_machine.travel(datetime.fromisoformat(at + "Z"), tick=False):
            fresh.onboard("clockwork")
            with fresh.tenant("clockwork") as s:
                seed_tenant(s, PROFILES[profile])
                s.flush()
                seen.append(_outcomes(s, demo_clock()))
    assert not seen[0]["future"]
    for at, other in zip(CLOCKS[1:], seen[1:]):
        assert other == seen[0], f"seeded at {at} the demo differs from seeding at {CLOCKS[0]}: {_diff(seen[0], other)}"


def _diff(a, b):
    out = []
    for k in a:
        if a[k] != b[k]:
            if isinstance(a[k], Counter):
                out.append(f"{k}: only first {dict(a[k] - b[k])}, only second {dict(b[k] - a[k])}")
            else:
                sa, sb = Counter(a[k]), Counter(b[k])
                out.append(f"{k}: only first {sorted(sa - sb)[:8]}, only second {sorted(sb - sa)[:8]}")
    return "; ".join(out)
