"""Building OpenLineage run events, keeping them, and sending them on."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..tenancy import tenant_of

log = logging.getLogger("caseforge.lineage")
SPEC = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
PRODUCER = "https://github.com/nbalawat/case-management"
FACET = "https://openlineage.io/spec/facets/1-0-1/{}.json#/$defs/{}"


@dataclass(frozen=True)
class Dataset:
    """A dataset as OpenLineage names it: a namespace saying where it lives, and a name within it."""

    namespace: str
    name: str
    kind: str = "table"  # table | files | topic | lake | backup: how the UI draws it
    fields: tuple[str, ...] = ()
    rows: int | None = None  # rows written, for an output

    def as_event(self, output: bool) -> dict:
        d: dict = {"namespace": self.namespace, "name": self.name, "facets": {}}
        if self.fields:
            d["facets"]["schema"] = {"_producer": PRODUCER, "_schemaURL": FACET.format("SchemaDatasetFacet", "SchemaDatasetFacet"),
                                     "fields": [{"name": f} for f in self.fields]}
        if output and self.rows is not None:
            d["outputFacets"] = {"outputStatistics": {"_producer": PRODUCER, "rowCount": self.rows,
                                                      "_schemaURL": FACET.format("OutputStatisticsOutputDatasetFacet", "OutputStatisticsOutputDatasetFacet")}}
        return d


# Where the platform's own datasets live, named the way OpenLineage names databases, topics and object stores.
def table(tenant_id: str, name: str, fields: tuple[str, ...] = (), rows: int | None = None) -> Dataset:
    return Dataset(f"caseforge://{tenant_id}", name, "table", fields, rows)


def topic(tenant_id: str, kind: str) -> Dataset:
    from ..backbone import topics

    url = settings.event_bus_url or ""
    host = url.removeprefix("kafka://").split(",")[0].split("@")[-1] if url.startswith("kafka://") else "local"
    return Dataset(f"kafka://{host}", topics.topic(tenant_id, kind), "topic")


def lake_table(tenant_id: str, name: str, rows: int | None = None) -> Dataset:
    return Dataset(f"iceberg://caseforge_{tenant_id.replace('-', '_')}", name, "lake", rows=rows)


def files(url: str, key: str = "") -> Dataset:
    """Objects in a store, named as OpenLineage names them: the namespace is the scheme and bucket (for Azure, the
    container@account), never credentials; the name is the path. A local directory keeps only its path."""
    from urllib.parse import urlsplit

    u = urlsplit(url)
    host = u.hostname or ""
    if host and u.username and u.password is None and u.scheme in ("abfs", "abfss", "wasb", "wasbs"):
        host = f"{u.username}@{host}"  # the container, which is part of where the data lives, not a secret
    namespace = f"{u.scheme}://{host}"
    path = "/".join(p for p in (u.path.strip("/"), key.strip("/")) if p)
    if u.scheme == "file":  # a directory on the server: named from the platform's own data folders, not the server's layout
        namespace, path = _local(path)
    return Dataset(namespace, path or "/", "files")


def _local(path: str) -> tuple[str, str]:
    from pathlib import PurePosixPath

    from ..config import ROOT

    p = PurePosixPath("/" + path)
    for name, base in (("lake", settings.lake_dir), ("data", ROOT / "data")):
        try:
            return f"file://caseforge-{name}", str(p.relative_to(PurePosixPath(str(base))))
        except ValueError:
            continue
    return "file://", path


def job_namespace(tenant_id: str) -> str:
    return f"caseforge.{tenant_id}"


def build(tenant_id: str, job: str, *, inputs: list[Dataset], outputs: list[Dataset], state: str = "COMPLETE", run_id: str | None = None,
          started: datetime | None = None, ended: datetime | None = None, job_type: str = "BATCH", error: str | None = None,
          extra: dict | None = None) -> dict:
    ended = ended or datetime.now(timezone.utc)
    run: dict = {"runId": run_id or str(uuid.uuid4()), "facets": {}}
    if started is not None:
        run["facets"]["nominalTime"] = {"_producer": PRODUCER, "_schemaURL": FACET.format("NominalTimeRunFacet", "NominalTimeRunFacet"),
                                        "nominalStartTime": _iso(started), "nominalEndTime": _iso(ended)}
    if error:
        run["facets"]["errorMessage"] = {"_producer": PRODUCER, "_schemaURL": FACET.format("ErrorMessageRunFacet", "ErrorMessageRunFacet"),
                                         "message": error[:2000], "programmingLanguage": "python"}
    if extra:
        run["facets"]["caseforge"] = {"_producer": PRODUCER, "_schemaURL": f"{PRODUCER}/lineage/caseforge-run-facet.json", **extra}
    return {"eventType": state, "eventTime": _iso(ended), "producer": PRODUCER, "schemaURL": SPEC, "run": run,
            "job": {"namespace": job_namespace(tenant_id), "name": job,
                    "facets": {"jobType": {"_producer": PRODUCER, "_schemaURL": FACET.format("JobTypeJobFacet", "JobTypeJobFacet").replace("1-0-1", "2-0-2"),
                                           "processingType": job_type, "integration": "CASEFORGE", "jobType": "JOB"}}},
            "inputs": [d.as_event(False) for d in inputs], "outputs": [d.as_event(True) for d in outputs]}


def _iso(t: datetime) -> str:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record(s: Session, job: str, *, inputs: list[Dataset], outputs: list[Dataset], state: str = "COMPLETE", transmit: bool = True, **kw) -> dict:
    """Keep one run event for this client and, unless `transmit` is off (demo history), send it on. Returns the event."""
    from ..domain.models import LineageEvent

    tenant = tenant_of(s)
    ev = build(tenant, job, inputs=inputs, outputs=outputs, state=state, **kw)
    s.add(LineageEvent(job=job, run_id=ev["run"]["runId"], state=state, at=datetime.fromisoformat(ev["eventTime"].replace("Z", "+00:00")).replace(tzinfo=None),
                       inputs=[{"namespace": d.namespace, "name": d.name, "kind": d.kind} for d in inputs],
                       outputs=[{"namespace": d.namespace, "name": d.name, "kind": d.kind, "rows": d.rows} for d in outputs],
                       event=ev))
    if transmit:
        from .transport import send

        send(ev)
    return ev
