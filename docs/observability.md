# Observability

Every signal uses an open standard, so the deployment picks the tools. On a laptop, `make observability` runs everything in `make full` plus Prometheus, Grafana and Jaeger.

| Signal | Where | What reads it |
|---|---|---|
| Liveness | `GET /healthz` | Kubernetes liveness probe. It answers whenever the process does |
| Readiness | `GET /readyz` | Startup and readiness probes, load balancers. 503 unless the database answers and holds the newest migration |
| Detail | `GET /api/platform/health` (platform staff) | The operator console's **Platform health** card. It checks every configured service with a time limit and reports how long each took |
| Metrics | `/metrics` on the API, or on `METRICS_PORT` | Prometheus, or anything that scrapes its format (Mimir, Thanos, a cloud's managed Prometheus) |
| Traces | OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT` | Jaeger, Grafana Tempo, an OpenTelemetry Collector in front of a cloud's tracing service |
| Logs | stdout, `LOG_FORMAT=json` | Fluent Bit, Vector, a cloud's log agent |

Optional services (Kafka, the lake catalog, OpenSearch, the graph engine, Temporal) are checked and reported. They never make the API unready, because the platform falls back without each of them.

## Metrics

Process metrics are counted as the work happens:

- `caseforge_http_requests_total{method,route,status}` and `caseforge_http_request_seconds{method,route}`. The route is the template (`/api/cases/{case_id}`), never the raw path.
- `caseforge_stream_messages_total{stream,tenant,outcome}` (loaded, duplicate, rejected), `caseforge_stream_batch_seconds{stream}` (detection included), and `caseforge_alerts_raised_total{stream,tenant}`.
- `caseforge_lake_rows_written_total{tenant,table}`.

Platform gauges are read from the database when Prometheus scrapes, at most every 15 seconds, and only by the API:

- `caseforge_clients{status}`
- `caseforge_cases{tenant,status}`, with status open, closed or overdue
- `caseforge_alerts{tenant,status}`
- `caseforge_outbox_waiting{tenant}` and `caseforge_outbox_oldest_waiting_seconds{tenant}` (relay mode only)
- `caseforge_stream_*_total_stored` and `caseforge_stream_last_message_timestamp_seconds`

Labels are bounded: a deployment has tens of clients, and no case or customer id is ever a label. With `METRICS_PORT` set (Helm sets it), metrics are served only on that port, which no ingress routes to.

## Traces

Each API request is a span (FastAPI instrumentation). Each SQL statement is a child span, created from SQLAlchemy's own events, and its parameters are never recorded. Each stream micro-batch is a `stream.batch` span with its client and size.

## Dashboard and alerts

`deploy/observability/grafana/dashboards/caseforge-platform.json` is provisioned into Grafana. It has four sections:

- **Right now:** targets up, request rate, p95, transactions loaded a second, oldest outbox event, and cases past deadline.
- **API:** requests and p95 by route, errors by status, and percentiles.
- **Streams and the lake:** messages by client and outcome, micro-batch time, alerts raised, lake rows written, outbox backlog, and time since a stream last loaded.
- **Work:** open and overdue cases by client, and alerts by status.

`deploy/observability/alerts.yml` holds starting-point Prometheus rules:

- a target is down
- more than 2% of requests are failing
- p95 is over 2 s
- the outbox has been stuck for more than 5 minutes
- a client's stream is rejecting records
- cases are overdue

## Helm

- The API's startup and readiness probes use `/readyz`, and liveness uses `/healthz`.
- Every pod serves metrics on `observability.metricsPort` (9100) and carries `prometheus.io/*` annotations (`observability.scrapeAnnotations`).
- `networkPolicy.monitoringNamespace` lets that namespace's Prometheus reach the metrics port.
- `observability.otlpEndpoint` turns on traces, and `observability.logFormat` defaults to `json`.
