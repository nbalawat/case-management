"""The platform's own jobs described as lineage: what each one reads and writes. Each function is called where the
job finishes and never raises, because lineage must not stop the work it describes."""
from __future__ import annotations

import logging
from functools import wraps

from sqlalchemy.orm import Session

from .events import Dataset, files, lake_table, record, table, topic
from .tally import Tally

log = logging.getLogger("caseforge.lineage")

# Which operational table each file dataset loads, and each table's columns worth naming in a catalog.
LOADS = {"customers": "party", "accounts": "account", "transactions": "txn"}


def _quiet(fn):
    @wraps(fn)
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            log.warning("Lineage for %s not recorded: %s", fn.__name__, e)
            return None
    return run


@_quiet
def pipeline_run(s: Session, source, run, totals: dict, store_url: str | None) -> dict | None:
    """A file pipeline run: objects under the source's prefix into an operational table, then alerts and cases."""
    if run.status in ("nothing_new", "skipped"):
        return None
    tenant = run.tenant_id
    inputs = [files(store_url, source.prefix)] if store_url else [Dataset(f"storage://{source.root}", source.prefix or "/", "files")]
    outputs = [table(tenant, LOADS.get(source.dataset, source.dataset), rows=totals.get("loaded", 0) + totals.get("updated", 0))]
    if totals.get("alerts"):
        outputs.append(table(tenant, "alert", rows=totals["alerts"]))
    if totals.get("cases"):
        outputs.append(table(tenant, "case", rows=totals["cases"]))
    if totals.get("rejected") or totals.get("objects_quarantined"):
        outputs.append(Dataset("quarantine://", f"{tenant}/{source.id}", "files", rows=totals.get("rejected", 0)))
    failed = run.status == "failed"
    return record(s, f"pipeline.{source.name}", inputs=inputs, outputs=outputs, state="FAIL" if failed else "COMPLETE",
                  run_id=_uuid(run.id), started=run.started, ended=run.finished, error=run.error if failed else None,
                  extra={"pipelineRun": run.id, "status": run.status, "source": source.id, "dataset": source.dataset})


@_quiet
def lake_backfill(s: Session, tenant_id: str, written: int) -> dict | None:
    return record(s, "lake.backfill", inputs=[table(tenant_id, "txn")], outputs=[lake_table(tenant_id, "transactions", rows=written)])


@_quiet
def backup(s: Session, tenant_id: str, tables: dict[str, int], target: Dataset, backup_id: str) -> dict | None:
    return record(s, "backup.create", inputs=[table(tenant_id, name) for name in tables], outputs=[Dataset(target.namespace, target.name, "backup", rows=sum(tables.values()))],
                  extra={"backup": backup_id})


def stream_tally(stream: str) -> Tally:
    return Tally(f"stream.{stream}")


def stream_batch(tally: Tally, tenant_id: str, stream: str, out) -> None:
    """A stream consumer's micro-batch: a client's topic into the operational store, plus the alerts it raised."""
    from ..backbone import topics

    target = "txn" if stream == topics.TRANSACTIONS else "alert"
    outputs = [(table(tenant_id, target), out.loaded)]
    if stream == topics.TRANSACTIONS:
        outputs.append((table(tenant_id, "alert"), out.alerts))
    tally.add(tenant_id, [topic(tenant_id, stream)], outputs)


def lake_batch(tally: Tally, tenant_id: str, name: str, kind: str, rows: int) -> None:
    tally.add(tenant_id, [topic(tenant_id, kind)], [(lake_table(tenant_id, name), rows)])


@_quiet
def flush_if_due(tally: Tally) -> int:
    return tally.flush() if tally.due() else 0


def _uuid(text: str) -> str:
    """OpenLineage run ids are UUIDs; derive a stable one from the platform's own run id."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"caseforge:{text}"))
