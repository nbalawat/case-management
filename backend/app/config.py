"""Runtime settings. Everything environment-specific enters the app here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
    demo_tenants: bool = os.getenv("DEMO_TENANTS", "1") == "1"


settings = Settings()
