"""The pieces that keep an answer honest: the figure checker, the calculator, record citations, and the offline
planner's reading of questions about records other than cases. No database except where a citation needs one."""
from datetime import date

import pytest

from app.ask import calculator, citations, tools, verify
from app.ask.landscape import catalog
from app.ask.providers import offline_landscape as ol
from app.ask.semantic import QueryError
from app.domain.personas import get_persona
from ask_support import build_book
from support import T


# --- figures ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("written,known", [
    ("1,234,567", 1234567), ("$1,234,567", 1234567), ("$1.2M", 1234567), ("$1.23 million", 1234567), ("1.2m", 1234567),
    ("$12k", 12345), ("12 thousand", 12345), ("34%", 0.3396), ("34%", 33.96), ("33.96%", 33.96), ("2.5bn", 2.5e9),
    ("0.5", 0.5), ("17.25", 17.25), ("$9,999.50", 9999.5),
])
def test_a_figure_written_from_a_tool_result_is_verified(written, known):
    out = verify.check_figures(f"The total was {written} in the period.", [{"value": known}])
    assert out["verified"] == [written] and out["unverified"] == []


@pytest.mark.parametrize("written,known", [
    ("1,234,568", 1234567), ("$1.3M", 1234567), ("35%", 0.3396), ("12.4", 12.3), ("$13k", 12345), ("2.6bn", 2.5e9),
])
def test_a_figure_rounded_wrongly_or_made_up_is_flagged(written, known):
    out = verify.check_figures(f"The total was {written}.", [{"value": known}])
    assert out["unverified"] == [written]


def test_numbers_inside_ids_dates_and_times_are_not_figures():
    text = "Case case-0a1b2c3d4e was opened 2026-11-02 at 14:30 after alert alr-12ab34cd56; see SAR sar-00ff00ff00."
    assert verify.figures_in(text) == []


def test_small_counting_words_and_numbers_from_the_question_count_as_known():
    out = verify.check_figures("Of the top 3 customers, 2 are PEPs; you asked about 250,000 and above, and 412 transactions qualify.",
                               [{"rows": [{"n": 412}]}], question="Which customers moved over 250,000?")
    assert out["unverified"] == [] and out["stated"] == 4
    # a small number with a unit is a figure, not a word
    assert verify.check_figures("A loss of $7.", [{}])["unverified"] == ["$7"]


def test_numbers_anywhere_in_a_result_are_known_including_inside_text():
    result = {"rows": [{"key": ["wire"], "values": [12, 1500000.0]}], "note": "of which 8 were over $10,000", "flag": True}
    assert verify.numbers_in(result) >= {12.0, 1500000.0, 8.0, 10000.0}
    assert 1.0 not in verify.numbers_in({"flag": True})  # a boolean is not the number one


def test_platform_ids_are_found_once_each_in_order():
    text = "See alr-00000000aa and hld-00000000bb, then alr-00000000aa again; case-00000000cc too."
    assert verify.platform_ids(text) == [("alert", "alr-00000000aa"), ("hold", "hld-00000000bb"), ("case", "case-00000000cc")]


# --- the calculator ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("expr,value", [("1+2*3", 7), ("(1,200 - 300) / 3", 300), ("-4 + 10", 6), ("round(2/3, 2)", 0.67),
                                        ("$1,000 * 12%", 12000), ("round(10/3)", 3)])
def test_the_calculator_does_arithmetic(expr, value):
    assert calculator.evaluate(expr) == pytest.approx(value)


@pytest.mark.parametrize("expr", ["__import__('os').system('id')", "2**1000", "a+1", "1/0", "open('x')", "round(1, n=2)", "", "9" * 400,
                                  "1e300 * 1e300", "[1, 2]", "(lambda: 1)()"])
def test_the_calculator_refuses_anything_that_is_not_arithmetic(expr):
    with pytest.raises(QueryError):
        calculator.evaluate(expr)


def test_the_calculate_tool_returns_the_value_so_the_answer_can_be_checked(s):
    ctx = tools.ToolContext(None, get_persona(T, "tara"))
    res = tools.run(ctx, "calculate", {"expression": "(1200 - 900) / 900 * 100"})
    assert not res.error and res.data["value"] == pytest.approx(33.333333)
    assert verify.check_figures("Up 33.3% on the month.", [res.data])["unverified"] == []
    assert tools.run(ctx, "calculate", {"expression": "import os"}).error


# --- citations -----------------------------------------------------------------------------------------------------
def test_links_go_to_the_page_a_person_opens_the_record_on():
    assert citations.link("customer", {"id": "C1"}) == "/customers/C1"
    assert citations.link("filing", {"id": "sar-1"}) == "/filings/sar-1"
    assert citations.link("hold", {"id": "hld-1", "case_id": "case-9"}) == "/cases/case-9"
    assert citations.link("hold", {"id": "hld-1"}) == "/sanctions"
    assert citations.link("transaction", {"id": "7", "customer_id": "C1"}) == "/customers/C1"
    assert citations.link("lookback", {"id": "lbk-1"}) == "/detection"


def test_an_id_a_tool_showed_is_a_source_and_an_invented_one_is_flagged(s):
    build_book(s)
    s.commit()
    from sqlalchemy import select

    from app.domain import models as m
    alert = s.scalars(select(m.Alert)).first()
    asker = get_persona(T, "ivan")
    seen = {alert.id: {"kind": "alert", "id": alert.id, "case_id": alert.case_id, "customer_id": alert.party_id}}
    answer = f"Alert {alert.id} is the one; alr-ffffffffff does not exist."
    sources, unverified = citations.resolve(s, asker, answer, seen)
    assert [x["id"] for x in sources] == [alert.id] and sources[0]["shown_by_tool"] and sources[0]["link"].startswith("/")
    assert unverified == ["alr-ffffffffff"]


def test_a_customer_id_counts_only_where_a_tool_showed_it(s):
    build_book(s)
    s.commit()
    from sqlalchemy import select

    from app.domain import models as m
    party = s.scalars(select(m.Party)).first()
    asker = get_persona(T, "ivan")
    seen = {party.id: {"kind": "customer", "id": party.id, "case_id": None, "customer_id": party.id}}
    assert [x["id"] for x in citations.resolve(s, asker, f"Customer {party.id} has three cases.", seen)[0]] == [party.id]
    assert citations.resolve(s, asker, f"Customer {party.id} has three cases.", {})[0] == []  # no prefix, not shown: not a claim we can place


# --- the offline planner on other objects --------------------------------------------------------------------------------
VOCAB = {o.id: o.vocabulary() for o in catalog.objects().values()}
TODAY = date(2026, 11, 21)


@pytest.mark.parametrize("question,obj", [
    ("How many transactions over $10,000 by channel?", "transactions"), ("Show the active holds", "holds"),
    ("How many alerts by status?", "alerts"), ("SARs filed this month", "filings"), ("total chargeback losses by rail", "disputes"),
    ("How often are risk ratings overridden?", "risk_assessments"), ("agent recommendations by case type", "agent_runs"),
    ("pipeline runs that failed", "pipeline_runs"), ("high-risk customers by country", "customers"),
    ("transactions of high-risk customers", "transactions"), ("lookbacks by status", "lookbacks"),
])
def test_the_planner_picks_the_object_the_question_names(question, obj):
    assert ol.plan(question, TODAY, VOCAB)["args"]["object"] == obj


def test_questions_that_name_cases_stay_with_the_case_planner():
    assert ol.plan("How many open cases by case type?", TODAY, VOCAB) is None
    assert ol.plan("What is the weather?", TODAY, VOCAB) is None


def test_the_planner_reads_amounts_splits_and_periods():
    p = ol.plan("Total value of outgoing wire transactions over $1.5m in the last 30 days by customer risk rating", TODAY, VOCAB)
    a = p["args"]
    assert p["tool"] == "breakdown" and a["group_by"] == ["customer_risk_rating"] and a["measures"][0] == "total_amount"
    f = {(x["field"], x["op"]): x["value"] for x in a["filters"]}
    assert f[("amount", ">")] == 1_500_000 and f[("direction", "=")] == "out" and f[("channel", "=")] == "wire"
    assert f[("date", ">=")] == "2026-10-22"


def test_the_planner_lists_when_asked_to_show_and_caps_the_list():
    p = ol.plan("Show the top 40 largest transactions", TODAY, VOCAB)
    assert p["tool"] == "list_records" and p["args"]["sort_by"] == "amount" and p["args"]["limit"] == 25


def test_the_planner_only_uses_objects_the_person_may_read():
    no_filings = {k: v for k, v in VOCAB.items() if k != "filings"}
    assert (ol.plan("How many regulatory filings are overdue?", TODAY, no_filings) or {}).get("args", {}).get("object") != "filings"


def test_every_planned_call_is_one_the_tools_accept():
    """Whatever the planner writes must pass the tools' own argument checks against the catalog."""
    for q in ["How many transactions over $10,000 by channel?", "average dispute amount by rail", "alerts closed by agents this week",
              "agent actions refused", "KYC triggers by kind", "detection changes by status", "monthly trend of holds"]:
        p = ol.plan(q, TODAY, VOCAB)
        assert p, q
        obj = catalog.get(p["args"]["object"])
        for g in p["args"].get("group_by", []):
            assert obj.dims[g].group, (q, g)
        for f in p["args"].get("filters", []):
            assert f["field"] in obj.dims, (q, f)
        for x in p["args"].get("measures", []):
            assert x in obj.measures, (q, x)


def test_every_suggested_question_about_other_records_is_one_the_planner_places():
    from app.ask.service import LANDSCAPE_SUGGESTIONS
    for oid, question in LANDSCAPE_SUGGESTIONS:
        assert ol.plan(question, TODAY, VOCAB)["args"]["object"] == oid, question
