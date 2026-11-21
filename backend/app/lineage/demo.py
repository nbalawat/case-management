"""Synthetic lineage history for the demo: a month of the jobs a client's data has been through, in proportion to the
data the demo actually holds. Marked as demo history in each event. Real runs (a pipeline run from the Data page,
the stream consumers, the lake writer, a backup) add real events beside it."""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..backbone import topics
from ..domain.models import Account, Alert, Case, Party, Transaction
from ..tenancy import tenant_of
from .events import Dataset, files, lake_table, record, table, topic

DAYS = 30


def seed(s: Session, end: datetime, landing_url: str) -> int:
    tenant = tenant_of(s)
    r = random.Random(f"lineage:{tenant}")
    count = {m: s.scalar(select(func.count()).select_from(m)) or 0 for m in (Party, Account, Transaction, Alert, Case)}
    per_day = max(1, count[Transaction] // DAYS)
    landing = {d: files(landing_url, f"core-banking/{d}") for d in ("customers", "accounts", "transactions")}
    n = 0

    def run(job: str, at: datetime, inputs: list[Dataset], outputs: list[Dataset], **kw) -> None:
        nonlocal n
        record(s, job, inputs=inputs, outputs=outputs, run_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"caseforge:demo:{tenant}:{job}:{at.isoformat()}")),
               started=at - timedelta(seconds=r.randint(4, 90)), ended=at, extra={"demo": True} | kw.pop("extra", {}), transmit=False, **kw)
        n += 1

    for day in range(DAYS, 0, -1):
        at = (end - timedelta(days=day)).replace(hour=2, minute=0, second=0, microsecond=0)
        new_customers = max(1, count[Party] // 200 + r.randint(-1, 2))
        run("pipeline.Core banking customers", at, [landing["customers"]], [table(tenant, "party", rows=new_customers)])
        run("pipeline.Core banking accounts", at + timedelta(minutes=3), [landing["accounts"]], [table(tenant, "account", rows=new_customers)])
        txns = max(1, int(per_day * r.uniform(0.7, 1.3)))
        alerts = max(0, int(count[Alert] / DAYS * r.uniform(0.4, 1.6)))
        if day == 9:  # one night the landing zone refused the platform's credentials, and the run says so
            run("pipeline.Core banking transactions", at + timedelta(minutes=6), [landing["transactions"]], [], state="FAIL",
                error="Access denied listing core-banking/transactions: the credentials for this location were rotated")
            continue
        outs = [table(tenant, "txn", rows=txns), table(tenant, "alert", rows=alerts)]
        if alerts:
            outs.append(table(tenant, "case", rows=max(1, alerts // 2)))
        run("pipeline.Core banking transactions", at + timedelta(minutes=6), [landing["transactions"]], outs)
        streamed = int(txns * r.uniform(0.2, 0.4))
        run("stream.transactions", at + timedelta(hours=12), [topic(tenant, topics.TRANSACTIONS)],
            [table(tenant, "txn", rows=streamed), table(tenant, "alert", rows=r.randint(0, 2))], job_type="STREAMING")
        run("lake.writer", at + timedelta(hours=12, minutes=1), [topic(tenant, topics.TRANSACTIONS), topic(tenant, topics.ALERTS), topic(tenant, topics.CASE_EVENTS)],
            [lake_table(tenant, "transactions", rows=txns + streamed), lake_table(tenant, "alerts", rows=alerts),
             lake_table(tenant, "case_events", rows=alerts * 6 + r.randint(10, 60))], job_type="STREAMING")
        if day % 7 == 0:
            names = ("party", "account", "txn", "alert", "case", "case_event")
            run("backup.create", at + timedelta(hours=1), [table(tenant, x) for x in names],
                [Dataset("file://caseforge-data", "backups", "backup", rows=sum(count.values()) * 3)], extra={"scope": tenant})
    return n
