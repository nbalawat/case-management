# Stream detection on Apache Flink

The application runs its scenarios itself: after each file load, and after each micro-batch from a client's
`transactions` topic, re-reading the touched customers' recent history from the database. That is correct at any
volume and comfortable at moderate ones. A rule with a time window can instead run on a stream processor, which
keeps each customer's open pattern in state and scales by partition. The jobs for that live in
`backend/app/streamjobs/`, as Flink SQL.

A job reads one client's `caseforge.<client>.transactions` topic and writes canonical alerts to its
`caseforge.<client>.alerts-in` topic. From there they take the same road as an alert from any other engine: the alert
stream consumer de-duplicates them by source and id, groups them into cases and starts the investigation. Nothing
downstream knows which engine raised an alert.

| Job | The rule | Alerts carry the source |
|---|---|---|
| `structuring` | A number of cash deposits just under the reporting threshold by one customer inside a window of days. The same rule and the same thresholds as the application's `structuring` scenario. | `flink-structuring` |

One job per client and rule, like the topics. Its name on the cluster is `caseforge structuring <client>`.

## Render and submit

A job is rendered for a client, never edited by hand. Rendering writes in the client's topics and the thresholds
that client has approved under Detection, and refuses any value that could change the SQL.

    cd backend
    uv run python -m app.streamjobs structuring meridian --bootstrap kafka:9092 > meridian.sql
    $FLINK_HOME/bin/sql-client.sh -f meridian.sql

`--bootstrap` is the brokers as the Flink cluster reaches them; without it they come from `EVENT_BUS_URL`.
`--defaults` uses the scenario's shipped thresholds and opens no database. The cluster needs
`flink-sql-connector-kafka` in its `lib` directory (the `Dockerfile` here adds it to the official image).

With Docker: `make streaming` starts the demo with Kafka, the lake, OpenSearch and a Flink cluster, and
`make flink-job CLIENT=meridian` renders and submits that client's job. Flink's own console is on
http://localhost:8081.

Set `FLINK_URL` (the cluster's REST address) on the application and the Integrations page shows each client whether
its job is running, and says so when the application is raising the same rule as well.

## Things to know before relying on it

- **Switch the rule off in the application for a client whose job is running.** Otherwise both raise it. That is
  double work, not double cases (both alerts join the customer's one case), but the alert counts are inflated.
  It is switched off under Detection, as a governed change with a second approver. Switching it off also stops it
  for file loads, so a client that loads files and streams should think about which it wants.
- **A change to the thresholds does not reach a running job.** Render again, cancel the old job and submit the new.
  Nothing does this for you yet.
- **Event time moves only when traffic does.** An alert is released once a later transaction, an hour or more
  after the last deposit, has arrived on the topic. On a busy topic that is immediate; on a silent one it waits.
- **A payment that arrives after event time has passed it by more than an hour is not counted by the job.** The
  application still loads it, and the application's own scenario would still see it.
- **The job alerts on exactly the count.** Three deposits raise an alert; a fourth and fifth are the start of the
  next pattern. The application's scenario, which looks back over history, would put all five in one alert.
- **Records must be in the canonical stream format**: plain JSON with `txn_id`, `party_id`, `ts` (ISO-8601, UTC),
  `amount`, `direction` (`in`/`out`) and `channel`. The stream consumer also accepts a CloudEvents wrapper and source
  codes such as `CR`; this job does not, and skips what it cannot read rather than dead-lettering it.
- **State is in memory unless you configure a state backend and checkpoint storage** on the cluster. The job asks
  for a checkpoint every 30 seconds; where they go is the cluster's configuration.
- A restarted job that re-reads the topic writes the same alerts again with the same ids, and the alert stream
  drops them.

## What has and has not been run

Run for real: the rendered job on a local Flink 2.1.3 cluster with the Kafka connector 5.0.0-2.1 against an Apache
Kafka 3.9.1 broker, through to an open case (`backend/tests/test_flink.py`, seven tests, set `TEST_KAFKA_URL`,
`TEST_FLINK_URL` and `TEST_FLINK_HOME`).

Never run: the Docker image and Compose services here, Flink on Kubernetes, a managed Flink service, checkpoints
to object storage, recovery from a failure, or any volume beyond a handful of records. These tests are not in the
CI workflow.
