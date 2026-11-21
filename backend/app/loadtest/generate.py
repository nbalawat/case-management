"""Synthetic customers and transactions, deterministic for a given seed so two runs load the same data.

A small share of customers behave suspiciously (cash just under the reporting threshold, wires to higher-risk
countries), so detection does real work at a rate a monitoring team would recognise rather than none at all.
"""
from __future__ import annotations

import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

CHANNELS = ("card", "card", "card", "ach", "ach", "p2p", "wire", "cash")
COUNTERPARTIES = ("Harbor Grocers", "Northline Utilities", "Metro Transit", "Bluewater Payroll", "Atlas Insurance",
                  "Cedar Pharmacy", "Summit Fuel", "Orchard Rentals", "Lumen Telecom", "Riverside Dental")
HIGH_RISK = ("IR", "KP", "SY", "MM", "AF")


@dataclass(frozen=True)
class Population:
    """Who the transactions belong to. Ids carry a prefix so a load-test client can never collide with real ones."""

    customers: int
    seed: int = 7
    suspicious_share: float = 0.01
    prefix: str = "LT"

    def customer_id(self, n: int) -> str:
        return f"{self.prefix}-C{n:08d}"

    def account_id(self, n: int) -> str:
        return f"{self.prefix}-A{n:08d}"

    def suspicious(self, n: int) -> bool:
        return random.Random(self.seed * 1_000_003 + n).random() < self.suspicious_share

    def customer_rows(self) -> Iterator[tuple[dict, dict]]:
        """(party, account) column values for every customer, ready for a bulk insert."""
        r = random.Random(self.seed)
        for n in range(self.customers):
            risk = "high" if self.suspicious(n) else r.choice(("low", "low", "low", "medium"))
            yield ({"id": self.customer_id(n), "name": f"Load test customer {n}", "risk_rating": risk, "country": "US"},
                   {"id": self.account_id(n), "party_id": self.customer_id(n)})


def transactions(pop: Population, count: int, *, run: str, start: datetime, days: int = 30, offset: int = 0) -> Iterator[dict]:
    """`count` canonical transactions spread over `days` before `start`. Ids are unique per run."""
    r = random.Random(f"{pop.seed}:{run}:{offset}")
    span = days * 86_400
    for i in range(offset, offset + count):
        n = r.randrange(pop.customers)
        bad = pop.suspicious(n)
        channel = "cash" if bad and r.random() < 0.6 else r.choice(CHANNELS)
        if bad and channel == "cash":
            amount = round(r.uniform(8_500, 9_950), 2)
        elif channel == "wire":
            amount = round(r.lognormvariate(8.0, 1.0), 2)
        else:
            amount = round(r.lognormvariate(4.0, 1.1), 2)
        country = r.choice(HIGH_RISK) if bad and channel == "wire" else "US"
        yield {"txn_id": f"{pop.prefix}-{run}-{i:010d}", "account_id": pop.account_id(n), "party_id": pop.customer_id(n),
               "ts": (start - timedelta(seconds=r.randrange(span))).strftime("%Y-%m-%dT%H:%M:%S"), "amount": f"{amount:.2f}",
               "currency": "USD", "direction": "in" if bad and channel == "cash" else r.choice(("in", "out", "out")),
               "channel": channel, "counterparty": r.choice(COUNTERPARTIES), "counterparty_country": country}


def encode(t: dict) -> tuple[str, bytes]:
    """(key, value) for the bus: keyed by customer, so one customer's payments stay in order on one partition."""
    return t["party_id"], json.dumps(t, separators=(",", ":")).encode()
