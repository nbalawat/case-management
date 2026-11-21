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
    ask_model: str = os.getenv("ASK_MODEL", "claude-opus-5-5")
    ask_effort: str = os.getenv("ASK_EFFORT", "medium")
    demo_tenants: bool = os.getenv("DEMO_TENANTS", "1") == "1"


settings = Settings()
