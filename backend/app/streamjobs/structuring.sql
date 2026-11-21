-- Cash structuring, as a Flink SQL job over one client's transaction topic.
--
-- The same rule as the application's `structuring` scenario (backend/app/detection/scenarios.py), with the client's
-- approved thresholds written in when the job is rendered: ${COUNT} cash deposits of ${MIN} to ${MAX} by one customer
-- within ${DAYS} days. Flink keeps each customer's open pattern in state, so it never re-reads history. An alert is
-- released once event time has passed the last deposit by the out-of-order allowance below, which needs later
-- traffic on the topic, and every further ${COUNT} deposits raise another. Alerts go to the client's alerts-in topic
-- in the canonical alert format, where the alert hub de-duplicates them by (source, external_id) and groups them
-- into cases.
--
-- Render it with:  python -m app.streamjobs structuring <client>      (never fill the placeholders in by hand)
-- Run it with:     bin/sql-client.sh -f <rendered file>               (needs flink-sql-connector-kafka in Flink's lib)
-- Keep semicolons out of these comments: the SQL client splits statements on them.

SET 'pipeline.name' = '${JOB_NAME}';
-- A quiet partition must not hold event time back.
SET 'table.exec.source.idle-timeout' = '10 s';
SET 'execution.checkpointing.interval' = '30 s';

CREATE TEMPORARY TABLE transactions (
  txn_id STRING,
  party_id STRING,
  ts STRING,                                      -- ISO-8601, as the stream contract says, read as written (UTC)
  amount DOUBLE,
  direction STRING,
  channel STRING,
  event_time AS TO_TIMESTAMP(REPLACE(SUBSTRING(ts FROM 1 FOR 19), 'T', ' ')),
  WATERMARK FOR event_time AS event_time - INTERVAL '1' HOUR   -- payments may arrive up to an hour out of order
) WITH (
  'connector' = 'kafka',
  'topic' = '${TRANSACTIONS}',
  'properties.bootstrap.servers' = '${BOOTSTRAP}',
  'properties.group.id' = '${GROUP}',
  'scan.startup.mode' = 'earliest-offset',
  'format' = 'json',
  'json.ignore-parse-errors' = 'true'             -- the stream consumer dead-letters bad records, this job skips them
);

CREATE TEMPORARY TABLE alerts_in (
  source STRING,
  external_id STRING,
  scenario STRING,
  party_id STRING,
  score DOUBLE,
  explanation STRING,
  txn_ids ARRAY<STRING>
) WITH (
  'connector' = 'kafka',
  'topic' = '${ALERTS_IN}',
  'properties.bootstrap.servers' = '${BOOTSTRAP}',
  'key.format' = 'raw',
  'key.fields' = 'party_id',                      -- one customer's alerts stay in order
  'value.format' = 'json'
);

INSERT INTO alerts_in
SELECT
  '${SOURCE}',
  CONCAT(party_id, ':', DATE_FORMAT(first_ts, 'yyyy-MM-dd''T''HH:mm:ss')),
  'structuring',
  party_id,
  LEAST(0.55 + 0.08 * n, 0.97),
  CONCAT(CAST(n AS STRING), ' cash deposits between $${MIN_TEXT} and $10,000 within ${DAYS} days, totalling $', CAST(CAST(total AS DECIMAL(18, 0)) AS STRING), '.'),
  SPLIT(ids, ',')
FROM (SELECT * FROM transactions WHERE channel = 'cash' AND direction = 'in' AND amount BETWEEN ${MIN} AND ${MAX} AND event_time IS NOT NULL)
MATCH_RECOGNIZE (
  PARTITION BY party_id
  ORDER BY event_time
  MEASURES
    FIRST(D.event_time) AS first_ts,
    COUNT(D.txn_id) AS n,
    SUM(D.amount) AS total,
    LISTAGG(D.txn_id, ',') AS ids
  ONE ROW PER MATCH
  AFTER MATCH SKIP PAST LAST ROW
  PATTERN (D{${COUNT}}) WITHIN INTERVAL '${DAYS}' DAY
  DEFINE D AS TRUE
);
