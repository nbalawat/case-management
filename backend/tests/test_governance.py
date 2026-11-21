"""Management reporting and governance: the period report and its exports, the change register, access recertification, audit search."""
import csv
import io
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import reference
from app.cases.case_types import load_case_types
from app.cases.service import create_case, record
from app.detection import tuning
from app.domain.models import AccessReview, Account, Alert, Case, CaseEvent, Dispute, Filing, Hold, Party, now
from app.governance import audit, recert, register
from app.governance.audit import AuditError
from app.governance.recert import RecertError
from app.reporting import mi
from app.reporting.mi import MiError
from app.sanctions import lists
from app.tenancy import tenant_of
from app.visibility import bind_viewer
from support import PERSONAS, T
from test_client_api import CLIENTS, EVERYONE, _permissions, h

P = PERSONAS
MAYA, HUGO, OMAR, IVAN, ANA, AUDRA, TARA, FRAN = (P[k] for k in ("maya", "hugo", "omar", "ivan", "ana", "audra", "tara", "fran"))
THIS = now().strftime("%Y-%m")
START = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
MID = START + (now() - START) / 2  # a moment in this month that has already passed, whatever day the tests run
EARLIER = START + (MID - START) / 2  # and one before it, still in this month


def case(s, pid, kind="tm_alert_investigation", created=None, closed=None, disposition=None, owner=None, due=None, recommendation=None):
    if s.scalars(select(Party).where(Party.id == pid)).first() is None:
        s.add_all([Party(id=pid, name=f"Customer {pid}"), Account(id=f"A-{pid}", party_id=pid)])
        s.flush()
    c = create_case(s, load_case_types(tenant_of(s))[kind], pid, "medium", created=created or MID, actor="test")
    c.owner, c.agent_recommendation = owner, recommendation
    if due:
        c.sla_due = due
    if closed:
        c.closed, c.disposition, c.state = closed, disposition, "closed"
    s.flush()
    return c


# --- management information -----------------------------------------------------------------------------------------------------
def test_a_period_is_a_month_and_defaults_to_this_one():
    start, end, label = mi.month_of("2026-02", today=datetime(2026, 6, 15))
    assert (start, end, label) == (datetime(2026, 2, 1), datetime(2026, 3, 1), "2026-02")
    assert mi.month_of("2025-12", today=datetime(2026, 6, 15))[1] == datetime(2026, 1, 1)
    assert mi.month_of(None, today=datetime(2026, 6, 15, 9))[0] == datetime(2026, 6, 1)
    for bad, why in (("June", "written YYYY-MM"), ("2026-13", "written YYYY-MM"), ("2026-07", "has not started")):
        with pytest.raises(MiError, match=why):
            mi.month_of(bad, today=datetime(2026, 6, 15))


def test_an_empty_client_reports_zeros_and_no_rates(s):
    r = mi.report(s)
    assert r["period"] == THIS and r["partial"] and r["periods"][0] == THIS and len(r["periods"]) == 12 and len(r["trend"]) == 6
    assert r["headline"] == {"alerts": 0, "opened": 0, "closed": 0, "backlog": 0, "median_days": None, "in_time": None, "productive": 0, "filings_sent": 0,
                             "filings_late": 0, "stage_breaches": 0}
    assert r["case_types"] == [] and r["people"] == [] and r["quality"]["pass_rate"] is None and r["agents"]["agreement"] is None
    assert [a["count"] for a in r["ageing"]] == [0, 0, 0, 0]


def test_the_headline_counts_the_period_and_the_backlog_at_its_end(s):
    last_month = (MID.replace(day=1) - timedelta(days=10))
    case(s, "A", created=last_month)  # opened before, still open: backlog only
    case(s, "B", created=last_month, closed=MID, disposition="sar_filed", owner="ivan", due=MID + timedelta(days=5), recommendation="escalate")
    case(s, "C", created=EARLIER, closed=MID, disposition="no_sar", owner="ivan", due=EARLIER, recommendation="escalate")
    case(s, "D", created=MID)
    case(s, "E", created=last_month, closed=last_month + timedelta(days=1), disposition="no_sar")  # another month's work entirely
    r = mi.report(s)
    hd = r["headline"]
    assert (hd["opened"], hd["closed"], hd["backlog"], hd["productive"]) == (2, 2, 2, 1)
    assert hd["in_time"] == 0.5  # one of the two closed after its deadline
    (row,) = r["case_types"]
    assert (row["opened"], row["closed"], row["backlog"], row["productive"], row["in_time"]) == (2, 2, 2, 1, 0.5)
    assert r["agents"] == {"advised": 2, "agreed": 1, "agreement": 0.5, "auto_closed": 0}
    assert {(o["disposition"], o["count"]) for o in r["outcomes"]} == {("sar_filed", 1), ("no_sar", 1)}
    before = mi.report(s, last_month.strftime("%Y-%m"))
    assert not before["partial"] and (before["headline"]["opened"], before["headline"]["closed"], before["headline"]["backlog"]) == (3, 1, 2)
    assert [t["month"] for t in r["trend"]][-2:] == [before["period"], THIS] and r["trend"][-2]["backlog"] == 2 and r["trend"][-1]["opened"] == 2


def test_open_cases_are_aged_and_those_past_their_deadline_counted(s):
    for pid, days, late in (("A", 2, False), ("B", 20, True), ("C", 45, False), ("D", 200, True)):
        case(s, pid, created=now() - timedelta(days=days), due=now() + timedelta(days=-1 if late else 30))
    assert [(a["count"], a["overdue"]) for a in mi.report(s)["ageing"]] == [(1, 0), (1, 1), (1, 0), (1, 1)]


def test_people_are_credited_with_what_they_closed_and_qa_reviews_are_kept_apart(s):
    a = case(s, "A", created=MID - timedelta(days=4), closed=MID, disposition="sar_filed", owner="maya")
    record(s, a.id, "ivan", "human", "case.state_changed", {"to": "closed"}, ts=MID)  # Ivan closed it, whoever owns it now
    case(s, "B", created=MID - timedelta(days=2), closed=MID, disposition="no_sar", owner="ivan")
    case(s, "C", created=MID, owner="ivan")
    review = case(s, "A", kind="qa_review", created=MID, closed=MID, disposition="qa_passed")
    review.data = {"closed_by": "ivan"}
    s.flush()
    r = mi.report(s)
    (ivan,) = r["people"]
    assert ivan == {"id": "ivan", "name": "Ivan Petrov", "title": "L2 investigator", "closed": 2, "median_days": 3.0, "productive": 1, "open_now": 1,
                    "qa_reviewed": 1, "qa_passed": 1}
    assert r["quality"] == {"reviewed": 1, "passed": 1, "failed": 0, "pass_rate": 1.0}
    assert r["headline"]["closed"] == 2 and all(t["case_type"] != "qa_review" for t in r["case_types"])


def test_scenarios_filings_breaches_fraud_and_sanctions_are_reported(s):
    c = case(s, "A", created=MID - timedelta(days=1), closed=MID, disposition="sar_filed")
    s.add_all([
        Alert(id="alr-1", dedupe_key="k1", scenario="structuring", scenario_version="1", party_id="A", score=0.8, explanation="x", case_id=c.id, status="in_case", created=MID),
        Alert(id="alr-2", dedupe_key="k2", scenario="structuring", scenario_version="1", party_id="A", score=0.4, explanation="x", status="auto_closed", created=MID),
        Filing(id="sar-1", case_id=c.id, kind="SAR", filed_by="maya", sha256="0" * 64, status="acknowledged", submitted_at=MID, due=MID - timedelta(days=1)),
        Filing(id="sar-2", case_id=c.id, kind="SAR", filed_by="maya", sha256="0" * 64, status="submitted", submitted_at=MID, due=MID + timedelta(days=1)),
        Filing(id="sar-3", case_id=c.id, kind="SAR", filed_by="maya", sha256="0" * 64, status="prepared"),
        Dispute(id="dsp-1", case_id=c.id, party_id="A", amount=900.0, rail="card", reason_code="unauthorised", opened_by="fran", status="closed", closed_at=MID, recovered=600.0, loss=300.0),
        Hold(id="hld-1", case_id=c.id, party_id="A", target="payment", reason_code="sanctions_potential_match", placed_by="sofia", status="blocked", decided_at=MID),
    ])
    record(s, c.id, "timers", "system", "sla.stage_breached", {}, ts=MID)
    s.flush()
    r = mi.report(s)
    assert r["scenarios"] == [{"scenario": "structuring", "title": "Cash structuring below reporting threshold", "alerts": 2, "auto_closed": 1, "waiting": 0,
                               "decided_ever": 1, "productive_ever": 1, "productive_rate": 1.0}]
    assert r["filings"] == [{"kind": "SAR", "sent": 2, "late": 1, "accepted": 1, "rejected": 0}]
    assert (r["headline"]["filings_sent"], r["headline"]["filings_late"], r["headline"]["stage_breaches"], r["headline"]["alerts"]) == (2, 1, 1, 2)
    assert r["breaches"][0]["count"] == 1 and r["fraud"] == {"disputes_closed": 1, "claimed": 900.0, "recovered": 600.0, "loss": 300.0}
    assert r["sanctions"] == {"decided": 1, "released": 0, "blocked": 1, "rejected": 0} and r["agents"]["auto_closed"] == 1


def test_the_report_counts_every_case_whoever_asks(s):
    hidden = case(s, "A", created=MID)
    hidden.restricted = True
    s.flush()
    bind_viewer(s, ANA)  # an analyst who cannot open the restricted case still gets true totals
    assert s.scalars(select(Case)).first() is None and mi.report(s)["headline"]["opened"] == 1


def test_the_exports_carry_the_same_figures(s):
    case(s, "B", created=START - timedelta(days=2), closed=START + timedelta(seconds=1), disposition="sar_filed", owner="ivan")
    r = mi.report(s)
    rows = list(csv.DictReader(io.StringIO(mi.as_csv(r))))
    assert rows[0].keys() == {"period", "section", "measure", "of", "value"} and all(x["period"] == THIS for x in rows)
    get = lambda section, measure, of="": next(x["value"] for x in rows if (x["section"], x["measure"], x["of"]) == (section, measure, of))  # noqa: E731
    assert get("Headline", "closed") == "1" and get("Headline", "in time") == "1.0" and get("People", "closed", "Ivan Petrov") == "1"
    assert get("Trend", "closed", THIS) == "1" and get("Case types", "productive", "Transaction monitoring investigation") == "1"
    pack = mi.as_pack(r, "Meridian")
    assert pack.startswith(f"# Financial crime operations: {THIS}") and "(the month is not over)" in pack
    assert "- 0 alerts raised and 0 cases opened; 1 case closed, leaving 0 open." in pack
    assert "| Transaction monitoring investigation | 0 | 1 | 0 | 1 | 2.0 | 100% |" in pack


# --- change register ------------------------------------------------------------------------------------------------------------
def test_the_register_gathers_changes_from_every_kind_of_configuration(s):
    assert [e["area"] for e in register.entries(s)] == ["Access"]  # the policy the client was onboarded with
    v = tuning.propose(s, "structuring", {"count": 4}, True, "Three deposits a week catches ordinary cash businesses.", OMAR)
    tuning.decide(s, v.id, True, "Agreed after reading the backtest.", MAYA)
    lists.ensure(s)
    reference.replace(s, "alert_hibernation", [{"code": "weak", "label": "Weak first alert", "scenario": "*", "below": 0.5, "days": 30}], OMAR)
    review = recert.start(s, MAYA, "Second quarter review")
    rows = register.entries(s)
    assert {e["area"] for e in rows} == {"Access", "Detection", "Screening", "Reference lists"}
    assert rows == sorted(rows, key=lambda e: e["at"], reverse=True)
    live = next(e for e in rows if e["what"] == "Settings revision 1 put live")
    assert (live["by"], live["second"], live["subject"], live["link"]) == ("omar", "maya", "Cash structuring below reporting threshold", "/detection")
    assert next(e for e in rows if e["what"].startswith("Settings revision 1 proposed"))["second"] is None
    assert any(e["subject"] == "Second quarter review" and e["what"] == f"Recertification started for {len(review.items)} people" for e in rows)
    assert [e["area"] for e in register.entries(s, area="Detection")] == ["Detection", "Detection"]
    assert register.entries(s, since=now() + timedelta(days=1)) == []
    text = register.as_csv(rows)
    assert text.splitlines()[0] == "when,area,subject,change,by,second person,note" and len(text.splitlines()) == len(rows) + 1


def test_one_clients_register_holds_nothing_of_anothers(db):
    db.onboard("one"), db.onboard("two")
    with db.tenant("one") as s:
        v = tuning.propose(s, "structuring", {"count": 4}, True, "Three deposits a week catches ordinary cash businesses.", OMAR)
        recert.start(s, MAYA, "Review for client one")
        s.commit()
    with db.tenant("two") as s:
        assert [e["area"] for e in register.entries(s)] == ["Access"] and recert.view(s, MAYA)["reviews"] == []
        with pytest.raises(RecertError, match="not found"):
            recert.close(s, "acr-none", MAYA)
        assert v.id and mi.report(s)["detection"] == {"changes_live": 0, "lookbacks_open": 0}


# --- access recertification -----------------------------------------------------------------------------------------------------
def test_a_review_snapshots_everyone_with_what_they_hold(s):
    c = case(s, "A", owner="ivan")
    record(s, c.id, "ivan", "human", "case.assigned", {}, ts=now() - timedelta(days=2))
    record(s, c.id, "ana", "human", "case.assigned", {}, ts=now() - timedelta(days=45))
    review = recert.start(s, MAYA, "Second quarter review", 21)
    by = {i["person"]: i for i in review.items}
    assert set(by) == set(P) and review.policy_version == "1.0.0" and review.due - review.started == timedelta(days=21)
    assert by["ivan"]["permissions"] == sorted(IVAN.permissions) and by["ivan"]["queues"] == ["aml-l2"] and by["ivan"]["open_cases"] == 1
    assert by["ivan"]["flags"] == [] and by["ivan"]["last_active"] is not None
    assert by["ana"]["flags"] == ["No case activity in 30 days"] and by["fran"]["flags"] == ["No case activity on record"]
    assert by["audra"]["flags"] == [] and by["audra"]["read_only"]  # read-only: nothing to be dormant in
    assert by["maya"]["flags"][0] == "Proposes detection changes and can approve them (never their own)"
    assert by["omar"]["flags"] == []  # sets access, and neither works cases nor certifies
    assert all(i["decision"] is None for i in review.items)


def test_starting_a_review_needs_the_permission_and_only_one_runs_at_a_time(s):
    with pytest.raises(RecertError, match="needs the 'certify_access' permission"):
        recert.start(s, OMAR, "Second quarter review")
    for name, days, why in (("Q2", 14, "Give the review a name"), ("Second quarter review", 0, "between 1 and 90"), ("Second quarter review", True, "between 1 and 90")):
        with pytest.raises(RecertError, match=why):
            recert.start(s, MAYA, name, days)
    recert.start(s, MAYA, "Second quarter review")
    with pytest.raises(RecertError, match="already open"):
        recert.start(s, HUGO, "Another review")


def test_each_person_is_decided_by_someone_else_with_reasons_where_it_matters(s):
    review = recert.start(s, MAYA, "Second quarter review")
    with pytest.raises(RecertError, match="needs the 'certify_access' permission"):
        recert.decide(s, review.id, "ivan", "keep", "", OMAR)
    with pytest.raises(RecertError, match="Someone else must certify your own access"):
        recert.decide(s, review.id, "maya", "keep", "I still need all of it.", MAYA)
    with pytest.raises(RecertError, match="one of: keep, change, remove"):
        recert.decide(s, review.id, "omar", "fine", "", MAYA)
    with pytest.raises(RecertError, match="Person not found"):
        recert.decide(s, review.id, "nobody", "keep", "", MAYA)
    with pytest.raises(RecertError, match="Say what should change"):
        recert.decide(s, review.id, "omar", "remove", "", MAYA)
    with pytest.raises(RecertError, match="This person is flagged"):
        recert.decide(s, review.id, "ivan", "keep", "", MAYA)  # no case activity on record
    recert.decide(s, review.id, "omar", "keep", "", MAYA)  # unflagged: a plain yes is enough
    recert.decide(s, review.id, "ivan", "keep", "Joined this month; first cases assigned next week.", MAYA)
    recert.decide(s, review.id, "ivan", "change", "Should not be in the L2 queue yet.", MAYA)  # a decision can be revised while the review is open
    by = {i["person"]: i for i in review.items}
    assert (by["ivan"]["decision"], by["ivan"]["decided_by"]) == ("change", "maya") and by["omar"]["decision"] == "keep" and by["ana"]["decision"] is None
    with pytest.raises(RecertError, match="not found"):
        recert.decide(s, "acr-none", "ivan", "keep", "", MAYA)


def test_a_review_closes_only_when_everyone_is_decided(s):
    review = recert.start(s, MAYA, "Second quarter review")
    with pytest.raises(RecertError, match=r"14 people are not yet decided: .*…"):
        recert.close(s, review.id, MAYA)
    for i in review.items:
        recert.decide(s, review.id, i["person"], "keep", "Checked with the line manager.", HUGO if i["person"] == "maya" else MAYA)
    with pytest.raises(RecertError, match="needs the 'certify_access' permission"):
        recert.close(s, review.id, OMAR)
    recert.close(s, review.id, MAYA)
    assert (review.status, review.closed_by) == ("closed", "maya")
    with pytest.raises(RecertError, match="already closed"):
        recert.close(s, review.id, MAYA)
    with pytest.raises(RecertError, match="This review is closed"):
        recert.decide(s, review.id, "ivan", "remove", "Changed my mind entirely.", MAYA)
    assert recert.start(s, HUGO, "Third quarter review").status == "open"  # the next one can now begin


def test_what_a_review_asked_for_stays_outstanding_until_the_policy_shows_it(s, monkeypatch):
    review = recert.start(s, MAYA, "Second quarter review")
    for i in review.items:
        who = HUGO if i["person"] == "maya" else MAYA
        decision = {"ivan": "change", "fran": "remove"}.get(i["person"], "keep")
        recert.decide(s, review.id, i["person"], decision, "Checked with the line manager; see the ticket.", who)
    recert.close(s, review.id, MAYA)
    row = recert.row(s, review)
    assert (row["people"], row["decided"], row["changes"], row["outstanding"]) == (14, 14, 2, 2)
    done = {i["person"]: i["done"] for i in row["items"]}
    assert done["ivan"] is False and done["fran"] is False and done["ana"] is None
    from dataclasses import replace
    later = {k: v for k, v in P.items() if k != "fran"} | {"ivan": replace(IVAN, queues=())}
    monkeypatch.setattr(recert, "policy_of", lambda tenant: type("Policy", (), {"people": later, "version": "1.1.0"})())
    row = recert.row(s, review)
    assert row["outstanding"] == 0 and {i["person"]: i["done"] for i in row["items"]}["fran"] is True
    assert review.policy_version == "1.0.0"  # the snapshot is what was reviewed


def test_the_view_says_what_a_new_review_would_find(s):
    view = recert.view(s, MAYA)
    assert view["reviews"] == [] and view["can_certify"] and view["current"]["people"] == 14 and view["current"]["flagged"] > 0
    assert not recert.view(s, OMAR)["can_certify"] and not recert.view(s, AUDRA)["can_certify"]
    recert.start(s, MAYA, "Second quarter review", at=now() - timedelta(days=30))
    view = recert.view(s, MAYA)
    assert view["current"] is None and view["reviews"][0]["overdue"] and view["reviews"][0]["decided"] == 0


# --- audit search ---------------------------------------------------------------------------------------------------------------
def trail(s):
    a, b = case(s, "A", owner="ivan", created=now() - timedelta(minutes=2)), case(s, "B", kind="fraud_claim", owner="fran", created=now() - timedelta(minutes=1))
    record(s, a.id, "ivan", "human", "case.state_changed", {"to": "investigation", "note": "Pattern unexplained by the business."}, ts=now() - timedelta(days=3))
    record(s, a.id, "investigation-agent", "agent", "agent.recommended", {"recommendation": "escalate"}, ts=now() - timedelta(days=2))
    record(s, b.id, "fran", "human", "case.state_changed", {"to": "customer_contact", "note": "Called the customer."}, ts=now() - timedelta(days=1))
    s.flush()
    return a, b


def test_the_trail_is_searched_by_who_what_where_and_when(s):
    a, b = trail(s)
    everything = audit.search(s)
    assert everything["total"] == 5 and everything["pages"] == 1 and everything["events"][0]["case_id"] == b.id  # newest first; two are the cases being created
    assert [e["actor"] for e in audit.search(s, actor="ivan")["events"]] == ["ivan"]
    assert audit.search(s, actor_kind="agent")["total"] == 1 and audit.search(s, kind="case.state")["total"] == 2 and audit.search(s, kind="case.")["total"] == 4
    assert audit.search(s, case_id=a.id)["total"] == 3
    day = (now() - timedelta(days=1)).date().isoformat()
    assert audit.search(s, date_from=day, date_to=day)["total"] == 1 and audit.search(s, date_to=(now() - timedelta(days=2)).date().isoformat())["total"] == 2
    assert [e["case_id"] for e in audit.search(s, text="UNEXPLAINED")["events"]] == [a.id]
    assert audit.search(s, text="customer", kind="case.state_changed")["total"] == 1 and audit.search(s, text="nothing like this")["total"] == 0
    for bad, why in (({"date_from": "yesterday"}, "first date is not a date"), ({"date_to": "x"}, "last date is not a date"), ({"actor_kind": "robot"}, "one of: human, agent, system")):
        with pytest.raises(AuditError, match=why):
            audit.search(s, **bad)


def test_results_come_a_page_at_a_time(s):
    c = case(s, "A")
    for n in range(7):
        record(s, c.id, "ivan", "human", "task.completed", {"n": n}, ts=now() - timedelta(minutes=n))
    s.flush()
    first, last = audit.search(s, kind="task", limit=3), audit.search(s, kind="task", limit=3, page=3)
    assert (first["total"], first["pages"], len(first["events"]), len(last["events"])) == (7, 3, 3, 1)
    assert [e["payload"]["n"] for e in first["events"]] == [0, 1, 2] and last["events"][0]["payload"]["n"] == 6
    assert len(audit.search(s, text="n", kind="task", limit=3, page=2)["events"]) == 3


def test_a_search_never_shows_events_on_cases_the_asker_cannot_see(s):
    a, b = trail(s)
    a.restricted = True
    s.flush()
    bind_viewer(s, TARA)  # sees the team, but not restricted matters
    assert {e["case_id"] for e in audit.search(s)["events"]} == {b.id} and audit.search(s, case_id=a.id)["total"] == 0
    assert audit.search(s, text="unexplained")["total"] == 0 and {x["actor"] for x in audit.facets(s)["actors"]} == {"fran", "test"}
    assert a.id not in audit.as_csv(s)
    bind_viewer(s, AUDRA)
    assert audit.search(s)["total"] == 5 and {x["kind"] for x in audit.facets(s)["kinds"]} == {"case.created", "case.state_changed", "agent.recommended"}
    lines = audit.as_csv(s, actor="ivan").splitlines()
    assert lines[0] == "when,case,who,kind of actor,event,detail" and len(lines) == 2 and "Pattern unexplained" in lines[1]


def test_one_clients_trail_is_invisible_to_another(db):
    db.onboard("one"), db.onboard("two")
    with db.tenant("one") as s:
        trail(s)
        s.commit()
    with db.tenant("two") as s:
        assert audit.search(s)["total"] == 0 and audit.facets(s) == {"kinds": [], "actors": []} and s.scalars(select(CaseEvent)).first() is None


# --- over HTTP, on the demo -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tenant,who", EVERYONE)
def test_management_views_need_the_team_permission(client, tenant, who):
    allowed = "view_team" in _permissions(tenant, who)
    for url in ("/api/mi", "/api/mi/export.csv", "/api/mi/pack.md", "/api/governance", "/api/governance/register.csv", "/api/audit", "/api/audit/export.csv"):
        assert (client.get(url, headers=h(tenant, who)).status_code == 200) == allowed, (who, tenant, url)
    start = client.post("/api/governance/access-reviews", headers=h(tenant, who), json={"name": "x"})
    perms = _permissions(tenant, who)
    assert start.status_code == (409 if "certify_access" in perms else 403)


def test_the_demo_month_reads_as_a_real_operation(client):
    r = client.get("/api/mi", headers=h("meridian", "tara")).json()
    hd = r["headline"]
    assert hd["opened"] > 10 and hd["closed"] > 5 and hd["backlog"] > 10 and hd["filings_sent"] > 0 and 0 < hd["in_time"] <= 1
    assert len(r["case_types"]) == 5 and len(r["people"]) >= 6 and sum(a["count"] for a in r["ageing"]) >= hd["backlog"] - hd["opened"]
    assert r["fraud"]["disputes_closed"] == 2 and r["sanctions"]["blocked"] >= 1 and r["detection"]["lookbacks_open"] == 1
    assert all(sum(t[k] for t in r["trend"]) > 0 for k in ("alerts", "opened", "closed", "filings"))
    last = client.get(f"/api/mi?period={r['periods'][1]}", headers=h("meridian", "tara")).json()
    assert not last["partial"] and last["period"] == r["periods"][1] and last["headline"]["closed"] > 0
    assert client.get("/api/mi?period=soon", headers=h("meridian", "tara")).status_code == 400
    other = client.get("/api/mi", headers=h("kestrel", "joy" if "view_team" in _permissions("kestrel", "joy") else next(p for t, p in EVERYONE if t == "kestrel" and "view_team" in _permissions(t, p)))).json()
    assert other["headline"]["opened"] != hd["opened"] or other["headline"]["backlog"] != hd["backlog"]


def test_the_exports_download_as_files(client):
    hd = h("meridian", "audra")
    sheet = client.get("/api/mi/export.csv", headers=hd)
    assert sheet.headers["content-type"].startswith("text/csv") and f'filename="management-report-meridian-{THIS}.csv"' in sheet.headers["content-disposition"]
    assert sheet.text.startswith("period,section,measure,of,value") and len(sheet.text.splitlines()) > 80
    pack = client.get("/api/mi/pack.md", headers=hd)
    assert pack.headers["content-type"].startswith("text/markdown") and "Meridian Retail Bank." in pack.text and "## Month by month" in pack.text
    reg = client.get("/api/governance/register.csv?area=Access", headers=hd)
    assert reg.status_code == 200 and all(line.split(",")[1] == "Access" for line in reg.text.splitlines()[1:])
    trail_ = client.get("/api/audit/export.csv?kind=case.restricted", headers=hd)
    assert trail_.status_code == 200 and "case.restricted" in trail_.text
    assert client.get("/api/governance?area=Nothing", headers=hd).status_code == 400 and client.get("/api/governance?since=then", headers=hd).status_code == 400
    assert client.get("/api/audit?date_from=then", headers=hd).status_code == 400


def test_the_demo_register_and_last_review(client):
    g = client.get("/api/governance", headers=h("meridian", "maya")).json()
    assert {"Access", "Detection", "Screening", "Reference lists"} <= {e["area"] for e in g["register"]} and g["areas"][0] == "Workflows"
    assert any(e["second"] for e in g["register"])  # at least one change shows its second person
    (review,) = g["access"]["reviews"]
    assert review["status"] == "closed" and review["decided"] == review["people"] == 14 and review["changes"] == 1 and review["outstanding"] == 1
    assert g["access"]["can_certify"] and g["access"]["current"]["people"] == 14
    assert not client.get("/api/governance", headers=h("meridian", "tara")).json()["access"]["can_certify"]
    assert client.get("/api/governance", headers=h("kestrel", next(p for t, p in EVERYONE if t == "kestrel" and "view_team" in _permissions(t, p)))).json()["access"]["reviews"] == []


def test_a_recertification_over_http(client):
    started = client.post("/api/governance/access-reviews", headers=h("meridian", "maya"), json={"name": "Access recertification, November", "due_days": 10})
    assert started.status_code == 200 and started.json()["status"] == "open" and started.json()["decided"] == 0
    rid = started.json()["id"]
    url = f"/api/governance/access-reviews/{rid}"
    assert client.post(f"{url}/people/ivan", headers=h("meridian", "omar"), json={"decision": "keep"}).status_code == 403
    assert client.post(f"{url}/people/maya", headers=h("meridian", "maya"), json={"decision": "keep", "note": "I need all of it."}).status_code == 409
    assert client.post(f"{url}/people/ivan", headers=h("kestrel", None), json={"decision": "keep"}).status_code in (403, 404)
    assert client.post(f"{url}/close", headers=h("meridian", "maya"), json={}).status_code == 409
    for item in started.json()["items"]:
        who = "hugo" if item["person"] == "maya" else "maya"
        r = client.post(f"{url}/people/{item['person']}", headers=h("meridian", who), json={"decision": "keep", "note": "Confirmed with the line manager this week."})
        assert r.status_code == 200, r.text
    closed = client.post(f"{url}/close", headers=h("meridian", "maya"), json={})
    assert closed.status_code == 200 and closed.json()["status"] == "closed" and closed.json()["outstanding"] == 0
    reg = client.get("/api/governance?area=Access", headers=h("meridian", "audra")).json()["register"]
    assert reg[0]["subject"] == "Access recertification, November" and reg[0]["what"] == "Recertification closed: 0 change(s) asked for"
    assert client.post("/api/governance/access-reviews/acr-none/close", headers=h("meridian", "maya"), json={}).status_code == 404


def test_audit_search_over_http_respects_what_each_person_sees(client):
    everything = client.get("/api/audit", headers=h("meridian", "audra")).json()
    partial = client.get("/api/audit", headers=h("meridian", "tara")).json()
    assert everything["total"] > partial["total"] > 100 and len(everything["events"]) == audit.PAGE and everything["pages"] > 5
    page2 = client.get("/api/audit?page=2", headers=h("meridian", "audra")).json()
    assert page2["page"] == 2 and page2["events"][0]["id"] != everything["events"][0]["id"]
    restricted = client.get("/api/audit?kind=case.restricted", headers=h("meridian", "audra")).json()
    assert restricted["total"] >= 1 and client.get("/api/audit?kind=case.restricted", headers=h("meridian", "tara")).json()["total"] == 0
    by_maya = client.get("/api/audit?actor=maya&actor_kind=human", headers=h("meridian", "audra")).json()
    assert by_maya["total"] > 0 and all(e["actor"] == "maya" for e in by_maya["events"])
    assert client.get("/api/audit?text=jurisdiction", headers=h("meridian", "audra")).json()["total"] >= 1
