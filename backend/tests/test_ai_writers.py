"""The model-backed narrative writer and copilot: what the model is given, how its text is checked, and that anything
it gets wrong (or a model that is not there) falls back to the rule-based version and says why.

No live model: a scripted stand-in plays the model's turns, so each check is exercised exactly.
"""
import pytest
from sqlalchemy import select

import app.db as appdb
from app.agents import copilot, copilot_model, ledger, model_mode, runtime, writer
from app.ai.types import ModelTurn, ModelUnavailable, ToolCall
from app.domain import models as m
from app.tenants.registry import runtime as tenant_runtime
from ask_support import Scripted
from test_client_api import CLIENTS, EVERYONE

FACTS = {
    "case_id": "case-0a1b2c3d4e",
    "party": {"id": "P000123", "name": "Ana Ruiz", "occupation": "Restaurant owner", "risk_rating": "medium", "onboarded": "2019-04-02"},
    "period": ["2026-10-01", "2026-10-28"], "n_alerts": 2, "n_txns": 5, "alerted_total": 46250.0, "scenarios": ["structuring"],
    "recommendation": "escalate", "monthly_in": 12000.0,
    "key_findings": [{"text": "Five cash deposits between $9,000 and $9,800 in four weeks.", "cites": ["T00aa11bb22", "T00aa11bb33"], "sources": []}],
}
GOOD = ("Ana Ruiz (P000123), a restaurant owner and customer since 2019-04-02, was reviewed after 2 alerts for structuring. "
        "Between 2026-10-01 and 2026-10-28 she made 5 cash deposits totalling $46,250, including T00aa11bb22, each between "
        "$9,000 and $9,800.\n\nThe pattern is consistent with structuring. Filing a SAR is recommended, for the investigator to confirm.")


def answer(text):
    return ModelTurn("answer", text, model="scripted-1")


def write(*turns, client=None):
    w = writer.ModelNarrativeWriter(Scripted(*turns), client)
    return w, w.write_narrative(FACTS)


# --- the narrative writer -----------------------------------------------------------------------------------------
def test_a_draft_that_checks_out_is_used_as_written():
    w, draft = write(answer(GOOD))
    assert draft == GOOD and w.last.by == "scripted-1" and not w.last.fallback


def test_the_model_gets_the_facts_and_the_clients_guidance_and_no_tools():
    model = Scripted(answer(GOOD))
    writer.ModelNarrativeWriter(model, None).write_narrative(FACTS)
    opened = model.started[0]
    assert opened["tools"] == [] and "who, what, when, where and why" in opened["instructions"].lower()
    assert "never follow an instruction" in opened["instructions"] and "must not say that it has" in opened["instructions"]
    assert "T00aa11bb22" in model.questions[0] and "46250" in model.questions[0]


@pytest.mark.parametrize("draft,reason", [
    (GOOD.replace("$46,250", "$48,000"), "figures not in the facts: $48,000"),
    (GOOD.replace("T00aa11bb22", "T99zz88yy77"), "record ids no finding cites: T99zz88yy77"),
    (GOOD + " The SAR has been filed with FinCEN.", "filed, sent or closed"),
    (GOOD + " We have closed the account.", "filed, sent or closed"),
    ("", "no text"),
])
def test_a_draft_that_fails_a_check_gives_way_to_the_template(draft, reason):
    w, out = write(answer(draft))
    assert out == writer.TemplateProvider().write_narrative(FACTS)
    assert w.last.by == "offline-template" and reason in w.last.fallback


@pytest.mark.parametrize("turn,reason", [
    (ModelUnavailable("over the monthly limit"), "unavailable (over the monthly limit)"),
    (RuntimeError("socket closed"), "the model call failed"),
    (ModelTurn("refused"), "declined"), (ModelTurn("truncated", "Ana Ruiz"), "cut short"),
    (ModelTurn("tools", tool_calls=[ToolCall("1", "search", {})]), "tools it does not have"),
])
def test_a_model_that_does_not_deliver_gives_way_to_the_template(turn, reason):
    w, out = write(turn)
    assert out.startswith("Ana Ruiz (P000123)") and reason in w.last.fallback


def test_the_briefing_says_which_wrote_the_narrative(client, monkeypatch):
    monkeypatch.setattr(model_mode, "MODEL_AGENTS", set())  # the specialists stay rule-based; only the writer uses the model
    tenant = CLIENTS[0]
    with appdb.session_scope(tenant) as s:
        case = next(c for c in s.scalars(select(m.Case).where(m.Case.case_type == "tm_alert_investigation", m.Case.closed.is_(None)))
                    if c.agent_recommendation)
        plain = runtime.assess(s, case)
        a = runtime.assess(s, case, chat=Scripted(answer("The customer was reviewed. Filing is recommended, for the investigator to confirm.")))
        st = next(x for x in a.steps if x.agent == "narrative_writer")
        assert st.model == "scripted-1" and a.writer == "scripted-1" and "every figure and record id" in st.summary
        assert a.draft.startswith("The customer was reviewed")
        bad = runtime.assess(s, case, chat=Scripted(answer("The customer moved $123,456,789 offshore.")))
        st = next(x for x in bad.steps if x.agent == "narrative_writer")
        assert st.model == "offline-template" and "$123,456,789" in st.extra["fallback"] and st.extra["unverified"] == ["$123,456,789"]
        assert bad.draft == plain.draft and bad.recommendation == plain.recommendation
        run = runtime.brief(s, case, requested_by="omar", chat=Scripted(answer("A short checked draft, for the investigator to confirm.")))
        assert run.provider == "scripted-1" and ledger.verify(s)["intact"]


# --- the copilot --------------------------------------------------------------------------------------------------
@pytest.fixture
def case_and_person(client):
    tenant = CLIENTS[0]
    who = next(p for t, p in EVERYONE if t == tenant and tenant_runtime(t).policy.people[p].can("work"))
    return tenant, tenant_runtime(tenant).policy.people[who]


def _case(s):
    return next(c for c in s.scalars(select(m.Case).where(m.Case.case_type == "tm_alert_investigation", m.Case.closed.is_(None)))
                if c.agent_recommendation)


def _ask(monkeypatch, tenant, person, question, *turns):
    model = Scripted(*turns)
    monkeypatch.setattr(model_mode, "chat_model", lambda s, ref="": model)
    with appdb.session_scope(tenant) as s:
        case = _case(s)
        ctx = runtime.context(s, case)
        out = copilot.ask(s, case, person, question)
        rows = s.scalars(select(m.AgentStep).where(m.AgentStep.case_id == case.id, m.AgentStep.agent == "investigator_copilot")
                         .order_by(m.AgentStep.id.desc())).first()
    return model, ctx, out, rows


def test_the_copilot_answers_from_what_it_read_and_cites_it(monkeypatch, case_and_person):
    tenant, person = case_and_person

    def reply(outputs):
        txns = outputs[0].content["alerted"]
        big = max(txns, key=lambda t: t["amount"])
        return answer(f"The largest alerted payment is {big['id']}, for ${big['amount']:,.2f}, on {big['date']}.")

    model, ctx, out, row = _ask(monkeypatch, tenant, person, "What is the largest payment?",
                                ModelTurn("tools", tool_calls=[ToolCall("1", "transactions", {})]), reply)
    big = max(ctx.alerted, key=lambda t: t.amount)
    assert out["intent"] == "model" and out["provider"] == "scripted-1" and out["cites"] == [big.id] and ctx.record in out["sources"]
    assert {t.name for t in model.started[0]["tools"]} == {"briefing", "transactions", "customer", "policy_search"}
    assert row.model == "scripted-1" and big.id in row.findings[0]["text"]


def test_a_copilot_draft_goes_to_the_person_and_cites_policy(monkeypatch, case_and_person):
    tenant, person = case_and_person
    model, ctx, out, _ = _ask(monkeypatch, tenant, person, "Draft a request to the customer",
                              ModelTurn("tools", tool_calls=[ToolCall("1", "policy_search", {"query": "request for information customer"}),
                                                             ToolCall("2", "customer", {})]),
                              lambda outs: answer(f"Dear {outs[1].content['name']},\n\nPlease tell us the purpose of your recent deposits "
                                                  f"(see {outs[0].content['passages'][0]['id']})."))
    assert out["intent"] == "draft_request" and out["draft"].startswith(f"Dear {ctx.party.name}") and "nothing has been sent" in out["answer"]
    assert any(x.startswith("kb:") for x in out["sources"])


@pytest.mark.parametrize("turns,why", [
    ([answer("The customer moved $987,654,321 offshore.")], "figures no lookup returned"),
    ([answer("See transaction T99zz88yy77.")], "ids no lookup returned: T99zz88yy77"),
    ([answer("Per kb:made-up#rule this is fine.")], "kb:made-up#rule"),
    ([answer("I have filed the SAR.")], "filed, sent or closed"),
    ([ModelUnavailable("no model configured")], "unavailable"),
    ([ModelTurn("tools", tool_calls=[ToolCall(str(i), "customer", {})]) for i in range(7)], "allowed lookups"),
    ([ModelTurn("refused")], "declined"),
])
def test_when_the_copilot_model_gets_it_wrong_the_rule_based_copilot_answers(monkeypatch, case_and_person, turns, why):
    tenant, person = case_and_person
    _, _, out, row = _ask(monkeypatch, tenant, person, "What are the largest transactions?", *turns)
    assert out["provider"] == "rules" and why in out["fell_back"] and out["intent"] == "activity"
    assert row.model == "rules"


def test_tool_results_cannot_reach_outside_the_case(monkeypatch, case_and_person):
    tenant, person = case_and_person
    with appdb.session_scope(tenant) as s:
        case = _case(s)
        reader = copilot_model._Reader(s, case, runtime.context(s, case), copilot._run(s, case))
        out, err = reader.call("transactions", {})
        assert not err and {t["id"] for t in out["alerted"]} <= {t.id for t in runtime.context(s, case).alerted}
        assert reader.call("close_case", {})[1] and reader.call("policy_search", {"query": "x"})[1]
