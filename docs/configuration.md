# Configuration reference

CaseForge has two kinds of configuration. **Deployment settings** are environment variables, the same for every
client a deployment serves, read in `backend/app/config.py`. **Client configuration** is data each client owns,
versions and changes from the UI. Nothing in either names a cloud: storage, brokers and engines are URLs, and cloud
specifics such as TLS or SASL properties are passed through as JSON.

## Client configuration

| Area | Starts from | Changed on | Versioning and controls |
|---|---|---|---|
| Case types and workflows | `backend/case_types/*.yaml`, chosen by the client's template | Workflows | Draft, check, diff, publish; open cases finish on the version they started on; a draft with an unknown role, dead-end stage or bad condition is refused |
| Access policy: permissions, roles, queues, people, case visibility | `backend/access/*.yaml` | Access | Versioned; refused if it would leave a workflow step with nobody who can take it or lock everyone out |
| Agent autonomy, limits, kill switches | `backend/agents/catalog.yaml` | Agents | Versioned; level L3 only after passing evaluation on the client's closed cases |
| Scenario settings | each scenario's declared defaults and ranges | Detection tuning | Numbered revisions, backtest attached, second-person approval |
| Code lists: closing reasons, QA criteria and weights, root causes, hibernation rules, risk factors and bands, ownership rules, document types, review triggers | `backend/reference/` | Lists | Retired rather than deleted; risk downgrades need a second person |
| Screening lists | loaded files | Screening lists | Each load a version; rescreening on change; exemptions need two people |
| Data sources, mappings, quality rules, schedules | proposed from a sample file | Data pipelines | Storage locations are set by platform operators only; mappings versioned |
| Regulatory regime and sending channel | template | Platform (operators) | FinCEN or goAML; checks per format before sending |
| Identity provider | none | `PUT /api/platform/tenants/{id}/identity` | Issuer, audience and claim per client |

### Case type anatomy

```yaml
case_type: tm_alert_investigation
title: Transaction monitoring investigation
version: 4.0.0
initial_state: triage
states:
  triage:
    label: Triage
    queue: aml-l1            # where unassigned work waits
    sla_hours: 48            # the stage clock
    tasks:
      - {id: review_briefing, label: Review the alert and agent briefing}
  awaiting_information: {label: Awaiting information, kind: wait, queue: aml-l2, sla_hours: 240}
  filed: {label: SAR filed, kind: closed, disposition: sar_filed}
transitions:
  - {from: triage, to: investigation, label: Escalate, roles: [analyst, investigator], requires: [tasks]}
  - from: mlro_review
    to: filed
    roles: [mlro]
    requires: [narrative]           # also: tasks, note, reason, open_request, requests_answered
    four_eyes: true                 # nobody approves work they drafted or moved
    when: ["alerted_amount < 100000", "pep == false"]
sla: {clock: SAR filing deadline, due_days: 30}
escalation: {raise_priority: true, notify: team_lead}
```

### Access policy anatomy

```yaml
permissions:
  submit_filings: Send regulatory reports
queues:
  aml-l2: AML investigations (L2)
roles:
  investigator: {title: L2 investigator, permissions: [work], sees: {scope: all, case_types: [tm_alert_investigation]}}
  auditor:      {title: Internal auditor, permissions: [view_team, view_restricted], read_only: true}
people:
  - {id: ivan, name: Ivan Petrov, role: investigator, queues: [aml-l2]}
```

`sees.scope` is `all`, `queues` or `own`; people always keep sight of cases they own or have acted on, and
restricted cases also need `view_restricted`. In sign-in mode the identity provider proves who someone is and the
policy says what they may do.

## Deployment settings

### Core

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | SQLite under `data/` | The operational store; Postgres 16 in any real deployment |
| `DATABASE_ROLE` | empty | Postgres only. The role every request's transaction runs as (`SET LOCAL ROLE`), so row-level security binds it even when `DATABASE_URL` names a superuser or the tables' owner. The application creates it (no login, no BYPASSRLS) with its grants at start. Compose and Helm set `caseforge_app`. Empty: requests run as the connecting user, and the health check says whether that user is held |
| `SCHEMA_ON_MISMATCH` | `rebuild` | A database at another schema version: `rebuild` drops every table (demos only), `refuse` stops with an explanation. Helm sets `refuse` |
| `MIGRATE_ON_START` | `1` | Migrate at start. Off where a separate step migrates (the Helm hook job) |
| `DEMO_TENANTS` | `1` | Seed the three demo clients |
| `DEFAULT_TENANT` | `meridian` | Demo mode only: the client when a request names none |
| `CASE_TYPES_DIR`, `ACCESS_DIR`, `TEMPLATES_DIR`, `SAMPLES_DIR` | `backend/...` | Where the libraries are read from |

### Identity

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_MODE` | `demo` | `demo`: client and person from headers and the pickers. `oidc`: only from a signed token |
| `PUBLIC_URL` | `http://localhost:8000` | The address users reach, for redirects |
| `OIDC_OPERATOR_ISSUER`, `OIDC_OPERATOR_AUDIENCE`, `OIDC_OPERATOR_CLAIM` | empty | The separate provider platform staff sign in through |
| `DEV_IDP` | `0` | A built-in identity provider for laptops and tests; never in a real deployment |

### Storage

| Variable | Default | Meaning |
|---|---|---|
| `EVIDENCE_URL` | local `data/evidence` | Customer documents and sent filings, per client under `<url>/<client>/`; `s3://`, `abfss://`, `gs://` |
| `EXPORTS_URL` | local `data/exports` | Offboarding bundles, per client under `<url>/<client>/`, sealed under the client's key until the client is erased. Helm sets it to `<evidenceUrl>-exports` unless `storage.exportsUrl` is given |
| `KEY_PROVIDER` | `local` | Per-client keys that seal customer documents (envelope encryption, `app/keys`). `local`: a keyring under `KEYS_DIR`; `transit`: OpenBao or Vault Transit; `none`: stored as given. Helm offers only `transit`, or `none` for demos |
| `KEYS_DIR` | `data/keys` | The local keyring. Keep it on a persistent volume, apart from the documents: a lost keyring is lost documents |
| `KEYS_MASTER_KEY` | empty | 32 bytes, base64, sealing the local keyring. Empty: a random one is written beside the keyring (demos) |
| `TRANSIT_ADDR`, `TRANSIT_TOKEN`, `TRANSIT_MOUNT`, `TRANSIT_PREFIX` | empty, empty, `transit`, `caseforge-` | The OpenBao or Vault Transit engine; one key per client named `<prefix><client>`. Put the token in the secret |
| `QUARANTINE_URL` | local `data/quarantine` | Rejected rows and held files |
| `LAKE_DIR` | local `data/lake` | Demo landing zone for the seeded clients |
| `PIPELINE_SCHEDULER` | `0` | In-process schedule timer; one process only |
| `ALLOW_MEMORY_STORAGE` | `0` | Tests only |

### Event backbone and lake

| Variable | Default | Meaning |
|---|---|---|
| `EVENT_BUS_URL` | empty | `kafka://host:9092[,host:9092]`. Empty: events wait in the outbox |
| `KAFKA_CONFIG` | empty | JSON of extra client properties (TLS, SASL, cloud settings) |
| `EVENT_TOPIC_PREFIX` | `caseforge` | Topics are `<prefix>.<client>.<stream>` |
| `OUTBOX_PUBLISHER` | `relay` | `relay` (built in) or `debezium` (change data capture; the relay stays off) |
| `BACKBONE_WORKERS` | `0` | Run the relay and consumers inside the API process (one process only); otherwise `python -m app.backbone.worker` |
| `OUTBOX_KEEP_DAYS` | `7` | How long published outbox rows are kept |
| `STREAM_UNKNOWN_CUSTOMER` | `reject` | A streamed payment for an unknown customer or account: `reject` or `create` |
| `LAKE_CATALOG` | empty | JSON of Iceberg catalog properties (SQL catalog on a laptop, REST catalog in a deployment). Empty: no lake |
| `LAKE_FLUSH_ROWS`, `LAKE_FLUSH_SECONDS` | `20000`, `5` | The lake writer gathers this many messages, or waits this long, before writing: fewer, larger files |
| `DETECTION_HISTORY` | `operational` | `lake`: backtests and lookbacks read history from the lake (falls back when there is none) |

### Backups

| Variable | Default | Meaning |
|---|---|---|
| `BACKUP_URL` | `data/backups` | Where `python -m app.backup` writes and reads backups: any object store URL. Helm sets it from `backup.url` and schedules a nightly backup and a weekly restore drill |

### Observability

| Variable | Default | Meaning |
|---|---|---|
| `LOG_FORMAT` | `text` | `json`: one object per line with request id, client and trace id. Helm sets `json` |
| `LOG_LEVEL` | `INFO` | Root log level |
| `METRICS_PORT` | empty | Serve Prometheus metrics on this port (the workers always need it to be scraped). Set: the API's own `/metrics` answers 404, so metrics never go out through the ingress. Helm sets `9100` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Send traces over OTLP/HTTP (Jaeger, Tempo, an OpenTelemetry Collector). The other standard `OTEL_*` variables apply |

### Engines

| Variable | Default | Meaning |
|---|---|---|
| `SEARCH_URL`, `SEARCH_CONFIG` | empty | `opensearch://` or `opensearchs://` nodes and extra client options. Empty: search in the database |
| `GRAPH_URL` | empty | `age://user:password@host:5432/db`. Empty: routes found in the database |
| `TEMPORAL_URL`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE` | empty, `default`, `caseforge` | Durable stage clocks; run `python -m app.durable.worker` |
| `TEMPORAL_SYNC_SECONDS`, `TEMPORAL_SWEEP_SECONDS` | `15`, `300` | How often clocks are brought into step, and the sweep schedule |
| `FLINK_URL` | empty | Flink REST address; the Integrations page shows each client's jobs |
| `FLINK_GATEWAY_URL` | empty | Flink SQL Gateway; lets people who approve detection changes start, stop and restart jobs |
| `FLINK_KAFKA_BOOTSTRAP` | empty | Brokers as Flink reaches them, when different |

### Models

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | empty | Turns on the hosted model |
| `ASK_PROVIDER` | `auto` | `auto` uses the model when a key is set; `offline` forces the planner |
| `ASK_MODEL`, `ASK_EFFORT` | `claude-opus-5-5`, `medium` | Model and effort for Ask |
| `AGENT_MODE` | `rules` | `model` runs specialist agents as tool loops on the model, rules as fallback |
| `LLM_PROVIDER` | `mock` | The provider behind agent drafting |

## Public API

| Variable | Default | Meaning |
|---|---|---|
| `API_SECRET_KEY` | empty (development key; refused under real sign-in) | Keys credential hashes and webhook secret encryption. 32+ characters, from the secret store |
| `API_TOKEN_TTL` | `900` | Access token lifetime, seconds |
| `API_RATE_LIMIT`, `API_TENANT_RATE_LIMIT` | `600`, `3000` | Requests a minute per application, per client |
| `API_MAX_BODY` | `2097152` | Largest request body, bytes |
| `API_CALL_KEEP_DAYS` | `30` | How long the call record is kept |
| `WEBHOOK_WORKERS` | `0` | Deliver webhooks inside the API process (one process only) |
| `WEBHOOK_ALLOW_PRIVATE` | `0` | Allow http and private webhook addresses, for a laptop receiver |

See [public-api.md](public-api.md).

## Helm values

The chart maps these onto values (`eventBus.url`, `search.url`, `temporal.url`, `flink.url`, `storage.evidenceUrl`,
`llm.agentMode`, `auth.operatorIssuer` and so on); secrets such as `DATABASE_URL`, `ANTHROPIC_API_KEY`,
`KAFKA_CONFIG`, `SEARCH_CONFIG`, `GRAPH_URL` and `LAKE_CATALOG` come from the Secret named in `existingSecret`. See
[the chart's README](../deploy/helm/caseforge/README.md).
