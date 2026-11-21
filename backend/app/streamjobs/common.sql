-- What every stream job starts with: its name on the cluster, the client's transaction topic and its alerts-in topic.
-- Written in front of each job when it is rendered. Keep semicolons out of these comments.

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
  counterparty STRING,
  counterparty_country STRING,
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
