"""A small caseload with answers worked out by hand, and a scripted model, for the ask-anything tests."""
from datetime import timedelta

from app.ask.gateway import ModelTurn, ToolCall
from app.domain import models as m

C = {n: f"case-{n:010x}" for n in range(1, 7)}


def build_book(s):
    """Six cases for one client. Every expected figure in the tests is counted from this table.

    | case | type  | state         | customer        | owner | opened | closed | alert            | amount | to |
    | 1    | TM    | investigation | Alma (high,PEP) | ivan  | -10d   |        | structuring      | 50,000 | US |
    | 2    | TM    | triage        | Bo (low)        | none  | -2d    |        | rapid_movement   | 80,000 | AE |
    | 3    | TM    | SAR filed     | Alma            | ivan  | -40d   | -36d   | structuring      | 10,000 | US |
    | 4    | TM    | closed no SAR | Cara (medium)   | lena  | -20d   | -18d   | high_risk_geo    |  5,000 | IR |
    | 5    | fraud | triage        | Bo              | fran  | -1d    |        | card_fraud       |    900 | US |
    | 6    | fraud | refunded      | Cara            | fran  | -9d    | -8d    | account_takeover |  2,000 | US |
    """
    now = m.now()
    ago = lambda d: now - timedelta(days=d)  # noqa: E731
    for pid, name, country, risk, pep in [("P1", "Alma Reyes", "US", "high", True), ("P2", "Bo Chen", "US", "low", False),
                                          ("P3", "Cara Diaz", "MX", "medium", False)]:
        s.add(m.Party(id=pid, name=name, country=country, risk_rating=risk, pep=pep, occupation="trader"))
        s.add(m.Account(id=f"A-{pid}", party_id=pid))
    s.flush()
    rows = [  # n, type, version, state, party, priority, owner, queue, opened, closed, disposition, agent, scenario, amounts, country
        (1, "tm_alert_investigation", "4.0.0", "investigation", "P1", "high", "ivan", "aml-l2", 10, None, None, "escalate", "structuring", [30000, 20000], "US"),
        (2, "tm_alert_investigation", "4.0.0", "triage", "P2", "medium", None, "aml-l1", 2, None, None, None, "rapid_movement", [80000], "AE"),
        (3, "tm_alert_investigation", "4.0.0", "filed", "P1", "high", "ivan", None, 40, 36, "sar_filed", "escalate", "structuring", [10000], "US"),
        (4, "tm_alert_investigation", "4.0.0", "closed_no_sar", "P3", "low", "lena", None, 20, 18, "no_sar", "escalate", "high_risk_geo", [5000], "IR"),
        (5, "fraud_claim", "1.0.0", "triage", "P2", "critical", "fran", "fraud", 1, None, None, None, "card_fraud", [900], "US"),
        (6, "fraud_claim", "1.0.0", "refunded", "P3", "medium", "fran", None, 9, 8, "fraud_confirmed", "escalate", "account_takeover", [2000], "US"),
    ]
    for n, ctype, version, state, party, priority, owner, queue, opened, closed, disposition, agent, scenario, amounts, country in rows:
        txn_ids = []
        for i, amount in enumerate(amounts):
            txn_ids.append(f"T{n}-{i}")
            s.add(m.Transaction(id=txn_ids[-1], account_id=f"A-{party}", party_id=party, ts=ago(opened), amount=amount, direction="out",
                                channel="wire", counterparty="Counterparty Ltd", counterparty_country=country))
        s.add(m.Case(id=C[n], case_type=ctype, type_version=version, state=state, party_id=party, priority=priority, owner=owner, queue=queue,
                     created=ago(opened), state_entered=ago(opened), closed=ago(closed) if closed else None, disposition=disposition,
                     agent_recommendation=agent, sla_due=ago(opened) + timedelta(days=30), narrative=f"Narrative for case {n}."))
        s.flush()
        s.add(m.Alert(id=f"alr-{n}", dedupe_key=f"k{n}", scenario=scenario, scenario_version="1", party_id=party, score=0.8,
                      explanation=f"Explanation {n}", txn_ids=txn_ids, case_id=C[n], status="in_case"))
    s.flush()


def call(name, **args):
    return ToolCall(f"call-{name}-{len(str(args))}", name, args)


class Scripted:
    """A model that plays back fixed turns, and remembers everything the answer loop told it."""

    name = "scripted"

    def __init__(self, *turns):
        self.turns, self.started, self.received, self.questions = list(turns), [], [], []

    def start(self, **kw):
        self.started.append(kw)
        return self

    def _next(self):
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn(self.received[-1]) if callable(turn) else turn

    def ask(self, question):
        self.questions.append(question)
        return self._next()

    def results(self, outputs):
        self.received.append(outputs)
        return self._next()


def tools_turn(*calls):
    return ModelTurn("tools", tool_calls=list(calls))


def answer(text):
    return ModelTurn("answer", text)
