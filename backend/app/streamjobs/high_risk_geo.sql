-- Wires to high-risk jurisdictions, as a Flink SQL job over one client's transaction topic.
--
-- The same rule as the application's `high_risk_geo` scenario (backend/app/detection/scenarios.py), with the client's
-- approved threshold written in when the job is rendered: one customer's outbound wires to ${COUNTRIES_TEXT} adding up
-- to at least ${MIN_TOTAL} in a calendar month. Flink keeps a running total per customer and month, and raises the
-- alert on the wire that takes the total over the threshold, once per customer and month.
--
-- Where it differs from the application, on purpose: the alert lists the wires up to the one that crossed the
-- threshold. The application reads the month afterwards and lists every wire in it. Running totals are dropped
-- after 40 days, so a wire that arrives more than a month late starts a new total.
--
-- Render it with:  python -m app.streamjobs high_risk_geo <client>    (never fill the placeholders in by hand)
-- Run it with:     bin/sql-client.sh -f <rendered file>               (needs flink-sql-connector-kafka in Flink's lib)
-- Keep semicolons out of these comments: the SQL client splits statements on them.

${COMMON}

SET 'table.exec.state.ttl' = '40 d';

INSERT INTO alerts_in
SELECT
  '${SOURCE}',
  CONCAT(party_id, ':', mon),
  'high_risk_geo',
  party_id,
  ROUND(LEAST(0.5 + total / 200000, 0.9), 2),
  CONCAT(CAST(n AS STRING), ' outbound wires totalling $', CAST(CAST(total AS DECIMAL(18, 0)) AS STRING),
         ' to high-risk jurisdictions in ', mon, '.'),
  SPLIT(ids, ',')
FROM (
  SELECT party_id, mon, amount,
         SUM(amount) OVER w AS total,
         COUNT(txn_id) OVER w AS n,
         LISTAGG(txn_id, ',') OVER w AS ids
  FROM (
    SELECT txn_id, party_id, amount, event_time, DATE_FORMAT(event_time, 'yyyy-MM') AS mon
    FROM transactions
    WHERE channel = 'wire' AND direction = 'out' AND amount > 0 AND counterparty_country IN (${COUNTRIES}) AND event_time IS NOT NULL
  )
  WINDOW w AS (PARTITION BY party_id, mon ORDER BY event_time ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
WHERE total >= ${MIN_TOTAL} AND total - amount < ${MIN_TOTAL};
