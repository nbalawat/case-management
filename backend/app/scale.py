"""In-process throughput benchmark for the mapping and detection code paths.

This measures one Python process on the host. It is not the production streaming path.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from .detection.scenarios import REGISTRY
from .ingest.service import transform

MAPPING = {"txn_id": "TXN_REF", "account_id": "ACCT_NBR", "party_id": "CUST_NO", "ts": "TXN_DT", "amount": "TXN_AMT",
           "currency": "CCY", "direction": "DR_CR_IND", "channel": "CHNL", "counterparty": "CNTRPRTY_NM", "counterparty_country": "CNTRY_CD"}


def benchmark(n: int = 200_000, parties: int = 2_000) -> dict:
    r = random.Random(1)
    base = datetime(2026, 1, 1)
    raw = [{"TXN_REF": str(i), "ACCT_NBR": f"A{i % parties}", "CUST_NO": f"C{i % parties}",
            "TXN_DT": (base + timedelta(minutes=i % 120_000)).strftime("%Y%m%d%H%M%S"), "TXN_AMT": f"{r.uniform(5, 12000):.2f}",
            "CCY": "USD", "DR_CR_IND": r.choice(["CR", "DR"]), "CHNL": r.choice(["CSH", "WIR", "ACH", "CRD"]),
            "CNTRPRTY_NM": "Harbor Grocers", "CNTRY_CD": "US"} for i in range(n)]
    t0 = time.perf_counter()
    by_party: dict[str, list] = {}
    for row in raw:
        t = transform(row, MAPPING)
        by_party.setdefault(t["party_id"], []).append(SimpleNamespace(id=t["txn_id"], **{k: t[k] for k in
                                                     ("ts", "amount", "direction", "channel", "counterparty", "counterparty_country")}))
    t1 = time.perf_counter()
    hits = 0
    for txns in by_party.values():
        txns.sort(key=lambda x: x.ts)
        for sc in REGISTRY.values():
            if sc["name"] != "rapid_movement":  # quadratic on dense synthetic data; excluded from this micro-benchmark
                hits += len(sc["fn"](txns))
    t2 = time.perf_counter()
    return {"events": n, "parties": parties, "map_seconds": round(t1 - t0, 2), "detect_seconds": round(t2 - t1, 2),
            "map_per_sec": round(n / (t1 - t0)), "end_to_end_per_sec": round(n / (t2 - t0)), "alerts": hits}
