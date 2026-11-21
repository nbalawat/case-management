"""Verifying OpenID Connect tokens.

Nothing here knows about clients or people: a `Provider` says who issues tokens and for whom, and `Verifier.verify`
answers with the token's claims or raises. Works with any standards-compliant provider (Keycloak, Entra ID, Okta,
Auth0, Google) because it uses only discovery, JWKS and signed JWTs.
"""
from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import jwt

# Asymmetric only. A shared-secret algorithm would let anyone holding the public key forge a token.
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")
LEEWAY_SECONDS = 30
Fetch = Callable[[str], dict]


class AuthError(Exception):
    """The caller is not signed in, or the token cannot be trusted. Always answered with 401."""


def trusted_url(url: str) -> bool:
    """HTTPS, or plain HTTP on this machine for local development."""
    u = urlparse(url or "")
    return bool(u.hostname) and (u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1")))


def http_fetch(url: str) -> dict:
    if not trusted_url(url):
        raise AuthError("Identity provider address is not trusted")
    with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 - scheme checked above
        return json.loads(r.read())


@dataclass(frozen=True)
class Provider:
    issuer: str
    audience: str
    person_claim: str = "preferred_username"
    client_id: str = ""  # what the browser app presents when it sends the user to sign in
    jwks_uri: str | None = None  # skip discovery
    jwks: tuple | None = None  # keys held in this process (the built-in development provider)


class Verifier:
    """Checks signatures against a provider's published keys, which it caches."""

    def __init__(self, fetch: Fetch = http_fetch, ttl: int = 600, refetch_after: int = 30):
        self.fetch, self.ttl, self.refetch_after = fetch, ttl, refetch_after
        self._keys: dict[str, tuple[float, dict[str, dict]]] = {}

    def _load(self, p: Provider) -> dict[str, dict]:
        if p.jwks is not None:
            return {k["kid"]: k for k in p.jwks}
        try:
            uri = p.jwks_uri
            if not uri:
                found = self.fetch(p.issuer.rstrip("/") + "/.well-known/openid-configuration")
                if found.get("issuer") != p.issuer:
                    raise AuthError("Identity provider describes a different issuer")
                uri = found["jwks_uri"]
            if not trusted_url(uri):
                raise AuthError("Identity provider key address is not trusted")
            keys = {k["kid"]: k for k in self.fetch(uri).get("keys", []) if k.get("kid") and k.get("use", "sig") == "sig"}
        except AuthError:
            raise
        except Exception as e:  # network, JSON, shape
            raise AuthError("Identity provider could not be reached") from e
        self._keys[p.issuer] = (time.monotonic(), keys)
        return keys

    def _key(self, p: Provider, kid: str) -> dict:
        at, keys = self._keys.get(p.issuer, (0.0, {}))
        age = time.monotonic() - at
        # A key we have never seen may be a rotation, so look again, but not on every bad token.
        if p.jwks is not None or not keys or age > self.ttl or (kid not in keys and age > self.refetch_after):
            keys = self._load(p)
        if kid not in keys:
            raise AuthError("Token was signed with a key this provider does not publish")
        return keys[kid]

    def verify(self, token: str, p: Provider) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise AuthError("Not a token") from e
        if header.get("alg") not in ALGORITHMS or not header.get("kid"):
            raise AuthError("Token uses a signing method that is not accepted")
        key = jwt.PyJWK(self._key(p, header["kid"]))
        try:
            return jwt.decode(token, key.key, algorithms=[header["alg"]], audience=p.audience, issuer=p.issuer, leeway=LEEWAY_SECONDS,
                              options={"require": ["exp", "iat", "iss", "aud"]})
        except jwt.ExpiredSignatureError as e:
            raise AuthError("Sign-in has expired") from e
        except jwt.PyJWTError as e:
            raise AuthError("Token was not accepted") from e


def unverified_issuer(token: str) -> str:
    """Who the token claims to be from. Used only to choose which provider's keys to check it against."""
    try:
        return str(jwt.decode(token, options={"verify_signature": False}).get("iss") or "")
    except jwt.PyJWTError as e:
        raise AuthError("Not a token") from e
