-- Counterparty names screened against a client's watchlist, as a Flink SQL job over its transaction topic.
--
-- The same rule as the application's `sanctions_name` scenario (backend/app/detection/scenarios.py): a payment
-- raises an alert when its counterparty is at least ${THRESHOLD_TEXT} alike to a name or alias on the client's lists
-- in force, unless a person has ruled that pair out. The first listed name that is alike enough is the match.
-- Flink SQL cannot score how alike two names are, so the job calls WATCH_MATCH, a small function shipped in the
-- Flink image (deploy/flink/udf). It is the application's measure ported to Java, and a test holds the two together.
--
-- The list is written into the job when it is rendered: ${NAMES_COUNT} names and ${EXEMPT_COUNT} pairs ruled out.
-- A change to the list, an exemption or the threshold reaches the job when it is restarted, and an exemption that
-- runs out keeps holding until then. The Integrations page shows when the job is behind the lists.
--
-- Render it with:  python -m app.streamjobs sanctions_name <client>   (never fill the placeholders in by hand)
-- Run it with:     bin/sql-client.sh -f <rendered file>               (needs the Kafka connector and caseforge-udf.jar in Flink's lib)
-- Keep semicolons out of these comments: the SQL client splits statements on them.

${COMMON}

CREATE TEMPORARY FUNCTION WATCH_MATCH AS 'caseforge.WatchMatch';

INSERT INTO alerts_in
SELECT
  '${SOURCE}',
  txn_id,
  'sanctions_name',
  party_id,
  ROUND(m.ratio, 2),
  CONCAT('Counterparty ''', counterparty, ''' matches watchlist entry ''', m.name, ''' at ',
         CAST(CAST(ROUND(m.ratio * 100) AS INT) AS STRING), '% similarity on a $',
         CAST(CAST(COALESCE(amount, 0) AS DECIMAL(18, 0)) AS STRING), ' ', COALESCE(channel, 'payment'), '.'),
  ARRAY[txn_id]
FROM (
  SELECT txn_id, party_id, counterparty, amount, channel,
         WATCH_MATCH(counterparty, ARRAY[${NAMES}], ARRAY[${EXEMPT}], CAST(${THRESHOLD} AS DOUBLE)) AS m
  FROM transactions
  WHERE counterparty IS NOT NULL AND counterparty <> '' AND txn_id IS NOT NULL
)
WHERE m IS NOT NULL;
