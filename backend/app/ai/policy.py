"""Each client's model policy: which model serves which purpose, where inference may run, and how much it may spend.

Kept in the client's settings under "ai" and changed only by platform staff, because it decides where a client's
data is sent. With no policy a client gets the deployment default: the first configured endpoint for questions
(or the offline planner), and the rule-based agents unless AGENT_MODE=model.

    ask:             endpoint id for "ask anything", or "auto" (the deployment default) or "offline"
    agents:          endpoint id for the specialist agents, or "auto" or "rules"
    fallback:        endpoint ids tried in order when the chosen one cannot be reached
    regions:         where inference may run (an endpoint's region must be one of these); empty means anywhere
    monthly_tokens:  input plus output tokens the client may use in a calendar month (UTC); 0 or empty means no limit
    question_tokens: tokens one question or one agent run may use before it is stopped; 0 or empty means no limit
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from . import endpoints as eps

PURPOSES = ("ask", "agents")
MAX_TOKENS_LIMIT = 10_000_000_000


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ModelPolicy:
    ask: str = "auto"
    agents: str = "auto"
    fallback: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    monthly_tokens: int = 0
    question_tokens: int = 0
    notes: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra")
        return d | {"fallback": list(self.fallback), "regions": list(self.regions)}


def _int(v, name: str) -> int:
    if v in (None, ""):
        return 0
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > MAX_TOKENS_LIMIT or int(v) != v:
        raise PolicyError(f"{name} must be a whole number of tokens, 0 for no limit")
    return int(v)


def parse(raw: dict | None) -> ModelPolicy:
    """Check a policy against the endpoints this server knows. Raises PolicyError with a reason a person can act on."""
    raw = dict(raw or {})
    unknown = sorted(set(raw) - {"ask", "agents", "fallback", "regions", "monthly_tokens", "question_tokens", "notes"})
    if unknown:
        raise PolicyError(f"Unknown setting(s): {', '.join(unknown)}")
    known = eps.endpoints()

    def endpoint(v, name: str, extra: tuple[str, ...]) -> str:
        v = str(v or "auto")
        if v not in known and v not in extra:
            raise PolicyError(f"{name}: no endpoint '{v}'. Choose one of: {', '.join([*extra, *[k for k in known if k not in extra]])}")
        return v

    ask = endpoint(raw.get("ask"), "ask", ("auto", "offline"))
    agents = endpoint(raw.get("agents"), "agents", ("auto", "rules"))
    if agents == "offline":
        raise PolicyError("agents: the offline planner answers questions only; use 'rules' for the rule-based agents")
    fallback = raw.get("fallback") or []
    if not isinstance(fallback, list) or any(not isinstance(f, str) for f in fallback):
        raise PolicyError("fallback must be a list of endpoint ids")
    for f in fallback:
        endpoint(f, "fallback", ())
    regions = raw.get("regions") or []
    if not isinstance(regions, list) or any(not isinstance(r, str) or not r.strip() for r in regions):
        raise PolicyError("regions must be a list of region names, for example ['us', 'local']")
    regions = tuple(dict.fromkeys(r.strip().lower() for r in regions))
    pol = ModelPolicy(ask=ask, agents=agents, fallback=tuple(dict.fromkeys(fallback)), regions=regions,
                      monthly_tokens=_int(raw.get("monthly_tokens"), "monthly_tokens"),
                      question_tokens=_int(raw.get("question_tokens"), "question_tokens"), notes=str(raw.get("notes") or "")[:500])
    for chosen in [pol.ask, pol.agents, *pol.fallback]:
        if chosen in known and chosen != "offline" and not allowed_region(pol, known[chosen]):
            raise PolicyError(f"'{chosen}' runs in region '{known[chosen].region}', which this client's residency rule "
                              f"({', '.join(regions)}) does not allow")
    return pol


def allowed_region(pol: ModelPolicy, endpoint: eps.Endpoint) -> bool:
    return endpoint.kind == "offline" or not pol.regions or endpoint.region.lower() in pol.regions


def of(settings_dict: dict | None) -> ModelPolicy:
    """The stored policy, read leniently: a policy saved before an endpoint was removed still loads."""
    raw = (settings_dict or {}).get("ai") or {}
    try:
        return parse(raw)
    except PolicyError:
        return ModelPolicy(regions=tuple(raw.get("regions") or ()), monthly_tokens=int(raw.get("monthly_tokens") or 0),
                           question_tokens=int(raw.get("question_tokens") or 0))


def for_session(s: Session) -> ModelPolicy:
    from ..tenancy import tenant_of
    from ..tenants.registry import runtime

    return of(runtime(tenant_of(s)).settings)


def deployment_default(purpose: str) -> str:
    """What "auto" means on this server, from the environment settings kept from the first release."""
    if purpose == "ask":
        choice = os.getenv("ASK_PROVIDER", settings.ask_provider)
        if choice == "offline":
            return "offline"
        if choice not in ("auto", "anthropic"):
            return choice  # an endpoint id
        ready = eps.first_ready()
        if choice == "anthropic":
            return "claude"
        return ready.id if ready else "offline"
    mode = os.getenv("AGENT_MODE", settings.agent_mode).lower()
    if mode != "model":
        return "rules"
    ready = eps.first_ready()
    return ready.id if ready else "rules"


def chosen(pol: ModelPolicy, purpose: str) -> str:
    v = pol.ask if purpose == "ask" else pol.agents
    return deployment_default(purpose) if v == "auto" else v


def set_policy(s: Session, tenant_id: str, raw: dict | None):
    """Save a client's model policy. Platform staff only, like every setting that decides where a client's data goes."""
    from ..domain.models import Tenant
    from ..tenants.onboarding import OnboardingError, _platform
    from ..tenants.registry import runtime

    _platform(s)
    row = s.get(Tenant, tenant_id)
    if row is None:
        raise OnboardingError(f"Unknown client '{tenant_id}'")
    pol = parse(raw)
    row.settings = {k: v for k, v in dict(row.settings or {}).items() if k != "ai"} | ({"ai": pol.as_dict()} if raw else {})
    s.flush()
    rt = runtime(tenant_id)
    rt.settings = dict(row.settings)
    return pol
