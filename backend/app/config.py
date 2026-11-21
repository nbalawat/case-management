"""Runtime settings. Everything environment-specific enters the app here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from the repository's .env (the same file Docker Compose reads) into the environment,
    without overriding anything already set. Keeps secrets such as ANTHROPIC_API_KEY out of the code and the shell."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key.isidentifier() and key not in os.environ:
            os.environ[key] = value


if os.getenv("CASEFORGE_NO_DOTENV") != "1":
    _load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'caseforge.db'}")
    case_types_dir: Path = Path(os.getenv("CASE_TYPES_DIR", ROOT / "backend" / "case_types"))
    access_dir: Path = Path(os.getenv("ACCESS_DIR", ROOT / "backend" / "access"))
    templates_dir: Path = Path(os.getenv("TEMPLATES_DIR", ROOT / "backend" / "templates"))
    samples_dir: Path = Path(os.getenv("SAMPLES_DIR", ROOT / "data" / "samples"))
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock")
    # Demo convenience: the client used when a request names none. Production takes the client from the sign-in token.
    default_tenant: str = os.getenv("DEFAULT_TENANT", "meridian")
    # "Ask anything": auto uses the hosted model when ANTHROPIC_API_KEY is set, otherwise the offline planner.
    ask_provider: str = os.getenv("ASK_PROVIDER", "auto")
    # rules: specialist agents are rule-based. model: they run as tool loops on the model behind the gateway, with the rules as fallback.
    agent_mode: str = os.getenv("AGENT_MODE", "rules")
    ask_model: str = os.getenv("ASK_MODEL", "claude-opus-5-5")
    ask_effort: str = os.getenv("ASK_EFFORT", "medium")
    # Demo storage. Each client's files live under lake_dir/<client>/; rejected records go to quarantine_url/<client>/.
    lake_dir: Path = Path(os.getenv("LAKE_DIR", ROOT / "data" / "lake"))
    quarantine_url: str = os.getenv("QUARANTINE_URL", f"file://{ROOT / 'data' / 'quarantine'}")
    # Customer documents. Each client's files live under evidence_url/<client>/; any object store URL works (s3://, abfs://, gs://).
    evidence_url: str = os.getenv("EVIDENCE_URL", f"file://{ROOT / 'data' / 'evidence'}")
    # Regulator schemas. FinCEN's ship with the app. goAML schemas are published by each FIU, often on terms that forbid
    # redistributing them, so the operator downloads the client's FIU schema and points this at the folder holding it.
    goaml_schema_dir: Path = Path(os.getenv("GOAML_SCHEMA_DIR", ROOT / "data" / "schemas" / "goaml"))
    # 1: a report that could not be checked against a schema is held back. 0: it can be sent, marked as not checked.
    filing_require_schema: bool = os.getenv("FILING_REQUIRE_SCHEMA", "0") == "1"
    allow_memory_storage: bool = os.getenv("ALLOW_MEMORY_STORAGE", "0") == "1"
    pipeline_scheduler: bool = os.getenv("PIPELINE_SCHEDULER", "0") == "1"
    # demo: the client and the person come from request headers (the persona switch). oidc: only from a signed token.
    auth_mode: str = os.getenv("AUTH_MODE", "demo")
    public_url: str = os.getenv("PUBLIC_URL", "http://localhost:8000")
    # Platform staff sign in through the provider's own identity system, separate from every client's.
    oidc_operator_issuer: str = os.getenv("OIDC_OPERATOR_ISSUER", "")
    oidc_operator_audience: str = os.getenv("OIDC_OPERATOR_AUDIENCE", "caseforge")
    oidc_operator_claim: str = os.getenv("OIDC_OPERATOR_CLAIM", "preferred_username")
    # A built-in identity provider for laptops and tests. Never switch it on in a real deployment.
    dev_idp: bool = os.getenv("DEV_IDP", "0") == "1"
    # The event backbone. Unset: events wait in the outbox table. memory:// keeps them in this process (tests, a laptop).
    # kafka://host:9092[,host:9092] publishes to Kafka or anything that speaks its protocol; KAFKA_CONFIG is a JSON object
    # of extra client properties (TLS, SASL) so cloud-specific settings stay out of the code.
    event_bus_url: str = os.getenv("EVENT_BUS_URL", "")
    event_topic_prefix: str = os.getenv("EVENT_TOPIC_PREFIX", "caseforge")
    kafka_config: str = os.getenv("KAFKA_CONFIG", "")
    # Run the outbox relay and the inbound stream consumers inside the API process. One process only; a real
    # deployment runs `python -m app.backbone.worker` beside the API instead.
    backbone_workers: bool = os.getenv("BACKBONE_WORKERS", "0") == "1"
    # relay: this application publishes the outbox. debezium: change data capture does, and the relay stays off.
    outbox_publisher: str = os.getenv("OUTBOX_PUBLISHER", "relay")
    outbox_keep_days: int = int(os.getenv("OUTBOX_KEEP_DAYS", "7"))
    # What the transaction stream does with a payment for a customer or account it has never seen: reject or create.
    stream_unknown_customer: str = os.getenv("STREAM_UNKNOWN_CUSTOMER", "reject")
    # History on the lake: a JSON object of Iceberg catalog properties. Unset: no lake. On a laptop, for example
    # {"type": "sql", "uri": "sqlite:///data/lake-catalog.db", "warehouse": "file:///abs/path/data/warehouse"}.
    lake_catalog: str = os.getenv("LAKE_CATALOG", "")
    # Where backups go: any object store URL (file://, s3://, abfs://, gs://). Empty on a laptop: data/backups.
    backup_url: str = os.getenv("BACKUP_URL", f"file://{ROOT / 'data' / 'backups'}")
    # Where trained models are kept: any object store URL. Empty on a laptop: data/models.
    model_store_url: str = os.getenv("MODEL_STORE_URL", f"file://{ROOT / 'data' / 'models'}")
    # The lake writer gathers this many messages, or waits this long, before writing: fewer, larger files.
    lake_flush_rows: int = int(os.getenv("LAKE_FLUSH_ROWS", "20000"))
    lake_flush_seconds: float = float(os.getenv("LAKE_FLUSH_SECONDS", "5"))
    # Search. Unset: names are searched in the database. opensearch://host:9200[,host:9200] (opensearchs:// for TLS) puts
    # OpenSearch behind the same interface; SEARCH_CONFIG is a JSON object of extra client options (http_auth, ca_certs).
    search_url: str = os.getenv("SEARCH_URL", "")
    search_config: str = os.getenv("SEARCH_CONFIG", "")
    # The payment network on a graph engine: age://user:password@host:5432/database (Apache AGE in Postgres). Empty:
    # routes between customers are found in the operational database.
    graph_url: str = os.getenv("GRAPH_URL", "")
    # Temporal: temporal://host:7233. Set, case workflows run on it (see WORKFLOW_ENGINE): each open case is a durable
    # workflow holding its stage clock and applying people's steps one at a time. The worker
    # (python -m app.durable.worker) adopts cases opened elsewhere every TEMPORAL_SYNC_SECONDS and Temporal schedules
    # the periodic sweep every TEMPORAL_SWEEP_SECONDS. Empty: the built-in engine and the sweep.
    temporal_url: str = os.getenv("TEMPORAL_URL", "")
    temporal_namespace: str = os.getenv("TEMPORAL_NAMESPACE", "default")
    temporal_task_queue: str = os.getenv("TEMPORAL_TASK_QUEUE", "caseforge")
    temporal_sync_seconds: float = float(os.getenv("TEMPORAL_SYNC_SECONDS", "15"))
    temporal_sweep_seconds: float = float(os.getenv("TEMPORAL_SWEEP_SECONDS", "300"))
    # Where case workflows run: builtin (inside the application) or temporal (each case a durable workflow). Empty:
    # temporal when TEMPORAL_URL is set, builtin otherwise.
    workflow_engine: str = os.getenv("WORKFLOW_ENGINE", "")
    # shared: every client in TEMPORAL_NAMESPACE. per_client: each client in a namespace of its own,
    # TEMPORAL_NAMESPACE_PREFIX + client id, registered when first needed, with its own retention and history.
    temporal_namespace_mode: str = os.getenv("TEMPORAL_NAMESPACE_MODE", "per_client")
    temporal_namespace_prefix: str = os.getenv("TEMPORAL_NAMESPACE_PREFIX", "caseforge-")
    temporal_retention_days: int = int(os.getenv("TEMPORAL_RETENTION_DAYS", "30"))
    # How long a person's step waits for its case workflow before the request gives up (nothing is changed then).
    temporal_step_seconds: float = float(os.getenv("TEMPORAL_STEP_SECONDS", "20"))
    # Due data pipelines are started by a Temporal schedule this often (replaces PIPELINE_SCHEDULER on Temporal).
    temporal_pipeline_seconds: float = float(os.getenv("TEMPORAL_PIPELINE_SECONDS", "60"))
    # How often each client's regulatory inbox is read for the regulator's answers (a schedule per client on Temporal).
    temporal_answers_seconds: float = float(os.getenv("TEMPORAL_ANSWERS_SECONDS", "300"))
    # 0: the worker creates no schedules (the sweep and due pipelines are then started some other way).
    temporal_schedules: bool = os.getenv("TEMPORAL_SCHEDULES", "1") == "1"
    # What workflow ids start with, before the client. Empty: EVENT_TOPIC_PREFIX. Two deployments sharing a Temporal
    # namespace need different ones.
    temporal_id_prefix: str = os.getenv("TEMPORAL_ID_PREFIX", "")
    # Payload encryption (app/durable/encryption.py): auto encrypts when a master key is set, required refuses to run
    # without one, off never encrypts. The key belongs in the deployment's Secret, never in values or the image.
    temporal_encryption: str = os.getenv("TEMPORAL_ENCRYPTION", "auto")
    temporal_encryption_key: str = os.getenv("TEMPORAL_ENCRYPTION_KEY", "")
    temporal_key_version: int = int(os.getenv("TEMPORAL_KEY_VERSION", "1"))
    # Stream detection: the REST address of an Apache Flink cluster running the jobs in app/streamjobs. The platform
    # page shows whether each client's jobs are running.
    flink_url: str = os.getenv("FLINK_URL", "")
    # The cluster's SQL Gateway (bin/sql-gateway.sh). With it set, people with the right to approve detection changes
    # start, stop and restart a client's jobs from the Integrations page. Without it jobs are submitted by hand.
    flink_gateway_url: str = os.getenv("FLINK_GATEWAY_URL", "")
    # The Kafka brokers as the Flink cluster reaches them, when that differs from how this application does.
    flink_kafka_bootstrap: str = os.getenv("FLINK_KAFKA_BOOTSTRAP", "")
    demo_tenants: bool = os.getenv("DEMO_TENANTS", "1") == "1"
    # The first member of the provider's staff, created at start-up if the staff directory is empty, so a fresh
    # installation has someone who can open the operator console: "id:Full name". Empty: create nobody.
    bootstrap_operator: str = os.getenv("BOOTSTRAP_OPERATOR", "olivia:Olivia Grant")
    # A database holding another schema version: "rebuild" drops every table and starts again (demos), "refuse"
    # stops the application with an explanation and changes nothing. The Helm chart sets refuse.
    schema_on_mismatch: str = os.getenv("SCHEMA_ON_MISMATCH", "rebuild")
    # Bring the database to the newest migration when the application starts. Off where a separate step migrates
    # (the Helm chart): the application then refuses to start against a database that is behind.
    migrate_on_start: bool = os.getenv("MIGRATE_ON_START", "1") == "1"
    # The public API (/api/v1). Credentials are stored as keyed hashes and webhook signing secrets are encrypted at rest,
    # both under API_SECRET_KEY. Set it from the deployment's secret store; the default exists for laptops and tests only.
    api_secret_key: str = os.getenv("API_SECRET_KEY", "")
    api_token_ttl: int = int(os.getenv("API_TOKEN_TTL", "900"))  # seconds an access token lives; clients may ask for less
    api_rate_limit: int = int(os.getenv("API_RATE_LIMIT", "600"))  # default requests per minute for one API client
    api_tenant_rate_limit: int = int(os.getenv("API_TENANT_RATE_LIMIT", "3000"))  # requests per minute for all of one client's apps
    api_max_body: int = int(os.getenv("API_MAX_BODY", str(2 * 1024 * 1024)))  # bytes
    api_call_keep_days: int = int(os.getenv("API_CALL_KEEP_DAYS", "30"))
    # Deliver webhooks from inside the API process (one process only). A deployment runs `python -m app.public.webhooks.worker`.
    webhook_workers: bool = os.getenv("WEBHOOK_WORKERS", "0") == "1"
    # Webhooks go to public https addresses only. 1 also allows http and private addresses, for a laptop receiver.
    webhook_allow_private: bool = os.getenv("WEBHOOK_ALLOW_PRIVATE", "0") == "1"
    # Postgres only. The role every request's transaction runs as (SET LOCAL ROLE), so that row-level security binds
    # it even when DATABASE_URL names the tables' owner or a superuser, which RLS would otherwise let through. The
    # application creates the role (no login, no superuser, no BYPASSRLS) and grants it the tables. Empty: requests run
    # as the connecting user, and the health check reports whether that user is held by row-level security.
    database_role: str = os.getenv("DATABASE_ROLE", "")


settings = Settings()
