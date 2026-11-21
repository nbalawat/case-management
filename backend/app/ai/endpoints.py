"""The models a deployment can reach, read from configuration.

An endpoint is a named way to reach one model: its kind of API, the model id, where inference runs and what it
costs. The file `backend/ai/endpoints.yaml` (or AI_ENDPOINTS) lists them; environment variables supply secrets
and per-deployment addresses. Two endpoints always exist and need no configuration: `offline`, the deterministic
planner that answers without any model, and `rules`, which means "no model: use the rule-based agents".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import ROOT
from .types import ChatModel, ModelUnavailable

KINDS = ("anthropic", "openai", "offline")
BUILTIN = ("offline", "rules")


class EndpointError(ValueError):
    pass


@dataclass(frozen=True)
class Endpoint:
    id: str
    kind: str
    label: str
    model: str = ""
    region: str = "local"
    effort: str = "medium"
    base_url: str = ""
    api_key_env: str = ""
    inference_geo: str = ""
    needs: tuple[str, ...] = ()
    price_in: float | None = None  # USD per million tokens
    price_out: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """Configured well enough to call: every variable it needs is set."""
        return all(os.getenv(v) for v in self.needs)

    @property
    def missing(self) -> list[str]:
        return [v for v in self.needs if not os.getenv(v)]

    def cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.price_in is None or self.price_out is None:
            return None
        return round((input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000, 6)

    def describe(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label, "model": self.model, "region": self.region,
                "ready": self.ready, "missing": self.missing,
                "price": None if self.price_in is None else {"input": self.price_in, "output": self.price_out}}

    def connect(self) -> ChatModel:
        """A model object for this endpoint. Raises ModelUnavailable when it is not configured."""
        if not self.ready:
            raise ModelUnavailable(f"The model endpoint '{self.label}' is not configured on this server "
                                   f"(set {', '.join(self.missing)}).")
        if self.kind == "anthropic":
            from .providers.anthropic import AnthropicChatModel

            return AnthropicChatModel(model=self.model, effort=self.effort, api_key_env=self.api_key_env,
                                      inference_geo=self.inference_geo or None, endpoint=self.id)
        if self.kind == "openai":
            from .providers.openai_compat import OpenAICompatibleChatModel

            return OpenAICompatibleChatModel(base_url=self.base_url, model=self.model, api_key=os.getenv(self.api_key_env, "") if self.api_key_env else "",
                                             endpoint=self.id)
        if self.kind == "offline":
            from ..ask.providers.offline import OfflinePlanner

            return OfflinePlanner()
        raise ModelUnavailable(f"Endpoint '{self.id}' has an unknown kind '{self.kind}'")


OFFLINE = Endpoint("offline", "offline", "Offline planner (no model)", model="offline-planner", region="local", price_in=0, price_out=0)


def _env(raw: dict, key: str, default: str = "") -> str:
    name = raw.get(f"{key}_env")
    return (os.getenv(name) if name else None) or str(raw.get(key) or default)


def parse(raw: dict, origin: str = "endpoints") -> dict[str, Endpoint]:
    if not isinstance(raw, dict) or not isinstance(raw.get("endpoints"), dict):
        raise EndpointError(f"{origin}: expected a mapping with 'endpoints'")
    out: dict[str, Endpoint] = {}
    for eid, e in raw["endpoints"].items():
        if eid in BUILTIN:
            raise EndpointError(f"{origin}: '{eid}' is a built-in endpoint and cannot be redefined")
        if not isinstance(e, dict) or e.get("kind") not in KINDS:
            raise EndpointError(f"{origin}: endpoint '{eid}' needs a kind ({', '.join(KINDS)})")
        price = e.get("price") or {}
        if price and not (isinstance(price, dict) and {"input", "output"} <= set(price)):
            raise EndpointError(f"{origin}: endpoint '{eid}' price needs input and output")
        if e["kind"] == "openai" and not (e.get("base_url") or e.get("base_url_env")):
            raise EndpointError(f"{origin}: endpoint '{eid}' needs base_url or base_url_env")
        out[eid] = Endpoint(id=eid, kind=e["kind"], label=str(e.get("label") or eid), model=_env(e, "model"), region=str(e.get("region") or "local"),
                            effort=_env(e, "effort", "medium"), base_url=_env(e, "base_url").rstrip("/"), api_key_env=str(e.get("api_key_env") or ""),
                            inference_geo=str(e.get("inference_geo") or ""), needs=tuple(e.get("needs") or ()),
                            price_in=float(price["input"]) if price else None, price_out=float(price["output"]) if price else None)
    return out


def _path() -> Path:
    return Path(os.getenv("AI_ENDPOINTS", ROOT / "backend" / "ai" / "endpoints.yaml"))


@lru_cache(maxsize=4)
def _load(path: str, env_key: tuple) -> dict[str, Endpoint]:
    p = Path(path)
    found = parse(yaml.safe_load(p.read_text()) or {}, str(p)) if p.exists() else {}
    return {"offline": OFFLINE} | found


def _env_key() -> tuple:
    """Endpoints read environment variables, so the cache is keyed on the ones that matter."""
    names = ("ANTHROPIC_API_KEY", "ASK_MODEL", "ASK_EFFORT", "OPEN_MODEL_URL", "OPEN_MODEL", "OPEN_MODEL_KEY")
    return tuple(bool(os.getenv(n)) if n.endswith("KEY") else os.getenv(n, "") for n in names)


def endpoints() -> dict[str, Endpoint]:
    return _load(str(_path()), _env_key())


def get(endpoint_id: str) -> Endpoint:
    found = endpoints().get(endpoint_id)
    if found is None:
        raise ModelUnavailable(f"There is no model endpoint '{endpoint_id}' on this server. Known: {', '.join(endpoints())}")
    return found


def first_ready(exclude: tuple[str, ...] = ("offline",)) -> Endpoint | None:
    """The first hosted or self-hosted endpoint that is configured, in file order."""
    return next((e for e in endpoints().values() if e.id not in exclude and e.ready), None)


def reload() -> None:
    """Forget the parsed file, after it or the environment changed."""
    _load.cache_clear()
