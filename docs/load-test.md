# Load test

A repeatable load test of the real ingestion path. Synthetic transactions go onto a client's Kafka topic. The platform's own stream consumers then load them, run detection, route alerts and open cases, and the lake writer appends the same transactions to Iceberg. The harness times each stage, then times queries on the lake and puts read load on the API. Everything in the report was measured except the last section, which is arithmetic on the measurements and says so.

```
make backbone                    # or make full / make observability
make load-test N=200000          # reports copied to data/loadtest/
```

You can also run it without Docker, with the consumers in the same process:

```
cd backend && EVENT_BUS_URL=memory:// uv run python -m app.loadtest run --transactions 20000 --inline
```

Options:

- `--client` loads into another client. It is created from the full-service template if missing, and ids carry an `LT-` prefix.
- `--customers` defaults to one for every 50 transactions.
- `--seed`: the same seed gives the same data.
- `--api URL` onboards through the running API and adds the API stage. Use `--api-seconds` and `--api-concurrency` to shape that stage.

About 1% of the synthetic customers behave suspiciously: cash just under the reporting threshold, and wires to higher-risk countries. Detection therefore does real work and raises alerts at a plausible rate.

## What the first runs found, and what was fixed

| Finding | Effect | Fix |
|---|---|---|
| Counterparty name screening compared every payment's counterparty with every list name using `difflib`, on every batch, across the customer's whole history | 92% of stream time (profiled: 69 of 75 s for 5,000 transactions) | Each list name's matcher is built once, and a name already screened is answered from memory. The answers are identical, which a randomised comparison over 3,000 names confirmed. Stream time fell about 10 times |
| The lake writer committed one file per table at every poll | 775 Parquet files for 50,000 rows, and lake queries 4 to 7 times slower | Messages are gathered up to `LAKE_FLUSH_ROWS` (20,000) or `LAKE_FLUSH_SECONDS` (5) before writing. The same rows now take 155 files |
| Each consumer read, added to and wrote back the client's stream totals | With three consumers side by side, 500 of 90,000 messages went missing from the totals. All 90,000 were loaded; only the counting was wrong | The totals are now added inside the database (`UPDATE ... SET received = received + n`) |

## Results: cloud container, 2026-11-21

The machine was Linux, 4 vCPUs and 16 GB. Postgres 17 and Kafka 3.9 ran in Docker. The API and the workers ran as separate processes: relay, lake writer, and 1 or 3 stream consumers. Each run used a fresh client with 90,000 transactions over 1,800 customers, and the topic had 3 partitions.

| | 1 stream consumer | 3 stream consumers |
|---|---:|---:|
| Publish to Kafka (send only) | 266,908 / s | 222,664 / s |
| Stream: loaded, detected, alerts routed, committed (first to last load) | **1,081 / s** | **2,990 / s** |
| First half / second half of the run | 1,183 / 947 per s | 3,331 / 2,493 per s |
| All 90,000 in Iceberg after publishing began | 106 s | 53 s |
| Alerts raised | 102 | 106 |
| Lake queries over 90,000 rows in 155 files (DuckDB, median) | 21 to 32 ms | 21 to 33 ms |
| API: 8 callers for 20 s, 6 pages in turn | 40 / s, p50 158 ms, p95 412 ms, 0 errors | 42 / s, p50 152 ms, p95 381 ms, 0 errors |

Three consumers loaded 2.8 times as fast as one on four cores. Throughput falls during a run, by about a fifth to a quarter, because detection re-reads each affected customer's history on every batch. That is the next limit: a customer with years of history costs more per payment than a new one. Production streaming detection is meant to run on Flink with state kept per customer (see the Flink jobs), and this measurement is of the in-application path.

## Projection (arithmetic, not measured)

At 1,081 a second, one consumer process handles about 93 million transactions a day. For the design target:

| Target | Average rate | Consumer processes if scaling stayed linear |
|---|---:|---:|
| 1 billion a year | 32 / s | 1 |
| 1 billion a month | 386 / s | 1 |
| 1 billion a day | 11,574 / s | 11 |

This assumes throughput keeps growing with consumer processes, each with its own partitions and enough database capacity behind it. The runs measured up to three consumers on one machine. Peaks run several times the average, and a fleet was not measured.
