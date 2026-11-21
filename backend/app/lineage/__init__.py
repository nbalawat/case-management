"""Data lineage as OpenLineage events: which job read which datasets and wrote which, with how many rows.

OpenLineage is the open standard (LF AI & Data) that Marquez, DataHub, OpenMetadata, Atlan, Microsoft Purview and
Google Dataplex read, so a client's catalog can show where every alert came from without this platform knowing
which catalog it is. Events are kept for the in-app lineage view and, when LINEAGE_URL is set, sent there too.

    record(s, job, inputs, outputs, ...)   one finished run of a job (a pipeline run, a backfill, a backup)
    Tally                                   continuous jobs (stream consumers, the lake writer): counts summed and
                                            recorded once a minute rather than for every micro-batch
    graph(s, days)                          datasets and jobs as nodes, reads and writes as edges, for the UI

Lineage never stops the work it describes: an event that cannot be sent is logged and dropped.
"""
from .events import Dataset, record
from .tally import Tally

__all__ = ["Dataset", "record", "Tally"]
