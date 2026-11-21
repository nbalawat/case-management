# Publishing the outbox with Debezium

The application writes every domain event to the `outbox_event` table in the same transaction as the change.
Something has to move those rows to Kafka. There are two choices, and they publish the same bytes to the same
topics under the same keys:

| | Built-in relay (`OUTBOX_PUBLISHER=relay`, the default) | Debezium (`OUTBOX_PUBLISHER=debezium`) |
|---|---|---|
| How | The worker polls the table and marks rows as published | Kafka Connect reads the Postgres log |
| Needs | Nothing beyond the worker | Postgres with `wal_level=logical`, a Kafka Connect worker with the Debezium Postgres connector |
| Order | By row id, which can differ from commit order when transactions overlap | Commit order |
| Database | Postgres or SQLite | Postgres only |

Run one or the other, not both: with `OUTBOX_PUBLISHER=debezium` the relay stands down and old rows are pruned by
age rather than by a published mark.

## Register the connector

`outbox-connector.json` is the connector's configuration. It reads the database address from the Connect worker's
environment (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`), so no password is stored in Kafka Connect's
configuration topic. The worker needs the environment config provider switched on:

    config.providers=env
    config.providers.env.class=org.apache.kafka.common.config.provider.EnvVarConfigProvider

Then, once the application has started at least once (the table must exist):

    curl -X PUT -H 'Content-Type: application/json' --data @outbox-connector.json \
      http://localhost:8083/connectors/caseforge-outbox/config

`make backbone-cdc` does all of this in Docker.

## Things to know

- `snapshot.mode` is `initial`: on first start the connector publishes every row already in the table, in id order,
  then follows the log. When taking over from the relay on a database that has already published, set it to
  `no_data` so history is not sent twice. Consumers drop repeats by the `id` header either way.
- The database user needs `REPLICATION` and ownership of `outbox_event` (to create the publication), or create the
  publication `caseforge_outbox` for that one table yourself and set `publication.autocreate.mode` to `disabled`.
- A replication slot that nobody reads holds the database log back. If the connector is retired, drop the slot
  `caseforge_outbox`.
- Only inserts are captured. The relay's "published" updates and the pruning deletes are ignored.

## What has been run

Against Postgres 16, Kafka 3.9.1 and Debezium 3.0.8 as plain processes: the snapshot and the streamed changes for
three seeded demo clients arrived on the right topics with the same key, value and headers the relay produces, and
in order per case (`backend/tests/test_cdc.py`). The Docker Compose services for it have not been started anywhere yet.
