-- Rapid movement of funds, as a Flink SQL job over one client's transaction topic.
--
-- The same rule as the application's `rapid_movement` scenario (backend/app/detection/scenarios.py), with the
-- client's approved thresholds written in when the job is rendered: a payment in of at least ${MIN_IN} that is not
-- cash, followed within ${HOURS} hours by payments out that add up to at least ${RATIO} of it. Every such payment in
-- is followed on its own, so two large receipts close together are two alerts.
--
-- Where it differs from the application, on purpose: the alert is raised the moment the money sent out reaches the
-- share, and lists the payments out up to that point. The application reads the whole window afterwards and lists
-- every payment out in it. The alert is released once event time has passed the last payment by the out-of-order
-- allowance, which needs later traffic on the topic.
--
-- Render it with:  python -m app.streamjobs rapid_movement <client>   (never fill the placeholders in by hand)
-- Run it with:     bin/sql-client.sh -f <rendered file>               (needs flink-sql-connector-kafka in Flink's lib)
-- Keep semicolons out of these comments: the SQL client splits statements on them.

${COMMON}

INSERT INTO alerts_in
SELECT
  '${SOURCE}',
  CONCAT(party_id, ':', in_id),
  'rapid_movement',
  party_id,
  ROUND(LEAST(0.6 + moved / received * 0.2, 0.95), 2),
  CONCAT('$', CAST(CAST(received AS DECIMAL(18, 0)) AS STRING), ' received from ', COALESCE(NULLIF(payer, ''), 'unknown'), ', $',
         CAST(CAST(moved AS DECIMAL(18, 0)) AS STRING), ' (', CAST(CAST(moved / received * 100 AS DECIMAL(18, 0)) AS STRING),
         '%) sent out within ${HOURS} hours.'),
  SPLIT(ids, ',')
FROM (
  SELECT txn_id, party_id, event_time, amount, direction, channel, counterparty,
         CASE WHEN direction = 'out' THEN amount ELSE 0 END AS sent,
         CASE WHEN direction = 'out' THEN txn_id END AS out_id
  FROM transactions
  WHERE event_time IS NOT NULL AND amount > 0 AND direction IN ('in', 'out')
)
MATCH_RECOGNIZE (
  PARTITION BY party_id
  ORDER BY event_time
  MEASURES
    A.txn_id AS in_id,
    A.amount AS received,
    A.counterparty AS payer,
    COALESCE(SUM(B.sent), 0) + C.sent AS moved,
    CONCAT_WS(',', A.txn_id, LISTAGG(B.out_id, ','), C.txn_id) AS ids
  ONE ROW PER MATCH
  AFTER MATCH SKIP TO NEXT ROW
  PATTERN (A B* C) WITHIN INTERVAL '${HOURS}' HOUR
  DEFINE
    A AS A.direction = 'in' AND A.channel <> 'cash' AND A.amount >= ${MIN_IN},
    B AS COALESCE(SUM(B.sent), 0) < ${RATIO} * A.amount,
    C AS C.direction = 'out' AND COALESCE(SUM(B.sent), 0) + C.sent >= ${RATIO} * A.amount
);
