# Data lineage

Every job that moves a client's data records what it read, what it wrote and how many rows, as an
[OpenLineage](https://openlineage.io) run event (spec 2-0-2). OpenLineage is the open standard that Marquez, DataHub,
OpenMetadata, Atlan, Microsoft Purview and Google Dataplex read, so a client's own catalog can show where every alert
came from without the platform knowing which catalog that is.

## What records lineage

| Job | Reads | Writes | When |
|---|---|---|---|
| `pipeline.<source name>` | the source's files (`s3://bucket`, `abfss://container@account`, `gs://bucket`) | `party`, `account` or `txn`, plus `alert`, `case` and the quarantine area when they apply | each run that found new files, including failed runs (`FAIL`, with the reason) |
| `stream.transactions`, `stream.alerts-in` | the client's Kafka topic | `txn` and `alert`, or `alert` | summed, one event per client a minute |
| `lake.writer` | the client's topics | the Iceberg tables | summed, one event per client a minute |
| `lake.backfill` | `txn` | the Iceberg `transactions` table | each backfill that wrote rows |
| `backup.create` | every table in the client's backup | the backup location | each client backup |

Streaming jobs are summed rather than recorded per micro-batch: a catalog needs "this job reads that topic and writes
those tables, this many rows", not an event every half second.

Dataset names never carry credentials or a server's layout: an object store is named by scheme and bucket (for
Azure, `container@account`), a local directory by the platform's own folder (`file://caseforge-lake`).

## Where it goes

- Each event is kept in the `lineage_event` table, per client, and drawn on **Oversight → Data lineage**: datasets
  and jobs left to right from where data arrives to where it ends up, with rows written over 7, 30 or 90 days. Choosing
  a node lights up everything upstream and downstream of it and lists the runs that touched it, each with its raw
  OpenLineage event. The API is `/api/lineage/graph`, `/api/lineage/events` and `/api/lineage/events/{id}`.
- With `LINEAGE_URL` set, each event is also sent there (see [configuration](configuration.md#lineage)). Sending
  never stops the job: an event that cannot be sent is logged and dropped, and the copy in the app remains.

## Marquez on a laptop

`make lineage` starts everything in `make full` plus [Marquez](https://marquezproject.ai) and sends every event to
it. The Marquez UI is on http://localhost:3001 and its API on http://localhost:5050 (5000 is taken by AirPlay on a
Mac). Run a pipeline from the Data page, then open the `caseforge.<client>` namespace in Marquez.

Checked in a cloud container: Marquez 0.51.1 accepted pipeline events sent by the app, and listed the three jobs as
`COMPLETED`, with their output tables.

## Demo history

The demo seed writes a month of synthetic lineage for each client, sized to the data the demo holds: nightly file
pipelines (one failed night, when the landing zone refused rotated credentials), the stream consumer, the lake writer
and weekly backups. Each of those events carries `"demo": true` in its `caseforge` run facet and is never sent to
`LINEAGE_URL`.
