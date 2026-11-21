"""Synthetic demo data with planted typologies. All people, companies and activity are fictional."""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .agents.investigator import investigate
from .alerts.hub import ingest_external_alert, route_alerts
from .cases import assignments, requests
from .cases.case_types import load_case_types, reset_published
from .cases.service import allowed_transitions, complete_stage_tasks, create_case, save_narrative, transition
from .cases.timers import run_timers
from .config import settings
from .db import Base, engine, ensure_schema, session_scope
from .detection.engine import run_detection
from .domain.models import Account, Case, Party, Transaction, WorkflowVersion, now
from .domain.personas import PERSONAS

FIRST = ["Elena", "Marcus", "Priya", "Tomás", "Aisha", "Daniel", "Mei", "Jonas", "Fatima", "Lucas", "Ingrid", "Kwame", "Sofia",
         "Hiro", "Nadia", "Owen", "Leila", "Viktor", "Carmen", "Arjun", "Greta", "Samuel", "Yara", "Felix", "Noor", "Callum"]
LAST = ["Okafor", "Lindqvist", "Marchetti", "Tanaka", "Abdullah", "Novak", "Castellanos", "Bergström", "Mensah", "Petrakis",
        "Hartmann", "Villanueva", "Kowalczyk", "Ibrahim", "Sorensen", "Delacroix", "Yamamoto", "Brennan", "Moreau", "Achebe"]
JOBS = ["teacher", "nurse", "software engineer", "electrician", "accountant", "sales manager", "graphic designer",
        "truck driver", "pharmacist", "retired", "consultant", "chef", "student", "civil engineer"]
MERCHANTS = ["Harbor Grocers", "Citywide Utilities", "Maple Street Pharmacy", "Northline Transit", "Orchard Rent Co",
             "Brightwave Telecom", "Summit Insurance", "Corner Cafe", "Lakeside Fitness", "Atlas Fuel"]
EMPLOYERS = ["Kestrel Logistics Payroll", "Bluefin Health Payroll", "Ridgeway Schools Payroll", "Tessera Software Payroll"]
SHELLS = ["Orion Global Consulting Ltd", "Bluepeak Holdings SA", "Meridian Trade Partners", "Sable Ridge Imports", "Quanta Ventures LLC"]


class Gen:
    def __init__(self, s: Session, seed: int = 7, end: datetime | None = None):
        self.s, self.r, self.end, self.n = s, random.Random(seed), end or now(), 0
        self.txns: list[Transaction] = []

    def party(self, occupation: str | None = None, risk: str = "low", pep: bool = False) -> Party:
        self.n += 1
        p = Party(id=f"P{10000 + self.n}", name=f"{self.r.choice(FIRST)} {self.r.choice(LAST)}",
                  occupation=occupation or self.r.choice(JOBS), risk_rating=risk, pep=pep,
                  onboarded=self.end - timedelta(days=self.r.randint(200, 3000)))
        self.s.add(p)
        self.s.add(Account(id=f"A{p.id[1:]}", party_id=p.id))
        return p

    def txn(self, p: Party, ts: datetime, amount: float, direction: str, channel: str, cp: str = "", country: str = "US") -> Transaction:
        t = Transaction(id=f"T{self.r.getrandbits(40):010x}", account_id=f"A{p.id[1:]}", party_id=p.id, ts=ts, amount=round(amount, 2),
                        direction=direction, channel=channel, counterparty=cp, counterparty_country=country)
        self.s.add(t)
        self.txns.append(t)
        return t

    def settle(self) -> None:
        """Shift the whole history back so nothing is dated after 'now'; patterns keep their shape."""
        over = max(t.ts for t in self.txns) - self.end + timedelta(minutes=5)
        if over > timedelta(0):
            for t in self.txns:
                t.ts -= over

    def baseline(self, p: Party, days: int = 90, per_day: float = 1.2) -> None:
        emp = self.r.choice(EMPLOYERS)
        for d in range(days):
            day = self.end - timedelta(days=d)
            if d % 14 == 0:
                self.txn(p, day.replace(hour=6, minute=5), self.r.uniform(2200, 6200), "in", "ach", emp)
            for _ in range(self.r.choices([0, 1, 2, 3], [0.25, 0.4, 0.25, 0.1])[0] if per_day > 1 else 1):
                self.txn(p, day.replace(hour=self.r.randint(8, 21), minute=self.r.randint(0, 59)), self.r.uniform(6, 240),
                         "out", self.r.choice(["card", "card", "ach", "p2p"]), self.r.choice(MERCHANTS))

    def when(self, lo: int, hi: int) -> datetime:
        return (self.end - timedelta(days=self.r.randint(lo, hi))).replace(hour=self.r.randint(9, 16), minute=self.r.randint(0, 59))

    # --- planted typologies -------------------------------------------------
    def structuring(self, lo: int, hi: int, benign: bool = False) -> Party:
        p = self.party("restaurant owner" if benign else None, "medium")
        self.baseline(p)
        if benign:
            for d in range(8, 90, 3):
                self.txn(p, (self.end - timedelta(days=d)).replace(hour=10), self.r.uniform(2100, 6400), "in", "cash")
        start = self.when(lo, hi)
        for i in range(3 if benign else self.r.randint(4, 6)):
            self.txn(p, start + timedelta(days=i, minutes=self.r.randint(0, 200)), self.r.uniform(8200, 9950), "in", "cash")
        return p

    def rapid(self, lo: int, hi: int, benign: bool = False) -> Party:
        p = self.party(None, "medium")
        self.baseline(p)
        start, amt = self.when(lo, hi), self.r.uniform(26000, 92000)
        if benign:
            self.txn(p, start, amt, "in", "wire", "Halcyon Brokerage Services")
            self.txn(p, start + timedelta(hours=20), amt * 0.97, "out", "wire", "First National Title & Escrow")
        else:
            self.txn(p, start, amt, "in", "wire", self.r.choice(SHELLS), self.r.choice(["CY", "AE", "HK", "US"]))
            left, k = amt * self.r.uniform(0.88, 0.98), self.r.randint(2, 4)
            for i in range(k):
                self.txn(p, start + timedelta(hours=4 + i * 9), left / k, "out", self.r.choice(["wire", "p2p"]),
                         f"{self.r.choice(FIRST)} {self.r.choice(LAST)}", self.r.choice(["US", "TR", "AE", "NG"]))
        return p

    def geo(self, lo: int, hi: int) -> Party:
        p = self.party("consultant", "high", pep=self.r.random() < 0.3)
        self.baseline(p)
        start = self.when(lo, hi).replace(day=min(self.when(lo, hi).day, 20))
        for i in range(self.r.randint(2, 4)):
            self.txn(p, start + timedelta(days=i), self.r.uniform(6000, 24000), "out", "wire",
                     self.r.choice(SHELLS), self.r.choice(["IR", "SY", "MM"]))
        return p

    def sanctions(self, lo: int, hi: int, name: str, country: str) -> Party:
        p = self.party("import-export trader", "medium")
        self.baseline(p)
        self.txn(p, self.when(lo, hi), self.r.uniform(9000, 60000), "out", "wire", name, country)
        return p


class Desk:
    """Plays the demo personas: claims a case, ticks the stage tasks, takes the step. Every guard still applies."""

    def __init__(self, s: Session, g: Gen):
        self.s, self.g = s, g

    def step(self, c: Case, to: str, who: str, at: datetime, note: str = "") -> datetime:
        at = min(at, self.g.end - timedelta(minutes=self.g.r.randint(3, 50)))
        p = PERSONAS[who]
        if c.queue in p.queues and c.owner != p.id:
            assignments.claim(self.s, c, p, ts=at - timedelta(minutes=20))
        complete_stage_tasks(self.s, c, p, ts=at - timedelta(minutes=5))
        transition(self.s, c, to, p, note, ts=at)
        return at

    def own(self, c: Case, who: str, at: datetime) -> None:
        assignments.claim(self.s, c, PERSONAS[who], ts=min(at, self.g.end - timedelta(minutes=30)))

    def ask(self, c: Case, who: str, recipient: str, question: str, at: datetime, due_days: int = 5, answer: str | None = None,
            answered_after_h: int = 30):
        r = requests.raise_request(self.s, c, PERSONAS[who], recipient, question, due_days, ts=at)
        if answer:
            requests.answer_request(self.s, r, PERSONAS[who], answer, ts=min(at + timedelta(hours=answered_after_h), self.g.end - timedelta(minutes=40)))
        return r


def _work_monitoring(d: Desk, cases: list[Case]) -> None:
    """Walk monitoring and sanctions cases through their workflows, mostly following the agent."""
    g = d.g
    for n, c in enumerate(cases):
        age_h = (g.end - c.created).total_seconds() / 3600
        l2 = "ivan" if n % 2 else "lena"
        follow = g.r.random() < 0.88
        escalate = (c.agent_recommendation == "escalate") == follow
        t = c.created + timedelta(hours=g.r.randint(2, 20))
        if c.case_type == "sanctions_hit":
            if age_h < 30:
                if age_h > 8:
                    d.own(c, "ana", c.created + timedelta(hours=1))
                continue
            t = c.created + timedelta(hours=g.r.randint(1, 3))
            if escalate:
                d.step(c, "escalated", "ana", t)
                if age_h > 60:
                    d.step(c, "blocked", "sofia", t + timedelta(hours=g.r.randint(4, 18)), "Match confirmed on name and jurisdiction; payment blocked and reported.")
            else:
                d.step(c, "discounted", "ana", t, "Name differs in a distinguishing token; no secondary identifier matches.")
            continue
        if age_h < 40:
            if age_h > 20 and n % 3 == 0:
                d.own(c, "ana", c.created + timedelta(hours=3))
            continue
        if not escalate and n % 4 == 0:
            d.step(c, "closed_no_sar", "ana", t, "Activity matches the documented business profile; agent briefing reviewed and agreed.")
            continue
        d.step(c, "investigation", "ana", t)
        if age_h < 100:
            if n % 3:
                d.own(c, l2, t + timedelta(hours=5))
            continue
        d.own(c, l2, t + timedelta(hours=5))
        if n % 5 == 0:  # the investigator needs something from outside the team
            asked = t + timedelta(hours=20)
            old = age_h > 400
            r = d.ask(c, l2, "relationship_manager" if n % 2 else "customer",
                      "Please confirm the source of the funds received and the purpose of the onward payments, with supporting documents.",
                      asked, due_days=5)
            d.step(c, "awaiting_information", l2, asked + timedelta(minutes=30))
            if not old:
                continue
            requests.answer_request(d.s, r, PERSONAS[l2], "Customer provided a sale agreement and bank confirmation covering the incoming funds.",
                                    ts=asked + timedelta(hours=38))
            d.step(c, "investigation", l2, asked + timedelta(hours=40))
        t += timedelta(days=g.r.randint(1, 4))
        if age_h < 150:
            continue
        if not escalate:
            d.step(c, "closed_no_sar", l2, t, "Activity is consistent with the customer profile and the documented purpose.")
            continue
        d.step(c, "sar_drafting", l2, t)
        if age_h < 200:
            continue
        save_narrative(d.s, c, c.narrative, PERSONAS[l2], ts=min(t + timedelta(hours=3), g.end - timedelta(hours=1)))
        d.step(c, "mlro_review", l2, t + timedelta(hours=4))
        if age_h < 260:
            continue
        options = {x["to"] for x in allowed_transitions(d.s, c, PERSONAS["maya"])}
        t += timedelta(days=g.r.randint(1, 2))
        if "filed" in options:
            d.step(c, "filed", "maya", t)
        else:
            d.step(c, "senior_approval", "maya", t)
            if age_h > 420:
                d.step(c, "filed", "hugo", t + timedelta(hours=g.r.randint(6, 30)))


def _work_qa(d: Desk) -> None:
    """Quinn reviews most sampled cases; the most recent one fails and sends its case back to the team."""
    reviews = sorted(d.s.scalars(select(Case).where(Case.case_type == "qa_review")), key=lambda c: c.created)
    due = [c for c in reviews if (d.g.end - c.created).total_seconds() / 3600 >= 48]
    for n, c in enumerate(due):
        if c is due[-1]:
            at = d.g.end - timedelta(hours=7)
            d.own(c, "quinn", at - timedelta(hours=1))
            transition(d.s, c, "failed", PERSONAS["quinn"],
                       "The closure rationale does not explain why the pattern fits the customer's business. Returned for a documented explanation.", ts=at)
        elif n % 4 != 3:
            d.step(c, "passed", "quinn", c.created + timedelta(hours=d.g.r.randint(20, 44)))


def _seed_kyc(d: Desk) -> None:
    g, s = d.g, d.s
    types = load_case_types()
    low = g.r.sample(list(s.scalars(select(Party).where(Party.risk_rating == "low").limit(80))), 5)
    high = list(s.scalars(select(Party).where(Party.risk_rating == "high").limit(2)))
    ask = "Your periodic review is due. Please upload current identification, proof of address and confirm your occupation."
    for n, p in enumerate(low + high):
        c = create_case(s, types["kyc_refresh"], p.id, "medium" if p.risk_rating == "high" else "low", created=g.when(3, 24), actor="kyc-scheduler")
        age_h = (g.end - c.created).total_seconds() / 3600
        d.own(c, "kai", c.created + timedelta(hours=2))
        if n in (0, 3):  # still waiting on the customer
            d.ask(c, "kai", "customer", ask, c.created + timedelta(hours=3), due_days=10 if n else 3)
            continue
        d.ask(c, "kai", "customer", ask, c.created + timedelta(hours=3), due_days=10,
              answer="Customer uploaded passport, utility bill and employer letter.", answered_after_h=50)  # answered before the step out of outreach
        t = d.step(c, "review", "kai", c.created + timedelta(hours=60))
        if age_h < 150:
            continue
        if p.risk_rating == "high":
            t = d.step(c, "edd", "kai", t + timedelta(hours=20))
            if n == len(low):
                d.step(c, "approved", "maya", t + timedelta(hours=30), "Source of wealth evidenced by audited accounts; relationship retained at high risk.")
        else:
            d.step(c, "approved", "kai", t + timedelta(hours=20), "Documents current, screening clear, activity matches the stated occupation.")
    applicants = [("Helena Brandt", "architect", "low", False, 30), ("Rashid Al-Amin", "import-export trader", "high", False, 70),
                  ("Odile Fontaine", "government minister", "medium", True, 50), ("Mateo Aguilar", "student", "low", False, 6),
                  ("Bianca Roth", "dentist", "low", False, 20), ("Tobias Lindgren", "crypto trader", "high", False, 110)]
    for n, (name, job, risk, pep, hours_ago) in enumerate(applicants):
        g.n += 1
        p = Party(id=f"P{10000 + g.n}", name=name, occupation=job, risk_rating=risk, pep=pep, onboarded=g.end)
        s.add(p)
        s.add(Account(id=f"A{p.id[1:]}", party_id=p.id))
        s.flush()
        c = create_case(s, types["kyc_onboarding"], p.id, "high" if pep or risk == "high" else "medium",
                        created=g.end - timedelta(hours=hours_ago), actor="onboarding-portal")
        if n == 3:
            continue  # fresh application, nobody has picked it up
        d.own(c, "kai", c.created + timedelta(hours=1))
        if n == 4:
            d.ask(c, "kai", "customer", "The proof of address is older than three months. Please send a recent one.", c.created + timedelta(hours=2), due_days=7)
            d.step(c, "awaiting_customer", "kai", c.created + timedelta(hours=2, minutes=10))
            continue
        t = d.step(c, "risk_assessment", "kai", c.created + timedelta(hours=5))
        if n == 0:
            d.step(c, "approved", "kai", t + timedelta(hours=3))
        elif n in (1, 2):
            t = d.step(c, "edd_approval", "kai", t + timedelta(hours=4))
            if n == 1:
                d.step(c, "approved", "tara", t + timedelta(hours=20), "Trade documents and audited accounts support the declared source of wealth.")


FRAUD = [  # (scenario, minutes ago, [(amount, payee, country, channel, hour or None)], stage to reach)
    ("account_takeover", 35, [(1850, "QuickSend Transfer", "RO", "p2p", 3), (2400, "QuickSend Transfer", "RO", "p2p", 3)], "triage"),
    ("card_fraud", 300, [(412, "Nova Electronics Online", "US", "card", None), (389, "Nova Electronics Online", "US", "card", None)], "triage"),
    ("account_takeover", 60 * 30, [(4200, "Marek Dvorak", "CZ", "wire", 2), (3900, "Marek Dvorak", "CZ", "wire", 2)], "customer_contact"),
    ("card_fraud", 60 * 50, [(96, "Harbor Grocers", "US", "card", None), (61, "Atlas Fuel", "US", "card", None)], "closed_no_fraud"),
    ("account_takeover", 60 * 70, [(7400, "Selim Kaya", "TR", "wire", 4), (2100, "Selim Kaya", "TR", "wire", 4)], "refund_approval"),
    ("card_fraud", 60 * 90, [(640, "LuxeGoods Outlet", "GB", "card", 1), (720, "LuxeGoods Outlet", "GB", "card", 1)], "refunded"),
    ("account_takeover", 60 * 130, [(6800, "Priority Crypto Desk", "LT", "wire", 5)], "refunded"),
    ("card_fraud", 60 * 100, [(240, "Corner Cafe", "US", "card", None), (180, "Lakeside Fitness", "US", "card", None)], "declined"),
]


def _plant_fraud(g: Gen, victims: list[Party]) -> list[tuple]:
    planted = []
    for p, (scenario, mins, items, stage) in zip(victims, FRAUD):
        base = g.end - timedelta(minutes=mins)
        txns = []
        for k, (amount, payee, country, channel, hour) in enumerate(items):
            ts = base + timedelta(minutes=4 * k) if hour is None or mins < 120 else (base - timedelta(days=0)).replace(hour=hour, minute=10 + 6 * k)
            txns.append(g.txn(p, ts, amount, "out", channel, payee, country))
        planted.append((p, scenario, txns, stage))
    return planted


def _seed_fraud(d: Desk, planted: list[tuple]) -> None:
    s, g = d.s, d.g
    for n, (p, scenario, txns, stage) in enumerate(planted):
        total = sum(t.amount for t in txns)
        last = max(t.ts for t in txns)
        text = (f"Customer reported {len(txns)} payment(s) totalling ${total:,.0f} as not made by them."
                if scenario == "card_fraud" else
                f"Login from a new device was followed by {len(txns)} payment(s) totalling ${total:,.0f} to a new payee.")
        _, c = ingest_external_alert(s, source="fraud-engine", external_id=f"FR-{88000 + n}", scenario=scenario, party_id=p.id,
                                     score=0.9 if scenario == "account_takeover" else 0.7, explanation=text,
                                     txn_ids=[t.id for t in txns], created=last + timedelta(minutes=12))
        investigate(s, c, ts=c.created + timedelta(minutes=1))
        if stage == "triage":
            continue
        t0 = c.created + timedelta(minutes=25)
        if stage == "closed_no_fraud":
            d.own(c, "fran", t0)
            transition(s, c, "closed_no_fraud", PERSONAS["fran"], "Customer recognised both payments after speaking with a family member.", ts=t0 + timedelta(hours=2))
            continue
        if stage == "customer_contact":
            d.own(c, "fran", t0)
            complete_stage_tasks(s, c, PERSONAS["fran"], ts=t0)
            d.ask(c, "fran", "customer", "Please confirm whether you shared a one-time passcode or approved a new device.", t0 + timedelta(minutes=10), due_days=2)
            transition(s, c, "customer_contact", PERSONAS["fran"], ts=t0 + timedelta(minutes=15))
            continue
        t = d.step(c, "investigation", "fran", t0)
        if stage == "declined":
            d.step(c, "declined", "fran", t + timedelta(hours=20), "Payments were chip-and-PIN at merchants the customer uses weekly; device and location match.")
        elif total >= 5000:
            t = d.step(c, "refund_approval", "fran", t + timedelta(hours=18), "Device and location do not match the customer; credentials were phished.")
            if stage == "refunded":
                d.own(c, "tara", t + timedelta(hours=2))
                transition(s, c, "refunded", PERSONAS["tara"], "Approved. Liability sits with the bank under the unauthorised payment rules.", ts=t + timedelta(hours=6))
        else:
            d.step(c, "refunded", "fran", t + timedelta(hours=16), "Card was used online at a merchant with no prior history while the customer was travelling.")


def write_sample_feed(path: Path, end: datetime, seed: int = 11) -> int:
    """A 'legacy core' extract with awkward column names, source code lists and a few bad rows."""
    r = random.Random(seed)
    rows = []

    def add(cust, ts, amt, drcr, chnl, cp="", ctry="US"):
        rows.append({"TXN_REF": f"LC{len(rows) + 1:07d}", "CUST_NO": cust, "ACCT_NBR": f"ACC-{cust[3:]}", "TXN_DT": ts,
                     "TXN_AMT": f"{amt:.2f}", "CCY": "USD", "DR_CR_IND": drcr, "CHNL": chnl, "CNTRPRTY_NM": cp, "CNTRY_CD": ctry,
                     "BRANCH_CD": f"BR{r.randint(1, 40):03d}"})

    custs = [f"LC-{70000 + i}" for i in range(40)]
    for c in custs:
        for d in range(45):
            day = end - timedelta(days=d, hours=r.randint(1, 10))
            if d % 14 == 0:
                add(c, day, r.uniform(2400, 5800), "CR", "ACH", r.choice(EMPLOYERS))
            if r.random() < 0.7:
                add(c, day, r.uniform(8, 220), "DR", r.choice(["CRD", "ACH", "ZEL"]), r.choice(MERCHANTS))
    for c in custs[:2]:  # structuring
        start = end - timedelta(days=r.randint(2, 5))
        for i in range(5):
            add(c, start + timedelta(hours=i * 20), r.uniform(8300, 9900), "CR", "CSH")
    start = end - timedelta(days=3)  # pass-through
    add(custs[2], start, 64000, "CR", "WIR", "Bluepeak Holdings SA", "CY")
    for i in range(3):
        add(custs[2], start + timedelta(hours=5 + i * 8), 20600, "DR", "WIR", f"{r.choice(FIRST)} {r.choice(LAST)}", "AE")
    over = max(max(x["TXN_DT"] for x in rows) - end + timedelta(minutes=5), timedelta(0))
    for x in rows:
        x["TXN_DT"] = (x["TXN_DT"] - over).strftime("%Y%m%d%H%M%S")
    r.shuffle(rows)
    for i in r.sample(range(len(rows)), 9):  # data-quality defects
        rows[i]["TXN_AMT" if i % 3 else "DR_CR_IND"] = "" if i % 3 else "XX"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def seed(s: Session, n_baseline: int = 260) -> dict:
    g = Gen(s)
    ordinary = []
    for _ in range(n_baseline):
        p = g.party()
        g.baseline(p)
        ordinary.append(p)
    for _ in range(9):
        g.structuring(1, 40)
    for _ in range(4):
        g.structuring(2, 40, benign=True)
    for _ in range(8):
        g.rapid(1, 40)
    for _ in range(4):
        g.rapid(2, 40, benign=True)
    for _ in range(6):
        g.geo(1, 40)
    g.sanctions(1, 3, "Volkov Maritime Trading", "RU")
    g.sanctions(8, 30, "Darya Shipping Lines", "IR")
    for name in ["Volkov Maritime Trading Co", "Darya Shipping Line LLC", "Zarin Exchange Houses", "Northgate Petrochem FZC"]:
        g.sanctions(1, 35, name, g.r.choice(["DE", "SG", "AE", "GB"]))
    g.settle()
    planted = _plant_fraud(g, g.r.sample(ordinary, len(FRAUD)))  # after settling, so "35 minutes ago" stays 35 minutes ago
    s.flush()
    alerts = run_detection(s)
    cases = route_alerts(s, alerts)
    for c in cases:
        investigate(s, c, ts=c.created + timedelta(minutes=2))
    desk = Desk(s, g)
    _work_monitoring(desk, sorted(cases, key=lambda c: c.created))
    _seed_fraud(desk, planted)
    _seed_kyc(desk)
    s.flush()
    _work_qa(desk)
    sweep = run_timers(s, at=g.end)
    n = write_sample_feed(settings.samples_dir / "legacy_core_transactions.csv", g.end)
    total = len(list(s.scalars(select(Case.id))))
    return {"parties": g.n, "alerts": len(alerts) + len(planted), "cases": total, "stage_breaches": sweep["stage_breaches"],
            "sample_feed_rows": n}


def main() -> None:
    Base.metadata.drop_all(engine)
    ensure_schema()
    with session_scope() as s:
        s.execute(delete(WorkflowVersion))
        reset_published()
        print("Seeded:", seed(s))


if __name__ == "__main__":
    main()
