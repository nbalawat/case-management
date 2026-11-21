"""Synthetic demo data with planted typologies. All people, companies and activity are fictional."""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agents.investigator import investigate
from .alerts.hub import route_alerts
from .cases.case_types import load_case_types
from .cases.service import create_case, save_narrative, transition
from .config import settings
from .db import Base, engine, init_db, session_scope
from .detection.engine import run_detection
from .domain.models import Account, Case, Party, Transaction, now
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


def _simulate_handling(s: Session, cases: list[Case], g: Gen) -> None:
    """Walk older cases through their workflow as the demo personas would, mostly following the agent."""
    ana, ivan, maya = PERSONAS["ana"], PERSONAS["ivan"], PERSONAS["maya"]
    for c in cases:
        age = (g.end - c.created).days
        if age < 6 or g.r.random() < 0.12:
            if age >= 3 and c.case_type == "tm_alert_investigation" and g.r.random() < 0.6:
                transition(s, c, "investigation", ana, ts=c.created + timedelta(hours=g.r.randint(4, 30)))
                c.owner = "ivan"
            continue
        follow = g.r.random() < 0.88
        escalate = (c.agent_recommendation == "escalate") == follow
        t = c.created + timedelta(hours=g.r.randint(3, 20))
        if c.case_type == "sanctions_hit":
            if escalate:
                transition(s, c, "escalated", ana, ts=t)
                transition(s, c, "blocked", maya, "Match confirmed on name and jurisdiction; payment blocked.", ts=t + timedelta(hours=5))
            else:
                transition(s, c, "discounted", ana, "Name differs; no secondary identifier matches.", ts=t)
            continue
        c.owner = "ivan"
        transition(s, c, "investigation", ana, ts=t)
        t += timedelta(days=g.r.randint(1, 4))
        if not escalate:
            transition(s, c, "closed_no_sar", ivan, "Activity consistent with customer profile and documented purpose.", ts=t)
            continue
        transition(s, c, "sar_drafting", ivan, ts=t)
        save_narrative(s, c, c.narrative, ivan, ts=t + timedelta(hours=3))
        transition(s, c, "mlro_review", ivan, ts=t + timedelta(hours=4))
        if age >= 9:
            transition(s, c, "filed", maya, ts=t + timedelta(days=g.r.randint(1, 3)))


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
    for _ in range(n_baseline):
        g.baseline(g.party())
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
    s.flush()
    alerts = run_detection(s)
    cases = route_alerts(s, alerts)
    for c in cases:
        investigate(s, c, ts=c.created + timedelta(minutes=2))
    kyc = load_case_types()["kyc_refresh"]
    for p in g.r.sample(list(s.scalars(select(Party).where(Party.risk_rating == "low").limit(60))), 6):
        create_case(s, kyc, p.id, "low", created=g.when(1, 20))
    _simulate_handling(s, sorted(cases, key=lambda c: c.created), g)
    n = write_sample_feed(settings.samples_dir / "legacy_core_transactions.csv", g.end)
    return {"parties": g.n, "alerts": len(alerts), "cases": len(cases), "sample_feed_rows": n}


def main() -> None:
    Base.metadata.drop_all(engine)
    init_db()
    with session_scope() as s:
        print("Seeded:", seed(s))


if __name__ == "__main__":
    main()
