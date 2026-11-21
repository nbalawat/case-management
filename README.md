# CaseForge

**A configurable, multi-tenant, AI-first case management platform for financial crime: AML transaction monitoring,
KYC and customer due diligence, sanctions, fraud, regulatory reporting and remediation, on one canonical model.**

CaseForge takes a bank's data from object storage or a stream, maps it to one canonical model, detects suspicious
activity, turns alerts into cases, has governed AI agents prepare each investigation, and routes the work through
the client's own workflows to the right people, under four-eyes, stage-clock and audit controls, ending in a
regulator-ready filing. Many clients run on one platform, each with its own people, access policy, workflows,
detection settings, data and identity provider, and a new client goes live from a template in about a minute.
Anyone can ask a plain-language question about the whole caseload and get an answer that names the cases behind it.

It is built to be deployed two ways by the same team: as a **dedicated stamp** inside a large bank's own AWS, Azure
or GCP account (or an air-gapped site), and as a **pooled managed service** for smaller institutions. Every
engine it uses is open source, every engine sits behind an interface, and nothing in the code names a cloud.

> **Status.** The full application tier is built and tested end to end, and every infrastructure engine has been run
> for real against its open-source server. It has not yet been installed on a cloud cluster or run under
> production load, and all data is synthetic. [What is real and what is not](#what-is-real-and-what-is-not) says
> exactly what has and has not been proven, component by component.

| | |
|---|---|
| ![My work](docs/screenshots/01-my-work.png) | ![Case investigation](docs/screenshots/04-case-investigation.png) |
| ![Ask anything](docs/screenshots/15-ask.png) | ![Agents](docs/screenshots/20-agents-acting.png) |
| ![Workflow designer](docs/screenshots/08-workflows.png) | ![Customer profile and network](docs/screenshots/28-customer-360.png) |
| ![Data pipelines](docs/screenshots/18-data-pipeline-run.png) | ![Operator console](docs/screenshots/14-operator-console.png) |

**Contents:** [Why CaseForge](#why-caseforge) ·
[Capabilities](#capabilities) · [Configured per client, not coded](#configured-per-client-not-coded) ·
[Architecture](#architecture) · [Multi-tenancy and security](#multi-tenancy-and-security) ·
[AI agents](#ai-agents-and-ask-anything) · [Technology](#technology) · [Quick start](#quick-start) ·
[Deploying to AWS, Azure and GCP](#deploying-to-aws-azure-and-gcp) · [Code layout](#how-the-code-is-laid-out) ·
[Documentation](#documentation) · [Status](#what-is-real-and-what-is-not) · [Milestones](#milestones)

## Why CaseForge

| | |
|---|---|
| **One platform, every financial-crime workload** | Monitoring, KYC, sanctions, fraud and filing share one model of customers, accounts, payments, alerts and cases, so an investigator sees one customer across every desk and a KYC review can be opened by a SAR. |
| **Configured, not coded** | Workflows, roles, permissions, case visibility, agent autonomy, detection thresholds, risk models, screening lists, code lists, data mappings and regulatory regime are each client's own versioned configuration, changed from the UI with checks and a second person where it matters. |
| **Multi-tenant by design** | Client isolation lives in the data layer under every query; each client brings its own identity provider; an operator console onboards, suspends and monitors clients without seeing their data. |
| **AI that prepares, people who decide** | Thirteen agents assemble evidence, research, draft and check, each within an autonomy level the client sets and only after evaluation on the client's own history. Filing, closing and approving are never agent actions. Every agent step is on a hash-chained ledger. |
| **Overlay or full platform** | Sit on top of an incumbent monitoring engine (alerts in by API or topic) or run detection, cases and filing end to end. |
| **Open source and cloud-neutral** | Postgres, Kafka, Debezium, Iceberg, OpenSearch, Flink, Apache AGE and Temporal, each switched on by one URL and each behind an interface; one Helm chart with four deployment modes; workload identity instead of stored cloud credentials. |
| **Evidence a regulator can follow** | Append-only case history, transactional outbox to CloudEvents, fingerprinted documents with chain of custody, locked narratives after filing, change register and access recertification. |

## Capabilities

| Area | What it does |
|---|---|
| **Detection and alert intake** | Four versioned scenarios (structuring, rapid movement, high-risk geography, watchlist name match) after every file load and stream batch; all four also as Apache Flink jobs per client, started and stopped from the product. Third-party alerts by `POST /api/alerts` or a Kafka topic, deduplicated and grouped per customer. Hibernation rules for weak alerts. |
| **Detection tuning and lookbacks** | Per-client scenario settings as numbered revisions, backtested on a year of history (alerts lost, filings that would have been missed, alerts gained) and approved by a second person. Lookback projects re-run a past period and are signed off with frozen figures. |
| **Case management** | Six case types as versioned YAML workflows: stages, queues, checklists, conditional routing, waiting stages, four-eyes, stage clocks with escalation, regulatory deadlines, QA sampling with reopen. Bulk claim, reassign, move and close; case merge; restricted cases; information requests. |
| **Case workspace** | Priority, deadlines, stage clock, agent view and owner at a glance; evidence in tabs (overview, transactions, customer, narrative, requests, timeline); a decision rail that explains why each step is or is not available; a transaction explorer that drills from any cited finding. |
| **KYC and due diligence** | Risk ratings from the client's own factors and bands, with governed downgrades; beneficial ownership worked out through every chain, with flags for trusts, nominees and loops; a document store with SHA-256 fingerprints and custody log; flow of funds; event-driven and periodic reviews. |
| **Sanctions** | Versioned watchlists with rescreening on every change, the client's own do-not-deal list, two-person false-match exemptions, holds on payments, accounts and cards with two-person release, blocked and rejected payment reports, confidential law-enforcement searches. |
| **Fraud** | A fraud workflow on a two-hour clock, customer contact as a waiting stage, refunds above a limit to a lead, disputes with credit, recovery and resolution clocks, loss and recovery by cause. |
| **Regulatory reporting** | One neutral report rendered as FinCEN SAR/CTR (BSA XML) or goAML STR/CTR per client; pre-send checks; a register from prepared to accepted or rejected; amendments that keep the original; narrative lock after filing; CTRs from cash days in one step. |
| **Entities, network and search** | Entity resolution of counterparties with a human desk for uncertain matches; customer 360 with a two-hop payment network; shortest path between customers; one typo-tolerant search box over everything a person may see. |
| **Ask anything** | Plain-language questions over the whole caseload, answered through governed read-only lookups (never SQL), limited to what the asker may see, with every cited case verified. |
| **Oversight and governance** | Dashboards where every figure drills to its cases; team workload and breached clocks; a monthly management report with exports; a change register across every configuration area; access recertification; audit search. |
| **Data onboarding** | Pipelines from S3, Azure Data Lake, Google Cloud Storage or local folders: CSV, JSON lines, Parquet; saved, versioned mappings with a proposer; quality gates, quarantine, reconciliation, load-once and lineage. Streams of transactions and alerts on Kafka. |
| **Platform operations** | Client templates and one-step onboarding, an operator console in counts only, per-client identity providers, versioned schema migrations, a Helm chart with four deployment modes, CI across every engine. |

## Configured per client, not coded

Almost everything that differs between two banks is configuration that each client owns, versions and publishes
from the UI. Work in flight keeps the version it started on; risky changes need a second person; a change that
would break something is refused before it is published.

| What a client configures | Where | Guard rails |
|---|---|---|
| Case types and workflows: stages, queues, tasks, transitions, conditions, four-eyes, clocks, escalation, QA sampling | Workflows page; `backend/case_types/*.yaml` is the library | Checked draft with a diff; unknown roles, dead ends and bad conditions refused; versions per client |
| Roles, permissions, queues and people | Access page; `backend/access/*.yaml` are the starting policies | Refused if it would strand a workflow step or lock everyone out; versioned; recertified periodically |
| Case visibility per role (`all`, `queues`, `own`, by case type) and restricted matters | Access page | Enforced in the data layer under lists, dashboards, agents and Ask |
| Agent autonomy per agent and case type (off, assist, prepare, act within policy), limits, kill switch | Agents page; `backend/agents/catalog.yaml` declares the roster | L3 only after passing evaluation on the client's history; sampled human checks; challenger in shadow |
| Scenario thresholds and on/off | Detection tuning | Backtest attached, second-person approval, written reason when alerts that mattered would be lost |
| Risk factors, points and bands; ownership thresholds; document types; review triggers | Lists | Downgrades need a second person |
| Closing reasons, QA criteria and weights, root causes, hibernation rules | Lists | Retired, never deleted |
| Screening lists and the client's own list | Screening lists | Versioned loads, rescreening on change |
| Data sources, storage locations, file mappings, quality rules, schedules | Data pipelines | Locations set by platform operators only; mappings versioned |
| Regulatory regime (FinCEN or goAML) and sending channel | Platform settings | Pre-send checks per format |
| Identity provider | `PUT /api/platform/tenants/{id}/identity` | Client is decided by which provider signed the token |
| Template at onboarding (full-service bank, payments fintech, private bank) | Operator console; `backend/templates/` | Each client gets its own copy |

A workflow step is a few lines of YAML. This is the approval step of the monitoring investigation: above $100,000
or on a politically exposed customer, the MLRO cannot file alone and the case goes to the Head of FIU.

```yaml
- from: mlro_review
  to: senior_approval
  label: Approve and send to Head of FIU
  roles: [mlro]
  requires: [narrative]
  four_eyes: true
  when: [{any: ["alerted_amount >= 100000", "pep == true"]}]
```

And a role is one line of the client's access policy, including what it may see:

```yaml
investigator: {title: L2 investigator, permissions: [work, resolve_entities], sees: {scope: all, case_types: [tm_alert_investigation]}}
```

## Architecture

CaseForge is a modular monolith in Python with a React front end, surrounded by open-source engines that each switch
on with one URL. With no URLs it runs as one process on SQLite; with all of them it runs the full reference
architecture. The application code is the same either way: every engine sits behind an interface with an
in-database or in-process fallback, and engines answer with ids that are re-read through the caller's session, so
the client boundary never depends on an external index.

```mermaid
flowchart TB
  subgraph people[People]
    UI["React UI<br/>role-shaped menu, data grid, case workspace,<br/>dashboards, command palette, Ask"]
  end
  IDP["Identity provider per client<br/>(OIDC)"]
  subgraph api["CaseForge API (FastAPI)"]
    TEN["Tenancy and visibility filter under every query"]
    CASE["Case core and workflows"]
    AGENTS["Agents, ledger and Ask"]
    DESKS["KYC, sanctions, fraud, filing"]
    DET["Detection, tuning, alert hub"]
    ENT["Entities, network, search"]
    OVR["Dashboards, MI, governance"]
    PIPE["Data pipelines"]
    OUTBOX["Outbox: CloudEvents in the same transaction"]
  end
  subgraph engines["Optional engines (one URL each)"]
    OS["OpenSearch"]
    AGE["Apache AGE"]
    TMP["Temporal"]
    FLINK["Apache Flink"]
    LLM["Model provider"]
  end
  subgraph data["Data services"]
    PG[("Postgres 16<br/>cases, audit, outbox")]
    OBJ[("Object storage<br/>S3, ADLS, GCS")]
    KAFKA[("Apache Kafka<br/>relay or Debezium")]
    ICE[("Apache Iceberg<br/>history per client")]
  end
  CLIENT["Client systems: core banking files, transaction streams, incumbent alert engines"]

  UI --> api
  UI <--> IDP
  api <--> engines
  api <--> PG
  PIPE --> OBJ
  OUTBOX --> KAFKA
  KAFKA --> ICE
  KAFKA <--> FLINK
  CLIENT --> OBJ
  CLIENT --> KAFKA
```

| Layer | In the application (`backend/app/`) | Engine | Switched on by |
|---|---|---|---|
| Experience | React SPA (`frontend/src`) served by the API | | always |
| Identity | `auth/`: OIDC per client, separate platform-staff issuer, dev provider | Keycloak, Entra ID, Okta | `AUTH_MODE=oidc` |
| Tenancy and visibility | `tenancy.py`, `visibility.py` | Postgres | always |
| Case core | `cases/`, `durable/` | Temporal | `TEMPORAL_URL` |
| Agents and Ask | `agents/`, `ask/` | Anthropic API or any `ChatModel` | `ANTHROPIC_API_KEY`, `AGENT_MODE=model` |
| Desks | `kyc/`, `sanctions/`, `fraud/`, `holds.py`, `filing/`, `entities/` | OpenSearch, Apache AGE | `SEARCH_URL`, `GRAPH_URL` |
| Detection | `detection/`, `alerts/`, `streamjobs/` | Apache Flink and its SQL Gateway | `FLINK_URL`, `FLINK_GATEWAY_URL` |
| Data fabric | `ingest/`, `storage/`, `backbone/`, `lake/` | S3, ADLS, GCS; Kafka; Debezium; Iceberg | storage locations, `EVENT_BUS_URL`, `OUTBOX_PUBLISHER`, `LAKE_CATALOG` |
| Persistence | SQLAlchemy models, Alembic migrations | SQLite or Postgres 16 | `DATABASE_URL` |

Every replaceable part sits behind a small interface: `ObjectStore`, `SearchIndex`, the graph store, the event bus,
the lake store, filing `Format` and `Channel`, `Connector`, `MappingProposer`, `LLMProvider`, `ChatModel` and the
scenario registry. The data model is described in [docs/data-model.md](docs/data-model.md).

## Multi-tenancy and security

- **Client boundary in the data layer.** One schema with every row keyed by client. Sessions are bound to a client;
  queries are filtered and writes stamped automatically, and a statement that cannot be filtered is refused. Tested
  with ten clients side by side.
- **Case visibility per person.** Roles can be limited to their queues, their own cases or some case types; people keep
  sight of what they own or touched; restricted cases are hidden from everyone not cleared, including administrators.
  The same filter sits under lists, case pages, dashboards, the event feed, the agent ledger and Ask; a hidden case
  answers "not found". The suite signs in as every person at every client and checks every case.
- **Single sign-on per client.** OpenID Connect with each client's own provider; the client is decided by which
  provider signed the token. Asymmetric signatures only; PKCE in the browser. Platform staff use a separate provider
  and see counts, not client data.
- **Controls the engine enforces.** Four-eyes on approvals, two-person release of sanctions holds, two-person risk
  downgrades and detection changes, the narrative lock after filing, and no agent tool that files, closes or approves.
- **Evidence.** Append-only case history, hash-chained agent ledger, fingerprinted documents and filings with custody
  logs, every configuration change in one register.
- **No stored cloud credentials.** Storage drivers use the workload's own identity; Kubernetes pods run as non-root
  with secrets only from a Secret you name.

## AI agents and Ask anything

Thirteen agents are declared in [`backend/agents/catalog.yaml`](backend/agents/catalog.yaml): triage, data assembly,
entity research, adverse media, typology analyst, fraud analyst, sanctions adjudicator, KYC refresh, narrative
writer, QA critic, investigator copilot, lookback batch and tuning advisor. Each declares its goal, the tools it may
call, its highest autonomy and the one thing it may do alone. Each client sets every agent per case type to off,
assist (L1), prepare (L2) or act within policy (L3); an L3 setting cannot be published until it has passed an
evaluation on the client's own closed cases, and a sample of what agents do alone goes to a person. A challenger
profile runs in shadow; findings without a cited record are dropped; outside content reaches agents only as typed
fields from a tool-less extractor.

Ask anything answers plain-language questions through five read-only lookups over a fixed vocabulary of 31 fields
and 9 measures. The model never sees the database or writes SQL; lookups run as the person asking, so two people can
rightly get different answers; cited case ids are checked before they become links. With no API key both features
run offline (a planner and rule-based agents), which is also how the air-gapped mode runs. Other model providers
(Bedrock, Azure AI Foundry, Vertex AI, a self-hosted open-weight model) plug in behind the same gateway in
`backend/app/ask/gateway.py`.

## Technology

| Concern | Choice |
|---|---|
| API and domain | Python, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, uv |
| Database | Postgres 16 (SQLite for the laptop demo) |
| Object storage | fsspec with s3fs, adlfs, gcsfs; PyArrow |
| Events | Apache Kafka, Debezium, CloudEvents 1.0 |
| Lake | Apache Iceberg (pyiceberg; SQL or REST catalog) |
| Search | OpenSearch |
| Stream processing | Apache Flink SQL, SQL Gateway |
| Graph | Apache AGE on Postgres |
| Durable execution | Temporal |
| Identity | OpenID Connect (PyJWT), PKCE |
| Models | Anthropic API behind a provider-neutral gateway; offline fallbacks |
| Front end | React 19, TypeScript, Vite, Tailwind, Radix, cmdk, TanStack Table, Apache ECharts |
| Delivery | Docker, Docker Compose profiles, Helm 3, GitHub Actions |
| Infrastructure | OpenTofu for AWS, Azure and GCP behind one stamp contract; Argo CD, Kyverno, External Secrets, Traefik, cert-manager; Strimzi, OpenSearch and Flink operators |

Every engine dependency is an optional group in `backend/pyproject.toml`, so an image carries only the drivers its
deployment uses.

## Quick start

Pick one. Both end at <http://localhost:8000>.

```bash
docker compose up --build      # Docker Desktop, on Postgres
make run                        # no Docker, on SQLite; needs uv and Node 20+ (brew install uv node)
```

The first start (about ten seconds) seeds three clients of different kinds: Meridian Retail Bank (every workload,
twelve people), Kestrel Payments (fraud first, a lean team where people hold two roles) and Alder Private Bank (due
diligence first, reporting to goAML). Together they hold about 540 customers, 61,000 transactions and 120 cases,
already worked to different stages by each client's own people. The account menu at the top right picks the client
and the person; press `/` or `⌘K` anywhere for the command palette. Then follow the
**[demo guide](docs/demo-guide.md)**: 22 steps, one persona at a time, from an analyst's queue to an MLRO's filing to
an operator onboarding a new client.

| Command | What runs |
|---|---|
| `make run` / `make demo` | The application on SQLite / Postgres |
| `make backbone` | + Kafka, the outbox relay, stream consumers and the Iceberg lake |
| `make backbone-cdc` | The same, with Debezium publishing the outbox |
| `make full` | + OpenSearch, Apache AGE and Temporal |
| `make streaming` | + an Apache Flink cluster; `make flink-job CLIENT=meridian JOB=structuring` submits a job |
| `make dev-api` and `make dev-ui` | Hot-reload development on <http://localhost:5173> |
| `make seed` / `make reset` | Restore the demo data (SQLite / Docker) |
| `make test` | The backend test suite |
| `make migration m="what changed"` | Write the next schema migration after a model change |

**Single sign-on.** Start the API with `AUTH_MODE=oidc DEV_IDP=1` to sign in through a built-in identity provider
instead of the pickers. For a real provider (Keycloak, Entra ID, Okta), leave `DEV_IDP` off and register each
client's issuer with `PUT /api/platform/tenants/{id}/identity`.

**A model.** `export ANTHROPIC_API_KEY=...` before starting turns on free-form questions in Ask and, with
`AGENT_MODE=model`, model-backed agents.

**The event backbone without Docker.** `EVENT_BUS_URL=kafka://localhost:9092 BACKBONE_WORKERS=1 make run`. From the
Mac the Compose broker is `localhost:29092`; publish a canonical transaction to `caseforge.meridian.transactions`
and watch the Integrations page. Gated tests run against real engines when `TEST_POSTGRES_URL`, `TEST_KAFKA_URL`,
`TEST_OPENSEARCH_URL` or `TEST_FLINK_URL` is set.

The public API for other teams and third parties is at <http://localhost:8000/api/v1/docs> (see [docs/public-api.md](docs/public-api.md); applications are registered on the Developer page). The internal API the web interface uses is at <http://localhost:8000/docs>. Every setting is listed in
[docs/configuration.md](docs/configuration.md).

## Deploying to AWS, Azure and GCP

One image and one Helm chart ([`deploy/helm/caseforge`](deploy/helm/caseforge/README.md)) deploy the application
tier of a stamp: the API and UI, the backbone worker, the durable worker and a migration job that runs before every
install and upgrade. Stateful services are addressed by URL and run as managed cloud services or through their
open-source operators. A release that would be unsafe (no Secret, demo sign-in behind an ingress, no evidence
storage, a schema rebuild outside a demo) fails at install with the reason.

| Deployment mode | Values file | Shape |
|---|---|---|
| Pooled SaaS | `modes/saas.yaml` | One stamp in the firm's cloud, many clients separated by row, autoscaling API |
| Dedicated stamp | `modes/dedicated.yaml` | One release per client with its own namespace, database, topics and bucket |
| Client cloud | `modes/client-cloud.yaml` | The stamp in the client's own AWS, Azure or GCP account, with its registry and workload identity |
| Air-gapped | `modes/air-gapped.yaml` | The site's registry, rule-based agents, offline Ask, nothing reaches out |

| Capability | Portable default | AWS | Azure | GCP |
|---|---|---|---|---|
| Kubernetes | Upstream Kubernetes | EKS | AKS | GKE |
| Case database (`DATABASE_URL`) | Postgres 16 (CloudNativePG) | Aurora or RDS for PostgreSQL | Database for PostgreSQL Flexible Server | Cloud SQL or AlloyDB |
| Landing, evidence, quarantine | MinIO | S3 (`s3://`) | ADLS Gen2 (`abfss://`) | Cloud Storage (`gs://`) |
| Event backbone (`EVENT_BUS_URL`) | Kafka (Strimzi) | MSK | Event Hubs Kafka API or Confluent Cloud | Managed Service for Apache Kafka |
| Lake (`LAKE_CATALOG`) | Iceberg + REST catalog | S3 Tables or Glue | ADLS + OneLake or Unity | GCS + BigLake |
| Stream detection (`FLINK_URL`) | Flink operator | Managed Service for Apache Flink | Flink operator on AKS | Flink operator on GKE |
| Durable workflows (`TEMPORAL_URL`) | Temporal | Temporal on EKS or Temporal Cloud | Temporal on AKS or Temporal Cloud | Temporal on GKE or Temporal Cloud |
| Search (`SEARCH_URL`) | OpenSearch | OpenSearch Service | OpenSearch on AKS | OpenSearch on GKE |
| Graph (`GRAPH_URL`) | Apache AGE | AGE on self-managed Postgres | AGE on self-managed Postgres | AGE on self-managed Postgres |
| Identity | Keycloak | the bank's IdP or IAM Identity Center | Entra ID | the bank's IdP or Cloud Identity |
| Workload identity | service account | `eks.amazonaws.com/role-arn` | `azure.workload.identity/client-id` | `iam.gke.io/gcp-service-account` |
| Models | vLLM, open-weight models | Bedrock | Azure AI Foundry | Vertex AI |

```bash
kubectl -n acme create secret generic caseforge-secrets --from-literal=DATABASE_URL='postgresql+psycopg://...'
helm install acme deploy/helm/caseforge -n acme -f deploy/helm/caseforge/modes/client-cloud.yaml \
  --set existingSecret=caseforge-secrets \
  --set image.repository=<registry>/caseforge \
  --set publicUrl=https://cases.acme.example --set ingress.host=cases.acme.example \
  --set auth.operatorIssuer=https://idp.example.com/realms/ops \
  --set storage.evidenceUrl=s3://acme-caseforge/evidence \
  --set eventBus.url=kafka://broker:9092 --set temporal.url=temporal://temporal-frontend:7233 \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=arn:aws:iam::123456789012:role/caseforge
```

**From an empty cloud account.** [`infra/`](infra/README.md) holds OpenTofu for all three clouds behind one stamp
contract, so the Kubernetes layer never names a cloud. Each cloud has layered stacks (bootstrap, platform, add-ons,
stamp): a network, a private EKS, AKS or GKE cluster, a registry and keys once per environment, then per stamp a
Postgres, buckets (optionally WORM), its own key and workload identity, and managed or in-cluster Kafka and search. A
cloud-neutral in-cluster layer installs the operators, Kyverno policies and each stamp's namespace, secrets and Argo
CD applications; a stamp's image tag is a line in Git, changed by pull request. GitHub Actions signs in to each cloud
through OIDC only. All of it is validated, unit-tested against mocked providers, linted and security-scanned, and
none of it has yet been applied to a real cloud account.

On Azure the storage URL becomes `abfss://evidence@<account>.dfs.core.windows.net/caseforge` and the annotation
`azure.workload.identity/client-id`; on GCP, `gs://acme-caseforge/evidence` and `iam.gke.io/gcp-service-account`.
Today only Anthropic's API is implemented as a hosted model provider; the others in the table are slots behind the
gateway. The chart renders and lints in every mode in CI and the image starts as the pods' non-root user, but the
chart has not yet been installed on a cluster: treat the first cloud install as a test.

## How the code is laid out

```
backend/app/
  domain/        canonical model and access policy
  tenancy.py     client isolation: session binding, automatic filtering and stamping
  visibility.py  which cases a person may see, under every query
  auth/          OIDC verification, each client's identity provider, a development provider
  tenants/       client registry, templates, onboarding, access policy administration
  cases/         case type registry, guards, conditions, tasks, assignment, requests, clocks, QA, bulk, merge
  durable/       Temporal workflows and worker for stage clocks
  alerts/        alert hub: dedupe, group into cases, prioritise
  detection/     scenarios, engine, per-client settings with backtests and approval, lookbacks
  streamjobs/    Flink SQL jobs rendered per client and rule, run from the product
  agents/        catalog, policy, runtime, toolbox, extractor, ledger, triage, evaluation, copilot, oversight
  ask/           query vocabulary, read-only tools, model gateway and providers, answer loop, question record
  kyc/           risk rating, ownership, document store, flow of funds, review triggers
  sanctions/     watchlists, rescreening, exemptions, blocked and rejected reports, law-enforcement searches
  fraud/         disputes, recovery and loss
  holds.py       holds with two-person release; workdays.py business-day deadlines
  filing/        the neutral report, FinCEN and goAML renderers, checks, channels, register
  entities/      name matching, entity resolution and its desk, network, profiles, search, graph store
  reporting/     dashboard metrics and the monthly management report
  governance/    change register, access recertification, audit search
  ingest/        formats, datasets, mapping proposer, pipelines, storage locations, scheduler
  storage/       ObjectStore: S3, Azure Data Lake, Google Cloud Storage, local
  backbone/      outbox and CloudEvents, event bus (in-process, Kafka), relay, stream consumers, worker
  lake/          Iceberg tables and the bus consumer that fills them
  migrations/    Alembic baseline and versions; migrate.py runs and checks them
  api/           HTTP routes (the UI uses only these)
backend/case_types/  the workflow library, one YAML file per case type
backend/agents/      the agent catalog
backend/access/      starting access policies
backend/templates/   client templates: a starting policy plus a set of case types
backend/reference/   starting code lists, risk factors, ownership rules, document types, review triggers
frontend/src/
  shell/         module registry (one entry per page), role-shaped sidebar, command palette, account menu
  components/    kit (Radix, cmdk, Tailwind), data grid, case workspace, charts, layout
  pages/         one file per page
deploy/          Helm chart, Debezium connector, Flink image and notes, per-environment stamp releases
infra/           OpenTofu per cloud behind one stamp contract, and the in-cluster platform layer
```

## Documentation

| Document | For |
|---|---|
| [Demo guide](docs/demo-guide.md) | A 22-step tour by persona, plus connecting a model and overlay mode |
| [Configuration reference](docs/configuration.md) | Every setting, and what each client configures where |
| [Data model](docs/data-model.md) | The tables, how tenancy keys them, and the event topics |
| [Infrastructure as code](infra/README.md) | Standing up AWS, Azure or GCP and onboarding stamps with OpenTofu and Argo CD |
| [Helm chart](deploy/helm/caseforge/README.md) | Installing a stamp on Kubernetes, the four modes, what the chart refuses |
| [Stream detection on Flink](deploy/flink/README.md) | Running the Flink jobs and their limits |
| API reference | <http://localhost:8000/docs> on a running app |

## What is real and what is not

| Area | In this build | Not yet |
|---|---|---|
| Ingestion | Pipelines that read customers, accounts and transactions from a client's registered storage locations, through one interface for S3, Azure Data Lake, Google Cloud Storage and local directories. CSV, JSON lines and Parquet, gzip, saved and versioned mappings, quality rules per source, whole-file quarantine with rejected rows written out, count and amount reconciliation, each file loaded once, lineage from a transaction back to its file, schedules with stale-source flags. No credentials are stored: each driver uses the workload's own identity. | S3 is tested against an emulated S3 API, not AWS. The Azure and Google drivers have never been run; only their addresses are checked. On Temporal, due pipelines are started by a schedule per client (once per interval however many replicas run); without it they run from a simple in-process timer (`PIPELINE_SCHEDULER=1`, one process only) or when "Run what is due" is called. A file is read into memory whole, so very large files need splitting. A local file's fingerprint is size and modified time. Capture from a client's core database (CDC), ISO 20022, mappings compiled to dbt or Flink SQL. Streaming is the next row. |
| Event backbone | Every audit event and every new alert is written to an outbox table in the same transaction as the change, as a CloudEvents 1.0 message, and published to the client's own topics (`caseforge.<client>.case-events`, `.alerts`). One bus interface with two adapters: in-process, and Kafka or anything that speaks its protocol (connection security is plain client properties in `KAFKA_CONFIG`). Two publishers that send identical bytes: a built-in relay (at least once, in order per case or customer, several relays share work on Postgres), or Debezium reading the database log (`deploy/debezium/`). Inbound, each client's `transactions` and `alerts-in` topics are consumed in micro-batches through the same loaders and detection the file pipelines use; a redelivered batch changes nothing, and a record that cannot load goes to the client's dead-letter topic with the reason. The Integrations page shows each client its own backlog and stream totals. **Run for real here:** the bus, relay, consumers and worker against an Apache Kafka 3.9.1 broker, and Debezium 3.0.8 on Kafka Connect against Postgres 16, checked message for message against the relay (`tests/test_kafka.py`, `tests/test_cdc.py`). | The CI workflow has run on GitHub, including the Postgres start-up test and the broker tests against service containers. **Never started:** the Docker Compose services for Kafka, the worker and Debezium (`make backbone`, `make backbone-cdc`), because the build environment has no Docker daemon; their first run is on your Mac. Not run against a managed service (MSK, Event Hubs, Confluent Cloud) or with TLS and SASL. No schema registry: messages are JSON with no enforced schema. No per-client quotas or ACL set-up, so who may write to a client's inbound topic is the broker's configuration, not the application's. The relay orders by row id, which can differ from commit order when transactions overlap; Debezium does not have that gap. The application's own consumers work in Python micro-batches, so their detection re-reads each touched customer's history from the database: correct, and not the billion-a-day design. One rule also runs as a Flink job (see "Stream detection on Flink"). Entity resolution and the payment network are not refreshed from the stream; they catch up on the next file pipeline run. Events about restricted cases are published like any other: a consumer of the bus is a system, and the bus has no per-person visibility. No replay tool for the dead-letter topic. |
| History on the lake | With `LAKE_CATALOG` set, every message on a client's case-event, alert and transaction topics is also written to Apache Iceberg tables in that client's own namespace, partitioned by day. The catalog is configuration: a SQL catalog on a laptop or in the demo's Postgres, a REST catalog (Polaris, Nessie) in a deployment. Each batch is committed together with the bus positions it reached, so after a crash a redelivered message is recognised and written once. A table can be read as it stood at any earlier commit. The Integrations page lists each client's tables, rows and commits. **Run for real here:** real Iceberg tables through pyiceberg 0.12 with SQL catalogs on SQLite and Postgres 16 and a local warehouse, filled from a real Kafka broker. | Never run against a REST catalog or against object storage (S3, ADLS, GCS): only a local directory. No compaction or snapshot expiry, so small files and commits accumulate. No schema evolution. Transactions loaded by the file pipelines do not reach the lake, only those that arrive on the stream; well-formed stream transactions are kept as received, including ones the loader set aside. Nothing reads the lake yet: the management report, backtests and lookbacks still run on the operational store, and there is no query engine (Trino, Spark, DuckDB) wired in. No tokenisation of identifiers at landing and no per-client encryption keys. The first run of the Compose set-up (catalog in Postgres, tables on a volume) is still to come. |
| Detection | Four scenarios (structuring, rapid movement, high-risk geography, watchlist name match), versioned and idempotent, run after each file load and after each micro-batch from the transaction stream | All four also run on a stream processor (the Flink row below). ML models, a real sanctions list (the watchlist is fictional) |
| Alert intake | `POST /api/alerts` or the client's `alerts-in` topic for outside engines, deduplicated, grouped per customer | Vendor-specific adapters |
| Workflows | Six case types as versioned YAML: stages, queues, stage tasks, conditional steps, waiting stages, four-eyes, stage clocks with escalation, QA sampling with reopen. Checked and published from the UI. One interpreter of that configuration (`app/workflow/actions.py`) runs under either of two engines behind one interface (`app/workflow/engine.py`): `builtin`, inside the application, and `temporal`, where each case is a durable workflow (next row). | Parallel branches, a drag-and-drop designer. The case type is still interpreted by our code on both engines: Temporal executes it durably, it does not replace the configuration. |
| Casework at volume | Per-client code lists (closing reasons, QA criteria and weights, QA root causes, hibernation rules), edited on the Lists page and retired rather than deleted. Closing without a report needs a reason the client offers for that outcome. Bulk claim, reassign and move, where each case passes its own checks and refusals change nothing. Merging two open cases about one customer. QA scorecards with a pass mark, and a findings report by person, criterion, root cause and agent advice. Weak alerts that wait for a second alert, join its case, or close with the rule on record. | Approval of list changes by a second person, list versions, case splitting, undoing a merge, scheduled sweeps (waiting alerts close when the clock sweep runs) |
| KYC depth | A risk rating per customer from the client's own factors, points and bands (edited on Lists), showing the evidence for each factor. Raising a rating takes effect at once; lowering one waits for a second person holding `approve_risk`, never the proposer. Ownership structures as versions: effective shares multiplied down every chain, beneficial owners at the client's threshold or by control, untraced shares reported, and flags for trusts and nominees, loops, depth, politically exposed or unverified owners. A document store behind the same storage interface as the pipelines (`EVIDENCE_URL`), with a SHA-256 fingerprint checked on every read, a tamper-evident custody log, verification, expiry, and redacted copies of text documents. Flow of funds per customer or per case under resolved names. Events between reviews (rating rose, owners changed, a document expired, a SAR was filed, a sanctions match confirmed) open or join a review, and a schedule shows who is due one. | Identity and document verification vendors, registry lookups for ownership, OCR and redaction of scans and PDFs, virus scanning of uploads, encryption keys per client, a customer portal for collecting documents. The periodic review backlog in the demo comes from synthetic onboarding dates. |
| Fraud and sanctions depth | Watchlists per client, loaded as versions from CSV or JSON: each load is compared with what is held, removed names are kept as history, and every customer and recent payment is screened again. The client's own do-not-deal list is screened the same way. False-match exemptions with two people, an end date, and automatic ending when the list entry changes; identical names cannot be exempted. Holds on a payment, account, card or online access, with a review clock per reason; a sanctions hold is released, blocked or rejected only by a second person, and a case cannot close while a hold is active. Blocked and rejected payments produce a report to the authority through the same reports machinery, due in ten business days. Confidential searches for law enforcement requests, visible only to people with the permission, opening no case. Fraud step-up checks on a hold, and disputes with clocks per payment kind for the customer's credit, the recovery (chargeback, return, recall) and the final answer, with loss and recovery worked out per dispute and by cause. | The lists are a fictional demo list: no feed from OFAC, the UN, the EU, HMT or a commercial list vendor. Holds are records here; nothing tells a payment engine or core banking system to stop or release money. Reports are rendered and sent through the simulated channel, not OFAC's own reporting system, and the annual report of blocked property is not built. No card network chargeback or ACH return connection. Business days skip weekends only, not public holidays. Step-up checks record an outcome and send nothing to the customer. Sharing between institutions (314(b)) is not built. |
| Detection tuning and lookbacks | Each client's own settings per scenario, within the range the scenario declares, as numbered revisions holding the complete settings. A change is run against the past year first: how many alerts would not have been raised, how many of those led to a filing or a confirmed concern (listed by customer), and how many would be new. One person proposes with the backtest attached, another approves; approving a change that would have lost alerts that mattered, or switching a scenario off, takes a written reason. Alerts are stamped with the revision that raised them. Lookback projects: a past period run again through one scenario on chosen settings, counted before anything is raised, raising only what was never alerted, with every alert going to a person (no agent closure, no waiting rule), progress by case, a second person's sign-off with frozen figures, and a report to download. | Backtests and lookbacks run in the application process over the operational store, which suits the demo's volumes; at production scale they belong on the lake and the stream processor. A backtest compares alerts by customer and window, so a change to how windows are counted is approximate. No statistical above-the-line and below-the-line sampling, no segment-specific thresholds, no champion and challenger runs. Scenarios are still code: a client tunes them and switches them on or off, and does not author new ones. |
| Management reporting and governance | A monthly management report counted from the operational store: volumes, backlog at month end, time to close and deadlines met, outcomes, reports to regulators and how many were late, stage deadlines missed, open cases by age, each scenario's yield, each person's closures with QA beside them, fraud losses and sanctions decisions, with six months of trend, a spreadsheet export and a committee paper. A change register that reads every area's own version history (workflows, access, agents, detection, screening lists, reference lists) into one list showing the second person where there was one. Access recertification: a snapshot of who holds what, flags for people holding both sides of a control or showing no case activity, a decision on each person by someone else, and changes asked for tracked until the access policy shows them. Audit search across cases by person, event, case, date and words, limited to the cases the reader may open, with export. | The report is computed on request in the application, which suits the demo's volumes; at scale it belongs on the lake with a BI tool on top, and there is no scheduled delivery or report builder. Reference lists keep only their latest save, so the register shows one line for each. A recertification records decisions and does not edit the access policy or the client's identity provider. Last activity means case activity only: sign-ins are not recorded. No records retention schedule, legal hold or policy attestation tracking. |
| People and access | Each client's own versioned policy: roles, permissions, queues, people (who may hold several roles), read-only roles. Edited and published from the UI with checks: a change is refused if it would strand a workflow or lock everyone out. Every person at every kind of client is tested against every workflow step. | People and group membership synced from the client's identity provider (SCIM); today the provider proves who someone is and the policy on the Access page says what they may do. A form-based editor. |
| Case visibility | Each role can be limited to its own queues, its own cases, or some case types; people keep cases they own or have worked on, and someone with several roles sees what any of them shows. Restricted cases are hidden from everyone not cleared, including their former owner, team leads and administrators, and cases opened from a restricted case inherit it. Enforced in the data layer, so lists, case pages, actions, the event feed, dashboards, the agent ledger and Ask all follow it; a hidden case answers "not found". Tested by signing in as every person at every client and checking every case against an independent statement of the rule. | Customers and transactions are not hidden by this rule, only cases and what hangs off them, so the Data pages and a customer lookup in Ask still show a customer whose case is restricted. Alerts closed by an agent before any case existed are visible to all. Rules by customer attribute (segment, country, relationship manager's own book). Break-glass access with a reason. Postgres row-level security as a second layer. A restricted case stays in its workflow, so someone cleared must also hold the role for the next step. |
| Clients | Shared database with every row keyed by client. Isolation is enforced in the data layer: queries are filtered and writes stamped automatically, and statements that cannot be filtered are refused. Under that, Postgres row-level security: every client table carries a forced policy keyed to the transaction's client, requests run as a role the policies bind (`DATABASE_ROLE`), and a table a later migration adds is covered at the next start (`app/rls.py`). **Run for real here:** on Postgres 16, by raw statements on a client's own session that try to read, write, move and delete another client's rows, and the whole demo started and served with every request held by the policies (`tests/test_rls.py`, `tests/test_postgres.py`). Per-client case types and workflow versions. Templates, one-step onboarding, suspend, and an operator console that shows counts only. | Platform work (onboarding, the outbox relay, the operator console) sets a flag the policies let through, so it is held by the application's own rules only. Debezium reads the outbox from the write-ahead log, which row-level security does not cover. Dedicated databases or stamps per client (the choice is recorded, not acted on), per-client encryption keys, usage metering, offboarding and data export |
| Information requests | Requests to customer, branch or relationship manager with due dates and overdue flags | Actually sending them (email, portal, secure message) |
| Agents | Thirteen agents declared in `backend/agents/catalog.yaml` (goal, tools, highest autonomy, the one thing each may do alone). Each client sets autonomy per agent and case type (off, assist, prepare, act within policy) in a versioned policy, with limits, a kill switch per agent and a challenger that runs in shadow. Agents use only declared tools; filing, closing, moving, approving and exiting are not tools at any level. Findings that cite no record are dropped. News reaches the adverse media agent only as typed fields from a tool-less extractor. Every step goes to a hash-chained ledger. At L3, triage may close a low-risk alert and the sanctions adjudicator may discount a weak hit, only under settings whose evaluation on the client's own history passed, with a sampled human check and reopening. Evaluation, lookback, tuning advisor, case copilot, agreement rates per reviewer and a documentation pack for model risk. | The specialists are rule-based by default. `AGENT_MODE=model` runs them as tool loops on the model behind the gateway with the rules as fallback; that path is tested only against a scripted stand-in, never a live model. The extractor and the copilot are pattern-based, not language models, so the extractor's removal of manipulative text is a keyword filter backed by the structure (typed fields only), not a guarantee. Golden sets are the client's own closed cases, which in the demo are a few dozen synthetic ones. On Temporal, briefings and batches run as durable jobs with retries; the agents themselves run in-process inside them. Tools are Python functions, not MCP servers. The ledger is tamper-evident, not tamper-proof: it lives in the same database. |
| Ask anything | Plain-language questions answered through a fixed vocabulary of 31 fields and 9 measures, never SQL. Answers are limited to the asker's client and to the cases the asker may see, team workload follows the client's own access policy, cited cases are verified, and every question is recorded. Hosted-model provider with tool use, plus an offline planner so the demo needs no key. | The hosted-model path has only been run against a stand-in client here, never a live model: its request shape and reply handling are tested, answer quality is not. Checking figures in an answer (only case ids are checked). Streaming. Questions about transactions outside alerted activity. Spend limits. |
| Entities, network and search | Counterparty names on payments are resolved into firms: legal forms, case, accents and punctuation are ignored, spellings at least 96% alike merge, look-alikes down to 82% wait for a person, and whether a payee is one of the client's own customers or two customer records are one person is always a person's decision. Decisions are kept by name and survive every rebuild. Thresholds are per client. A customer profile with totals, counterparties, connected customers, cases and a two-hop network graph; a counterparty page; shortest path between two customers. One search box over customers, counterparties under any spelling, cases, alerts and transactions, typo-tolerant, cut to what the asker may see. | Matching uses names only: no date of birth, address, identifier or document matching, no transliteration or nickname tables, no learned model. Resolution is a full rebuild per client after each load (a third of a second for 34,000 payments here), not incremental. The network lives in relational tables behind an interface meant for a graph store. Search runs in the database unless `SEARCH_URL` names an OpenSearch node (next row). No beneficial-ownership structures, no merge of duplicate customer records (they are linked with a warning), no graph analytics (communities, cycles), no screening-list management. |
| Search on OpenSearch | With `SEARCH_URL=opensearch://host:9200`, customer and counterparty names are found by OpenSearch behind the same two-method interface the database index implements. One index per client, rebuilt whole after each entity resolution into a new index and switched in by alias, so a search never sees a half-built one. Names are normalised by the application's own functions and ranked by the same scoring as the database index, so everything the database finds the engine finds with the same score, and it also sees through accents and punctuation that a SQL pattern match cannot. The engine returns only ids; rows are read again through the caller's session, so the client boundary and case visibility do not depend on the index. If the engine is down the search box answers from the database. **Run for real here:** against an OpenSearch 2.19.1 node, 63 tests (`tests/test_opensearch.py`), and the whole entities suite with the engine switched on. | Never run with the security plugin, TLS or a managed service (Amazon OpenSearch Service): the node here was the minimal test distribution with no plugins. Only names are in the engine: cases, alerts, transactions, documents and narrative text are still found by id in the database, so there is no full-text search of case content. The index is rebuilt whole per client, not updated as records change, so a customer added without a resolution run is found only after the next one. "Contains" uses a wildcard query, which is fine for hundreds of thousands of names and would want n-grams beyond that. Near spellings stop at two edits. The Compose service has not been started; the CI job for it has its first run on this pull request. |
| Stream detection on Flink | All four rules (structuring, rapid movement, wires to high-risk countries, watchlist name matching) as Apache Flink SQL jobs (`backend/app/streamjobs/`), one job per client and rule. A job reads the client's `transactions` topic, keeps each customer's open pattern or running total in Flink state, and writes canonical alerts to the client's `alerts-in` topic, where they are de-duplicated and grouped into cases like any other engine's. A job is rendered for a client (`python -m app.streamjobs <job> <client>`) with that client's own topics and the thresholds it has approved under Detection, and every value written into the SQL is checked first. With `FLINK_URL` set, the Integrations page shows each client which of its jobs are running, lets people who can approve detection changes start, stop and restart them through Flink's SQL Gateway (`FLINK_GATEWAY_URL`), records who did, flags a job whose client's settings have changed since it started, and says so when the application is raising the same rule too. Name screening calls a small Java function built into the Flink image (`deploy/flink/udf`), the application's own name-similarity measure ported step for step and held to the Python by a test on every change; the client's lists in force, exemptions and threshold are written into the job, and the page flags the job when the lists have changed since. `make streaming` and `make flink-job CLIENT=... JOB=...` for Docker; the limits are spelled out in `deploy/flink/README.md`. **Run for real here:** all four rendered jobs on a Flink 2.1.3 cluster against an Apache Kafka 3.9.1 broker, through to an open case, including per-client thresholds, two clients side by side, out-of-order and late payments (`tests/test_flink.py`, 15 tests, two of them starting jobs from the product through a real SQL Gateway). | Name screening runs on the list it was started with until it is restarted, and a job holds at most 5,000 names and aliases, which is smaller than a full published sanctions list with aliases. The rapid movement and high-risk wire jobs raise their alert at the payment that crosses the threshold and list the payments up to then, where the application lists the whole window or month. A handful of records, never a load test, so nothing here says what volume it carries. The Docker image and Compose services (`make streaming`, `make flink-job`) have had one run, on a MacBook: seven services up, the structuring job submitted, three test deposits through to one Flink alert in a case. The three newer jobs have not been run under Docker, and the Flink image has only been built in CI since it began compiling the name-matching function. **Never run:** Flink on Kubernetes or a managed service, checkpoints to object storage, recovery after a failure. The Flink tests are not in the CI workflow (the cluster is a 570 MB download). A threshold change does not reach a running job: someone renders and resubmits. Nothing stops the application and the job both raising the rule; the page only tells you. An alert waits for later traffic on the topic before it is released, and a payment more than an hour late is not counted by the job. Starting a job from the product has run against a real SQL Gateway outside Docker only. The gateway service in Compose has never been started. A restart replays the whole topic, with no savepoint. |
| Payment network on a graph engine | With `GRAPH_URL=age://user:password@host:5432/database`, the route between two customers, the network picture around a customer and the list of related customers are walked by Apache AGE (the graph extension for Postgres) behind a four-method interface (`backend/app/entities/graphstore.py`, `age.py`). One graph per client, loaded whole after each entity resolution inside one transaction, holding ids only: no names, no amounts. The engine answers with entity ids and the rows are read again through the caller's session, so the client boundary does not depend on it. If the engine is down, not yet built for a client, slower than three seconds, or behind the database, the same answer is read from the database instead. Every value reaches the engine as a parameter. **Run for real here:** against Apache AGE 1.5.0 on Postgres 16, 14 tests (`tests/test_age.py`) including a random network on which the engine and the database agree on every distance and draw the same picture and the same related customers for every one of 120 customers, two clients side by side, and the whole entities suite with the engine switched on. | The engine walks the network and answers with ids. Names, amounts and each customer's own counterparties are still read from the database, and the engine is believed about who is two steps away, so it must be rebuilt with the relationships (it is, in the same step of each resolution). This is not a graph analytics layer: no community detection, no rings, no centrality. The graph is rebuilt whole per client, not updated as payments arrive. A route is searched one length at a time, which is quick when there is one and can run to the time limit on a dense network when there is none. A connection is opened per question, with no pool. Never run against JanusGraph (the architecture's choice for the largest clients), Neptune or a managed Postgres, most of which do not offer AGE. The Compose service is `apache/age:release_PG16_1.5.0`. |
| Case workflows on Temporal | `WORKFLOW_ENGINE=temporal` (the default when `TEMPORAL_URL` is set) runs every open case as a `CaseWorkflow`. A person's step (a transition, a task, a bulk step) reaches the case's workflow as a Temporal update and is applied by an activity that runs exactly the case type's checks, so steps on one case are applied one at a time, in order: two people pressing Approve at once can no longer both succeed. A refused step fails the update with the case type's own words and changes nothing; a step retried after its commit is recognised and not taken twice. The stage deadline is a durable timer inside the case's workflow. Anything else that moves a case (the alert hub, a merge, QA reopening it) is noticed at commit and the workflow reads the case again; the worker also adopts every open case it finds, so cases opened before Temporal, or while it was away, are picked up. Each sent report to a regulator is a `FilingWorkflow` with durable timers for chasing an overdue answer and, for an accepted SAR or STR, the continuing-activity review (the sweep applies the same follow-ups on the built-in engine). Each client gets a namespace of its own (`TEMPORAL_NAMESPACE_MODE=per_client`, registered on first use) with schedules for its sweep, for starting its due data pipelines, and for reading the regulator's answers from its inbox (`app/filing/answers.py`; the in-process scheduler does the same on the built-in engine). If Temporal is down a step is refused with a 503 and nothing changes. Longer work runs as a `JobWorkflow` with retries (`app/workflow/jobs.py`): the agents' briefing on a new case (after commit, on a worker, and a retried briefing is not written twice), running a lookback, the agents' lookback and tuning batches, and sending a report to the regulator (a channel that stumbles is tried again; a report the rules refuse is not). Two requests for the same lookback or report share one run. Everything a workflow carries (arguments, results, steps, signals, failure messages) is sealed with AES-256-GCM before Temporal sees it, with a key per namespace, so a key per client in per-client mode, and one client's codec refuses another's payloads (`app/durable/encryption.py`, `TEMPORAL_ENCRYPTION_KEY`, `TEMPORAL_ENCRYPTION=auto|required|off`, rotation by `TEMPORAL_KEY_VERSION`). Workflow code imports nothing from the application. | Run on a Temporal development server, here and in the CI workflow on GitHub, never on a Temporal cluster or Temporal Cloud: 20 tests (`tests/test_temporal.py`, each run in a shared and in a per-client namespace, all with payloads encrypted) and the whole suite with the API on the Temporal engine (`TEST_WORKFLOW_ENGINE=temporal`, 2,071 tests, per-client namespaces). Keys come from one master secret through HKDF (`DerivedKeys`), not from a KMS or Vault yet: the `KeyProvider` interface is where one plugs in. Workflow ids still name the case and client (opaque ids), and search attributes are not encrypted. Temporal's own console shows sealed payloads, because there is no codec server for it. No Temporal cluster per large client: a namespace is the unit of isolation. The Compose service is Temporal's single-process development server. |
| Schema migrations | Versioned migrations with Alembic, run through the application (`python -m app.migrate`, `backend/app/migrations`). The baseline is the schema as it stood at version 16, written out table by table, and every later change is a file after it. An empty database is built by running them; a current one is left alone; one that is behind is migrated at start, or refused when a separate step migrates (`MIGRATE_ON_START=0`, as the Helm chart sets); a database from just before migrations is adopted with its data. On Postgres an advisory lock makes pods that start together migrate one at a time. A test fails, and so does a CI step, if a model changes without a migration (`make migration m="what changed"` writes it from the difference). Tables another tool keeps in the same database, such as an Iceberg catalog, are left alone. **Run for real here:** on SQLite and on Postgres 16, including four starts at once against an empty database (`tests/test_schema.py`, `tests/test_postgres.py`). | There is one migration, the baseline, so no upgrade between two real versions has been done yet, and that first real migration is where this gets proven. Downgrades are generated and never tested. A database older than version 16 cannot be adopted: it is refused, or with `SCHEMA_ON_MISMATCH=rebuild`, the demo default, every table is dropped. No data migrations yet, no zero-downtime pattern (expand, migrate, contract), no check that a migration is safe on a large table. Per-client databases for dedicated stamps are migrated one release at a time; nothing orchestrates many. |
| Kubernetes | A Helm chart for the application tier of one stamp (`deploy/helm/caseforge`): the API and UI, the backbone worker and the durable worker, with a values file for each of the four deployment modes (pooled SaaS, dedicated stamp, client cloud, air-gapped). Data services are addressed by URL. Secrets come from a Secret you name, never from values. A release that would be unsafe fails at install with the reason: no Secret, a schema rebuild outside a demo, demo sign-in behind an ingress, no evidence storage, a wrong address. Pods are set to run as a non-root user and write only to `/tmp` and `/app/data`, both scratch volumes. Schema changes run as a hook job before each install and upgrade, and a pod that finds the database behind stops and says so. **Run for real:** `helm lint`, a render of every mode, and every refusal, in the CI workflow on GitHub. The image is built there and started as the pods' user (10001) on every change, after its first such run on a MacBook failed because the data directory belonged to root. | **Never installed on a cluster.** Rendering proves the YAML, not that pods start: the image starts as the pods' user under Docker, with SQLite and no other services, but probes, resources and the network policy are untested. The migration hook job has never run on a cluster. The chart deploys no Postgres, Kafka, OpenSearch, Temporal, Flink, graph engine, object storage, identity provider or ingress controller. OpenTofu and Argo CD now exist (see the next row); still no control plane over stamps, no metering, no signed offline bundle for air-gapped sites: the four modes are four values files over one chart, not four proven deployments. Scheduled pipelines are started by Temporal schedules when `temporal.url` is set; without Temporal nothing triggers them on Kubernetes. |
| Infrastructure as code and delivery | OpenTofu for AWS, Azure and GCP behind one stamp contract (`infra/`, see `infra/README.md`): per cloud a network, a private Kubernetes cluster (EKS, AKS, GKE), registry, keys, and per stamp a Postgres, buckets (optionally WORM), its own key and workload identity, and optionally managed Kafka and search. A cloud-neutral Kubernetes layer installs the operators (cert-manager, External Secrets, Traefik, Argo CD, Kyverno, Strimzi, OpenSearch, Flink, Prometheus), the caseforge policies, and each stamp's namespace, secrets and Argo CD applications. In-cluster Kafka and OpenSearch per stamp come from `deploy/helm/caseforge-data`. State and plans are encrypted by OpenTofu; CI signs in to each cloud by OIDC with a read-only plan identity and per-environment apply identities behind protected environments, and applies exactly the reviewed plan. Releases are scanned, signed with cosign, carry an SBOM and provenance, and are promoted by pull request. **Run for real here:** fmt, validate on 44 roots, `tofu test` with mocked providers on 11 modules, the contract check, Checkov, TFLint, strict kubeconform against the operators' CRDs, Trivy on the rendered charts, actionlint (`.github/workflows/infra.yml`). The chart install on kind is `deploy-test.yml`. | **Never applied to any cloud account**, and never planned against one. The apply, release and promote workflows have never run. Temporal and the graph engine are not provisioned. Provider lock files are not committed yet. The full list is in `infra/README.md`. |
| Regulatory reports | Each client reports in its own regime, set by platform staff: FinCEN (SAR and CTR as BSA XML) or goAML (STR and CTR). Filing a case builds one neutral report and renders it in that format; a renderer per format sits behind one small interface. Every report is checked before it can be sent (institution details, narrative length, amounts, categories) and held with its problems listed until they are fixed. A register tracks each report from prepared to sent, accepted with the regulator's reference, or rejected; amendments name the original and keep it unchanged. Once sent, the document is stored with its hash and the case narrative is locked. Cash over $10,000 in a day with no CTR is listed, and a CTR is prepared from it in one step. Reports leave through a channel per client: an outbox in the client's object storage, or by hand. Only those who send reports or oversee the team can read the register. | The XML follows the published layouts but has never been validated against FinCEN's schema files, a country's goAML schema, or either test system; the activity code tables are placeholders to confirm against the current lists. No direct connection to BSA E-Filing or a goAML site, so the regulator's answer is typed in by a person. Customer address, date of birth and identification are not held, so those fields are empty or marked unknown. No joint filings, multiple subjects, CTR aggregation across related customers or exemptions, no continuing-activity automation beyond the review date, no batch files of many reports, no other regimes (UK SAR Online, FINTRAC, AUSTRAC). |
| Identity | Two modes. Demo (default): client and person pickers, no login, anyone can pick anything. `AUTH_MODE=oidc`: OpenID Connect. Each client registers its own identity provider, the client is decided by which provider signed the token, the person by a claim in it, and request headers are ignored. Only asymmetric signatures are accepted; issuer, audience, expiry and key are checked; someone signed in but absent from the client's policy gets nothing. Platform staff sign in through a separate provider and cannot reach client data. The browser uses the authorization-code flow with PKCE. A built-in development provider (`DEV_IDP=1`) makes this demonstrable without Docker. | Never run against Keycloak, Entra ID or Okta: the verifier is tested against a stand-in provider with real keys and tokens, and the browser flow against the built-in one. No UI for registering a provider (API only). SAML, SCIM, group-to-role mapping from claims, refresh tokens and silent renewal (a session lasts as long as the token), sign-out at the provider, service accounts for machine callers (in sign-in mode `POST /api/alerts` accepts any valid token from the client's provider), MFA policy. The token is kept in the browser's session storage, not an HTTP-only cookie behind a backend-for-frontend. |
| Scale | Measured single-process throughput on your machine | Any measured distributed figure. The event backbone above is the first distributed part, and no load test has been run through it. Do not quote the laptop figure as a production figure. |

## Milestones

**1. Working slice (done).** One process, one database, end-to-end story across ingestion, detection, cases,
agents and reporting.

**2. Workflows and people (done).** The workflow engine, twelve people, queues, access policy, fraud and
onboarding journeys, QA, the workflow diagram and editor.

**3. Multi-tenancy (done).** Isolated data per client, per-client access policy and workflow versions, templates
and onboarding, a client switcher and an operator console. Tested with ten clients side by side.

**4. Ask anything (done).** Questions across the whole caseload in plain language, answered through governed
lookups, citing the cases behind them and limited to what the asker may see. A model gateway with a hosted
provider and an offline planner. Still to come here: a live-model evaluation run, streaming, and a local model.

**5. Data pipelines (done).** Sources in object storage (S3, ADLS, GCS) behind one cloud-neutral interface,
pipelines in dependency order, saved mappings per source, data-quality gates, quarantine and lineage. File
upload remains only for samples. Still to come here: a first run against real cloud accounts, an orchestrator
for schedules, streaming and CDC.

**6. Agent layer (done).** The full roster, the autonomy dial per client, agents that act inside evaluated limits,
the ledger, evaluation against the client's own decisions, challenger in shadow, copilot, lookback, tuning and
oversight. Still to come here: a run against a live model, tools as MCP servers.

**7. Case visibility and sign-in (done).** What each role can see, restricted cases, and single sign-on through
each client's own identity provider. Still to come here: a run against a real provider, SCIM, break-glass.

**8. Entities, network and search (done).** Entity resolution with a human desk for the uncertain matches, a
customer profile and network graph, counterparty pages and one search box.

**9. Regulatory reports (done).** FinCEN and goAML formats, checks before sending, a tracked life for every
report, amendments, the narrative lock and CTRs from cash days. Still to come here: schema validation and a
direct connection to the regulators' systems.

**10. Casework at volume (done).** Client code lists, closing reasons, bulk steps, case merge, QA scorecards and
findings, alert hibernation. The demo data is now identical on every run.

**11. KYC depth (done).** Governed risk ratings, beneficial ownership, a document store with chain of custody,
flow of funds, and reviews triggered by events as well as by the calendar.

**12. Fraud and sanctions depth (done).** Versioned watchlists and rescreening, false-match exemptions, holds with
two-person release, reports on blocked and rejected payments, law enforcement searches, fraud disputes and
recoveries.

**13. Detection tuning and lookbacks (done).** Per-client scenario settings with backtests and two-person
approval, and lookback projects worked to a signed conclusion.

**14. Management reporting and governance (done).** The monthly management report with exports, the change
register, access recertification and audit search.

**15. Event backbone (done).** A transactional outbox, CloudEvents on Kafka through the relay or Debezium, and each
client's transactions and third-party alerts consumed from its own topics. Still to come here: a first run of the
Compose services, a managed broker, a schema registry.

**16. History on the lake (done).** Iceberg tables per client, filled from the bus exactly once. Still to come here:
a REST catalog on object storage, compaction, and moving reporting and backtests onto it.

**17. Search on OpenSearch (done).** Name search on OpenSearch behind the existing interface, one index per client.
Still to come here: case content in the index, incremental updates, a secured cluster.

**18. Stream detection on Flink (done).** The structuring rule as a Flink SQL job per client, feeding the alert
stream. Rapid movement and high-risk wires followed in milestone 23. Starting and stopping from the product followed in milestone 24. Still to come here: checkpoints on
object storage, a load test.

**19. Payment network on a graph engine (done).** Route-finding on Apache AGE behind a graph interface, one graph
per client. The network picture and related customers followed in milestone 25. Still to come here: incremental
updates, graph analytics.

**20. Durable stage clocks on Temporal (done).** A timer workflow per case deadline
and the sweep on a schedule. Superseded by 25.

**21. Kubernetes (chart renders; never installed).** A Helm chart for the application tier with the four deployment
modes as values files.

**22. Schema migrations (done).** Versioned migrations from a written-out baseline, a guard against model changes
without one, and a hook job in the chart. Still to come: a first install on a cluster, the data services through
their operators, OpenTofu and Argo CD, a control plane over stamps, and the cloud build on the reference
architecture.

**23. More rules on Flink (done).** Rapid movement of funds and wires to high-risk countries as Flink SQL jobs,
beside structuring, sharing one definition of the topics. Watchlist name matching stays in the application.

**24. Stream jobs run from the product (done).** Start, stop and restart on the Integrations page through Flink's
SQL Gateway, with a record of each and a flag when a client's settings have moved on. The first schema migration
after the baseline (`0017`).

**25. The network picture on the graph engine (done).** Who shares a counterparty with a customer is walked in
Apache AGE for the network picture and the related-customers list, with the database as the fallback.

**26. Name screening on Flink (done).** The fourth rule as a Flink job, with the application's name-similarity
measure ported to a small Java function in the Flink image and held to the Python by a test.

**27. Case workflows on Temporal (done).** A workflow-engine interface with two engines, the built-in one and
Temporal. On Temporal each case is a durable workflow that applies people's steps one at a time and holds its stage
deadline; each sent report is a workflow chasing the regulator's answer and the continuing-activity review; each
client has its own namespace with schedules for its sweep and due pipelines. Still to come here: a real cluster.

**28. Agent runs, lookbacks and report sending on Temporal (done).** A durable job registry behind the same engine
interface. On Temporal, a new case's briefing runs on a worker after commit, and lookbacks, the agents' lookback and
tuning batches, and sending a report run as workflows with retries; the built-in engine runs them in the request.

**29. Temporal payloads encrypted per client (done).** A payload codec seals everything a workflow carries with
AES-256-GCM under a key per namespace, derived from one master secret, with rotation by key version. Still to come
here: a KMS or Vault key provider, and a codec server so Temporal's console can show payloads to platform staff.

## Screenshots

`node scripts/screenshots.mjs` regenerates `docs/screenshots/` from a running app (needs Playwright).
`node scripts/screenshots-sso.mjs` walks sign-in the same way, against an app started with `AUTH_MODE=oidc DEV_IDP=1`.
