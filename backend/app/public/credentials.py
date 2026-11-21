"""Credential material for the public API.

Every credential is a high-entropy random string with a recognisable prefix, so secret scanners can find a leaked
one. Nothing reversible is stored for client secrets or tokens: the database holds an HMAC-SHA256 of each under
the deployment's API_SECRET_KEY, so a copy of the database alone cannot be used to call the API. Webhook signing
secrets must be readable to sign with, so they are encrypted (Fernet: AES-128-CBC with HMAC-SHA256) under a key
derived from the same secret.

The key comes from configuration, which a deployment fills from its own secret store (Vault, a cloud KMS, a
Kubernetes secret). Nothing here knows which.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

CLIENT_PREFIX, SECRET_PREFIX, TOKEN_PREFIX, WEBHOOK_SECRET_PREFIX = "cfc_", "cfs_", "cfat_", "whsec_"
_DEV_KEY = "caseforge-development-only-api-secret-key"
log = logging.getLogger(__name__)
_warned = False


class CredentialError(ValueError):
    pass


def _key() -> bytes:
    global _warned
    if settings.api_secret_key:
        if len(settings.api_secret_key) < 32:
            raise CredentialError("API_SECRET_KEY must be at least 32 characters")
        return settings.api_secret_key.encode()
    if settings.auth_mode == "oidc" and not settings.dev_idp:
        raise CredentialError("API_SECRET_KEY is not set. A deployment with sign-in switched on must set it.")
    if not _warned:
        log.warning("API_SECRET_KEY is not set: using the development key. Never do this in a real deployment.")
        _warned = True
    return _DEV_KEY.encode()


def digest(value: str) -> str:
    """The keyed hash a credential is stored and looked up by."""
    return hmac.new(_key(), value.encode(), hashlib.sha256).hexdigest()


def matches(value: str, stored: str) -> bool:
    return hmac.compare_digest(digest(value), stored)


def new_client_id() -> str:
    return CLIENT_PREFIX + secrets.token_hex(10)


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def new_webhook_secret() -> str:
    return WEBHOOK_SECRET_PREFIX + base64.b64encode(secrets.token_bytes(32)).decode()


def hint(value: str) -> str:
    return value[-4:]


def _box() -> Fernet:
    derived = hashlib.sha256(b"caseforge-webhook-secrets:" + _key()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def seal(value: str) -> str:
    return _box().encrypt(value.encode()).decode()


def unseal(sealed: str) -> str:
    try:
        return _box().decrypt(sealed.encode()).decode()
    except InvalidToken:
        raise CredentialError("A stored secret cannot be read with this deployment's API_SECRET_KEY") from None
