"""Usage metering: every figure is a count of the client's own records, so it is checked here against those records."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from sqlalchemy import func, select

import app.db as appdb
from app.domain import models as m
from app.tenants import metering
from test_client_api import OP, h

WIDE = {"start": "2000-01-01", "end": "2000-12-31"}


def _report(tc, **params):
    r = tc.get("/api/platform/usage", headers=OP, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_each_clients_totals_are_the_counts_of_its_own_records(client):
    today = date.today()
    start = today - timedelta(days=365)
    rep = _report(client, start=start.isoformat(), end=today.isoformat())
    assert {c["tenant"] for c in rep["clients"]} >= {"meridian", "kestrel", "alder"}
    for c in rep["clients"]:
        with appdb.session_scope(c["tenant"]) as s:
            lo = start.isoformat()
            cases = s.scalar(select(func.count()).select_from(m.Case).where(func.date(m.Case.created) >= lo))
            alerts = s.scalar(select(func.count()).select_from(m.Alert).where(func.date(m.Alert.created) >= lo))
            people = s.scalar(select(func.count(func.distinct(m.CaseEvent.actor))).where(m.CaseEvent.actor_kind == "human", func.date(m.CaseEvent.ts) >= lo))
        assert c["totals"]["cases_opened"] == cases and c["totals"]["alerts"] == alerts and c["totals"]["active_people"] == people, c["tenant"]
        for mid in ("cases_opened", "alerts", "documents", "transactions"):
            assert sum(d.get(mid, 0) for d in c["days"]) == c["totals"][mid], (c["tenant"], mid)


def test_a_new_case_is_metered_to_its_own_client_today_and_nowhere_else(client):
    today = date.today().isoformat()
    before = {c["tenant"]: c["totals"]["cases_opened"] for c in _report(client, start=today, end=today)["clients"]}
    from app.cases.case_types import load_case_types
    from app.cases.service import create_case

    with appdb.session_scope("alder") as s:
        party = s.scalars(select(m.Party.id)).first()
        create_case(s, load_case_types("alder")[next(iter(load_case_types("alder")))], party, "low")
    after = {c["tenant"]: c["totals"]["cases_opened"] for c in _report(client, start=today, end=today)["clients"]}
    assert after["alder"] == before["alder"] + 1 and all(after[t] == before[t] for t in before if t != "alder")


def test_a_period_with_nothing_in_it_meters_zero(client):
    rep = _report(client, **WIDE)
    assert all(not c["days"] and not any(c["totals"].values()) for c in rep["clients"])
    assert {mm["id"] for mm in rep["meters"]} == {mm.id for mm in metering.METERS}


def test_one_client_can_be_asked_for_alone(client):
    rep = _report(client, client="kestrel")
    assert [c["tenant"] for c in rep["clients"]] == ["kestrel"]


def test_the_billing_csv_holds_the_same_figures(client):
    today = date.today()
    start = (today - timedelta(days=90)).isoformat()
    rep = _report(client, start=start, end=today.isoformat())
    r = client.get("/api/platform/usage.csv", headers=OP, params={"start": start, "end": today.isoformat()})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    for c in rep["clients"]:
        got = sum(int(x["value"]) for x in rows if x["client"] == c["tenant"] and x["meter"] == "cases_opened")
        assert got == c["totals"]["cases_opened"]


def test_only_the_provider_reads_usage_and_bad_periods_are_refused(client):
    assert client.get("/api/platform/usage", headers={"X-Operator": "priya"}).status_code == 403
    assert client.get("/api/platform/usage", headers=h("meridian", "omar")).status_code == 403
    assert client.get("/api/platform/usage.csv", headers=h("meridian", "omar")).status_code == 403
    assert client.get("/api/platform/usage", headers=OP, params={"start": "yesterday"}).status_code == 422
    assert client.get("/api/platform/usage", headers=OP, params={"start": "2026-02-01", "end": "2026-01-01"}).status_code == 422
    assert client.get("/api/platform/usage", headers=OP, params={"start": "2020-01-01", "end": "2026-01-01"}).status_code == 422
