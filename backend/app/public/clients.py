"""Registering API clients, their secrets and their tokens.

A client's administrator (someone holding manage_access) registers each application, chooses the roles it acts
under and the scopes it may ask for, and receives its secret once. Secrets rotate with an overlap so nothing has
to stop; revoking a client ends every token it holds at once.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..domain.models import new_id, now
from ..domain.personas import Persona
from ..tenancy import tenant_of
from ..tenants.registry import UnknownTenant, runtime
from . import credentials, scopes as catalogue
from .models import ApiClient, ApiClientSecret, ApiToken
from .principal import Principal, PrincipalError, app_persona, check_blocks, ip_allowed

MAX_TTL, MIN_TTL = 3600, 60
MAX_RATE = 10_000
MAX_GRACE_HOURS = 168


class ClientError(ValueError):
    pass


class AuthFailure(PermissionError):
    """Credentials were refused. The message is safe to show; it never says which part was wrong."""

    def __init__(self, message: str = "invalid_client", status: int = 401, code: str = "invalid_client"):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass(frozen=True)
class Spec:
    name: str
    roles: list[str]
    scopes: list[str]
    description: str = ""
    queues: tuple[str, ...] = ()
    allowed_ips: tuple[str, ...] = ()
    rate_limit: int = 0
    token_ttl: int = 0
    expires: datetime | None = None


def _log(c: ApiClient, by: str, what: str, **detail) -> None:
    c.history = list(c.history or []) + [{"at": now().isoformat(), "by": by, "what": what} | detail]


def _validate(s: Session, spec: Spec) -> dict:
    name = spec.name.strip()
    if not (2 <= len(name) <= 80):
        raise ClientError("Name the application in 2 to 80 characters")
    rt = runtime(tenant_of(s))
    try:
        persona = app_persona(rt.policy, "check", name, list(spec.roles), list(spec.queues))
        blocks = check_blocks(list(spec.allowed_ips))
        wanted = catalogue.check(spec.scopes)
    except (PrincipalError, catalogue.ScopeError) as e:
        raise ClientError(str(e)) from None
    if not wanted:
        raise ClientError("Choose at least one scope")
    people_only = [x for x in wanted if catalogue.CATALOGUE[x].people_only]
    if people_only:
        raise ClientError(f"{', '.join(people_only)} can only be used with a person's own token: an application cannot make those decisions on its own")
    lacking = [x for x in wanted if (p := catalogue.CATALOGUE[x].permission) and not persona.can(p)]
    if lacking:
        raise ClientError(f"The roles chosen do not allow {', '.join(lacking)}: " +
                          "; ".join(f"{x} needs '{catalogue.CATALOGUE[x].permission}'" for x in lacking))
    if spec.rate_limit and not (1 <= spec.rate_limit <= MAX_RATE):
        raise ClientError(f"Rate limit must be between 1 and {MAX_RATE} requests a minute")
    if spec.token_ttl and not (MIN_TTL <= spec.token_ttl <= MAX_TTL):
        raise ClientError(f"Token lifetime must be between {MIN_TTL} and {MAX_TTL} seconds")
    if spec.expires is not None and spec.expires <= now():
        raise ClientError("The expiry date must be in the future")
    return {"name": name, "description": spec.description.strip()[:2000], "roles": list(spec.roles), "queues": list(spec.queues),
            "scopes": wanted, "allowed_ips": blocks, "rate_limit": spec.rate_limit, "token_ttl": spec.token_ttl, "expires": spec.expires}


def _add_secret(s: Session, client_id: str) -> str:
    secret = credentials.new_secret()
    s.add(ApiClientSecret(id=new_id("sec"), client_id=client_id, digest=credentials.digest(secret), hint=credentials.hint(secret)))
    return secret


def create(s: Session, spec: Spec, by: Persona) -> tuple[ApiClient, str]:
    """Register an application. Returns the client and its secret, which is shown this once and never again."""
    fields = _validate(s, spec)
    c = ApiClient(id=credentials.new_client_id(), created_by=by.id, **fields)
    _log(c, by.id, "created", scopes=fields["scopes"], roles=fields["roles"])
    s.add(c)
    secret = _add_secret(s, c.id)
    s.flush()
    return c, secret


def get(s: Session, client_id: str) -> ApiClient:
    c = s.get(ApiClient, client_id)
    if c is None or c.tenant_id != tenant_of(s):
        raise ClientError("No such API client")
    return c


def update_client(s: Session, client_id: str, spec: Spec, by: Persona) -> ApiClient:
    c = get(s, client_id)
    if c.status == "revoked":
        raise ClientError("A revoked client cannot be changed")
    fields = _validate(s, spec)
    narrowed = not set(fields["scopes"]) >= set(c.scopes or [])
    for k, v in fields.items():
        setattr(c, k, v)
    _log(c, by.id, "changed", scopes=fields["scopes"], roles=fields["roles"])
    if narrowed:  # tokens carry their scopes: end them so nothing outlives the narrower grant
        s.execute(update(ApiToken).where(ApiToken.client_id == c.id).values(revoked=True))
    return c


def rotate(s: Session, client_id: str, by: Persona, grace_hours: int = 24) -> str:
    """A new secret. The old ones keep working for `grace_hours` (0 ends them now) so callers can switch over."""
    c = get(s, client_id)
    if c.status != "active":
        raise ClientError("Only an active client's secret can be rotated")
    if not (0 <= grace_hours <= MAX_GRACE_HOURS):
        raise ClientError(f"The overlap must be between 0 and {MAX_GRACE_HOURS} hours")
    until = now() + timedelta(hours=grace_hours)
    for old in s.scalars(select(ApiClientSecret).where(ApiClientSecret.client_id == c.id)):
        if old.expires is None or old.expires > until:
            old.expires = until
    secret = _add_secret(s, c.id)
    _log(c, by.id, "secret rotated", overlap_hours=grace_hours)
    return secret


def set_status(s: Session, client_id: str, status: str, by: Persona, reason: str = "") -> ApiClient:
    if status not in ("active", "suspended", "revoked"):
        raise ClientError("Status is active, suspended or revoked")
    c = get(s, client_id)
    if c.status == "revoked":
        raise ClientError("A revoked client stays revoked. Register a new one.")
    c.status = status
    _log(c, by.id, status, reason=reason[:300])
    if status != "active":
        s.execute(update(ApiToken).where(ApiToken.client_id == c.id).values(revoked=True))
    if status == "revoked":
        s.execute(delete(ApiClientSecret).where(ApiClientSecret.client_id == c.id))
    return c


def secrets_of(s: Session, client_id: str) -> list[dict]:
    t = now()
    return [{"id": x.id, "hint": x.hint, "created": x.created.isoformat(), "expires": x.expires.isoformat() if x.expires else None,
             "live": x.expires is None or x.expires > t, "last_used": x.last_used.isoformat() if x.last_used else None}
            for x in s.scalars(select(ApiClientSecret).where(ApiClientSecret.client_id == client_id).order_by(ApiClientSecret.created))]


def row(c: ApiClient) -> dict:
    return {"client_id": c.id, "name": c.name, "description": c.description, "roles": c.roles, "queues": c.queues, "scopes": c.scopes,
            "allowed_ips": c.allowed_ips, "rate_limit": c.rate_limit or settings.api_rate_limit,
            "token_ttl": c.token_ttl or settings.api_token_ttl, "status": c.status, "created_by": c.created_by,
            "created": c.created.isoformat(), "expires": c.expires.isoformat() if c.expires else None,
            "last_used": c.last_used.isoformat() if c.last_used else None}


# --- tokens: these run on a platform session, because a credential arrives before anyone knows the client ------------

def _usable(c: ApiClient | None, ip: str) -> ApiClient:
    if c is None or c.status != "active" or (c.expires is not None and c.expires <= now()):
        raise AuthFailure()
    try:
        rt = runtime(c.tenant_id)
    except UnknownTenant:
        raise AuthFailure() from None
    if rt.status != "live":
        raise AuthFailure("This client is suspended", 403, "access_denied")
    if not ip_allowed(ip, c.allowed_ips or []):
        raise AuthFailure("Calls from this address are not allowed for this client", 403, "access_denied")
    return c


def authenticate_client(ps: Session, client_id: str, secret: str, ip: str) -> tuple[ApiClient, ApiClientSecret]:
    """Check a client id and secret. Every failure looks the same from outside, whichever part was wrong."""
    c = ps.get(ApiClient, client_id) if client_id.startswith(credentials.CLIENT_PREFIX) else None
    t = now()
    live = [x for x in ps.scalars(select(ApiClientSecret).where(ApiClientSecret.client_id == client_id))
            if x.expires is None or x.expires > t] if c is not None else []
    supplied = credentials.digest(secret or "")
    match = next((x for x in live if hmac.compare_digest(x.digest, supplied)), None)
    if match is None:
        raise AuthFailure()
    return _usable(c, ip), match


def issue_token(ps: Session, client_id: str, secret: str, requested: list[str] | None, ip: str) -> tuple[str, ApiToken]:
    """The client-credentials grant (RFC 6749 section 4.4). Asking for no scopes grants every scope the client holds."""
    c, match = authenticate_client(ps, client_id, secret, ip)
    t = now()
    held = set(c.scopes or [])
    granted = held if not requested else set(requested)
    if not granted <= held:
        raise AuthFailure(f"Scopes not granted to this client: {', '.join(sorted(granted - held))}", 400, "invalid_scope")
    value = credentials.new_token()
    tok = ApiToken(id=new_id("tok"), tenant_id=c.tenant_id, digest=credentials.digest(value), client_id=c.id, scopes=sorted(granted),
                   issued=t, expires=t + timedelta(seconds=c.token_ttl or settings.api_token_ttl), ip=ip[:45])
    match.last_used = t
    ps.add(tok)
    return value, tok


def resolve_token(ps: Session, value: str, ip: str) -> Principal:
    tok = ps.scalars(select(ApiToken).where(ApiToken.digest == credentials.digest(value))).first()
    if tok is None or tok.revoked or tok.expires <= now():
        raise AuthFailure("The access token is not valid or has expired", 401, "invalid_token")
    c = _usable(ps.get(ApiClient, tok.client_id), ip)
    rt = runtime(c.tenant_id)
    try:
        persona = app_persona(rt.policy, c.id, c.name, list(c.roles), list(c.queues))
    except PrincipalError as e:  # the access policy changed under the client: refuse rather than guess
        raise AuthFailure(f"This client's roles no longer fit the access policy: {e}", 403, "access_denied") from None
    if c.last_used is None or (now() - c.last_used).total_seconds() > 60:
        c.last_used = now()
    return Principal("app", rt, persona, frozenset(tok.scopes or []) & frozenset(c.scopes or []), c.id, tok.id, c.rate_limit or settings.api_rate_limit)


def revoke_token(ps: Session, value: str, client_id: str) -> None:
    """RFC 7009: revoking an unknown or someone else's token succeeds quietly and changes nothing."""
    tok = ps.scalars(select(ApiToken).where(ApiToken.digest == credentials.digest(value))).first()
    if tok is not None and tok.client_id == client_id:
        tok.revoked = True


def prune(ps: Session, older_than_hours: int = 24) -> int:
    """Expired tokens are useless; keep a day of them for investigation, then drop them."""
    cut = now() - timedelta(hours=older_than_hours)
    return ps.execute(delete(ApiToken).where(ApiToken.expires < cut)).rowcount or 0
