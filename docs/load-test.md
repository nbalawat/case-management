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

More stream consumers beside the worker's own, in the same container, each with its own metrics port (a process that
finds its port taken works on without metrics and says so):

```
docker compose exec -d -e METRICS_PORT=9101 worker uv run --no-dev python -m app.backbone.worker streams
docker compose exec -d -e METRICS_PORT=9102 worker uv run --no-dev python -m app.backbone.worker streams
```

About 1% of the synthetic customers behave suspiciously: cash just under the reporting threshold, and wires to higher-risk countries. Detection therefore does real work and raises alerts at a plausible rate.

## What the first runs found, and what was fixed

| Finding | Effect | Fix |
|---|---|---|
| Counterparty name screening compared every payment's counterparty with every list name using `difflib`, on every batch, across the customer's whole history | 92% of stream time (profiled: 69 of 75 s for 5,000 transactions) | Each list name's matcher is built once, and a name already screened is answered from memory. The answers are identical, which a randomised comparison over 3,000 names confirmed. Stream time fell about 10 times |
| The lake writer committed one file per table at every poll | 775 Parquet files for 50,000 rows, and lake queries 4 to 7 times slower | Messages are gathered up to `LAKE_FLUSH_ROWS` (20,000) or `LAKE_FLUSH_SECONDS` (5) before writing. The same rows now take 155 files |
| Three consumers on a MacBook (below) were no faster than one, with 9,000 duplicates. A new client's first payments reached all three at once; each tried to create the client's watchlist, two failed on its unique code, and a consumer whose batch failed left its group and joined again. Every member then rebalanced, their commits were refused ("generation id is not valid") and they processed the same messages again | Rebalances over and over, duplicate work, a third of the throughput | The watchlist is created in a savepoint and the loser uses the winner's list. A failed batch now seeks back to the last commit inside the group instead of leaving it. Reproduced on Kafka 3.9 and Postgres 17 with three worker processes: before, 1,500 duplicates and four refused commits in 30,000; after, none |
| The harness ended the stream stage when messages *received* reached the total, but a redelivered message is received twice | The Mac run stopped with 87,500 of 90,000 payments in the store; nothing was lost, the rest were still arriving | It waits for payments loaded (or set aside) instead, which reaches the total only when every one is in |
| Every worker process in one container read the same `METRICS_PORT` | Extra consumers started with `exec` crashed with "address already in use" | A process that finds the port taken works on without metrics and says so; give each its own port to scrape it |
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

## Results: MacBook Pro, Docker Desktop, 2026-11-21

The machine was an Intel Core i7-9750H (6 cores, 12 threads) with 16 GB, and Docker Desktop had 12 CPUs and 7.75 GB of
memory. The stack was `make backbone` (Postgres, Kafka, the API and one worker) at commit 2a938a5. Each run used a
fresh client with 90,000 transactions over 1,800 customers on a 3-partition topic.

| | Run 1, 1 stream consumer | Run 2, 1 stream consumer |
|---|---:|---:|
| Stream: loaded, detected, alerts routed, committed (first to last load) | **428 / s** | **528 / s** |
| First half / second half of the run | 469 / 379 per s | 578 / 468 per s |
| All 90,000 in Iceberg after publishing began | 221 s | 183 s |
| Duplicates, rejects | 0, 0 | 0, 0 |
| Alerts raised | 106 | 102 |
| Lake queries (DuckDB, median) | 47 to 124 ms over 186 files | 27 to 77 ms over 155 files |
| API: 8 callers for 20 s | 20 / s, p50 323 ms, p95 813 ms, 0 errors | 25 / s, p50 236 ms, p95 660 ms, 0 errors |

The worker used about 47% of one core and under 400 MB, and Postgres about 20%. One consumer on the laptop runs at
about 40 to 50% of the cloud container's rate. A likely reason, not measured: Docker Desktop on an Intel Mac runs
the containers in a virtual machine, where disk and network I/O between them costs more. The three-consumer run on this machine found
the defects in the table above, so its figures are not reported; the rerun with the fixes follows.


### With the fixes: one consumer, then three (commit ebe3a95)

The same Mac, with the same database volumes as the runs above. By then it held seven test clients and about 630,000
transactions. The two runs went back to back, so the machine was in the same state for both, though it was busy:
Spotlight was indexing, memory was nearly full and swapping, and the load average was 7 to 13. Compare the two runs
with each other, not with the ones above.

| | Run A, 1 stream consumer | Run B, 3 stream consumers |
|---|---:|---:|
| Stream (first to last load) | **209 / s** | **350 / s** (1.67 times Run A) |
| First half / second half of the run | 219 / 198 per s | 505 / 248 per s |
| All 90,000 in Iceberg after publishing began | 447 s | 265 s |
| Duplicates, rejects | 0, 0 | 0, 0 |
| Operational store holds | 90,000 | 90,000 |
| API: 8 callers for 20 s | 8.2 / s, p50 808 ms, p95 1,982 ms, 0 errors | 6.1 / s, p50 1,154 ms, p95 2,484 ms, 0 errors |
| CPU mid-run: worker / Postgres / Kafka | 55% / 39% / 12% | 151% / 64% / 81% |

Neither run logged a warning or an error. Three consumers reached about 56% of linear scaling. In the second half of
Run B, throughput fell to half its first-half rate. Either the partitions finished unevenly, or Postgres became the
shared limit, since three consumers and the API compete for it. API latency rose with the extra consumers for the same
reason. A clean baseline, meaning a fresh database on an idle machine, is still to be run.
