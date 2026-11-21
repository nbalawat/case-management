"""Which identity provider speaks for which client, and turning a verified token into a client and a person.

The client is never taken from the caller. It follows from who signed the token: each client registers its own
identity provider, and a token signed by that provider can only ever be a user of that client.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..tenants.registry import TenantRuntime, all_runtimes
from . import dev_idp
from .oidc import AuthError, Provider, Verifier, trusted_url, unverified_issuer

PERSON_CLAIMS = ("preferred_username", "email", "sub", "upn")
PLATFORM = "platform"  # the realm of the provider's own staff
verifier = Verifier()


class IdentityError(ValueError):
    pass


def check_provider(raw: dict) -> dict:
    """Validate a client's identity provider settings. Returns the cleaned form that is stored."""
    if not isinstance(raw, dict) or set(raw) - {"issuer", "audience", "client_id", "person_claim", "jwks_uri"}:
        raise IdentityError("An identity provider takes: issuer, audience, client_id, person_claim, jwks_uri")
    issuer = str(raw.get("issuer") or "").strip()
    if not trusted_url(issuer):
        raise IdentityError("issuer must be an https address")
    audience = str(raw.get("audience") or raw.get("client_id") or "").strip()
    if not audience:
        raise IdentityError("audience is required: the value the provider puts in tokens meant for this platform")
    claim = raw.get("person_claim") or PERSON_CLAIMS[0]
    if claim not in PERSON_CLAIMS:
        raise IdentityError(f"person_claim must be one of: {', '.join(PERSON_CLAIMS)}")
    jwks_uri = raw.get("jwks_uri") or None
    if jwks_uri and not trusted_url(jwks_uri):
        raise IdentityError("jwks_uri must be an https address")
    return {"issuer": issuer, "audience": audience, "client_id": str(raw.get("client_id") or audience), "person_claim": claim, "jwks_uri": jwks_uri}


def provider_of(rt: TenantRuntime) -> Provider | None:
    """The client's registered provider, or the built-in development one when that is switched on."""
    cfg = (rt.settings or {}).get("identity")
    if cfg:
        return Provider(**cfg)
    return dev_idp.provider(rt.id) if settings.dev_idp else None


def operator_provider() -> Provider | None:
    if settings.oidc_operator_issuer:
        return Provider(settings.oidc_operator_issuer, settings.oidc_operator_audience, settings.oidc_operator_claim, settings.oidc_operator_audience)
    return dev_idp.provider(PLATFORM) if settings.dev_idp else None


@dataclass(frozen=True)
class Identity:
    """A verified sign-in: a person at one client, or a member of platform staff. Never both."""
    subject: str
    tenant: TenantRuntime | None = None
    operator: bool = False


def authenticate(authorization: str | None) -> Identity:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Sign in to continue")
    token = token.strip()
    issuer = unverified_issuer(token)
    for rt in all_runtimes():
        p = provider_of(rt)
        if p is not None and p.issuer == issuer:
            return Identity(_subject(verifier.verify(token, p), p), tenant=rt)
    op = operator_provider()
    if op is not None and op.issuer == issuer:
        return Identity(_subject(verifier.verify(token, op), op), operator=True)
    raise AuthError("Token is from an identity provider no client has registered")


def _subject(claims: dict, p: Provider) -> str:
    who = claims.get(p.person_claim)
    if not isinstance(who, str) or not who:
        raise AuthError(f"Token does not say who the user is ('{p.person_claim}' is missing)")
    return who
