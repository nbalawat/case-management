"""Payload encryption for Temporal: what a workflow carries is unreadable to Temporal, its database and its UI.

Every argument, result, signal, update, memo and failure message that the application hands Temporal passes through
`EncryptionCodec` first. It is AES-256-GCM with a fresh nonce per payload, and the key id is both stored beside the
ciphertext and authenticated with it.

Keys are scoped to a namespace. With `TEMPORAL_NAMESPACE_MODE=per_client` (the default) that means a key per client:
a worker or client holding client A's codec cannot read client B's history, even with the same master key, because the
codec refuses a payload whose key is for another scope before it tries to decrypt it. In `shared` mode every client
shares the shared namespace's key.

Where keys come from is behind `KeyProvider`, so a cloud KMS or Vault can take over without touching the codec. The
built-in provider, `DerivedKeys`, derives each scope's key from one master secret with HKDF-SHA256. It needs nothing
stored besides the master secret, which lives in the deployment's Secret as `TEMPORAL_ENCRYPTION_KEY`. Rotation means
raising `TEMPORAL_KEY_VERSION`: new payloads use the new version, and older ones stay readable because their version
is in their key id.

`TEMPORAL_ENCRYPTION` decides how strict this is:
- `auto` (the default) encrypts when a master key is set.
- `required` refuses to connect without one.
- `off` never encrypts.

A plaintext payload is always decoded as it is, so history written before encryption was switched on stays readable.
"""
from __future__ import annotations

import base64
import binascii
import dataclasses
import os
from collections.abc import Sequence
from typing import Protocol

from ..config import settings
from .temporal import DurableError

ENCODING = b"binary/encrypted"
KEY_ID = "encryption-key-id"
NONCE_BYTES = 12


class EncryptionError(DurableError):
    """A key is missing or wrong, or a payload belongs to another scope."""


class KeyProvider(Protocol):
    def current(self, scope: str) -> tuple[str, bytes]:
        """The key id and 32-byte key new payloads in this scope are sealed with."""

    def key(self, key_id: str) -> bytes:
        """The key a payload names, current or older."""


def key_id(scope: str, version: int) -> str:
    return f"{scope}/v{version}"


def scope_of(kid: str) -> str:
    scope, sep, version = kid.rpartition("/v")
    if not sep or not scope or not version.isdigit():
        raise EncryptionError(f"Not a key id: {kid!r}")
    return scope


class DerivedKeys:
    """A key per scope and version, derived from one master secret with HKDF-SHA256."""

    def __init__(self, master: bytes, version: int = 1):
        if len(master) < 32:
            raise EncryptionError("The Temporal master key must be at least 32 bytes.")
        if version < 1:
            raise EncryptionError("The Temporal key version starts at 1.")
        self._master, self.version, self._keys = master, version, {}

    def current(self, scope: str) -> tuple[str, bytes]:
        kid = key_id(scope, self.version)
        return kid, self.key(kid)

    def key(self, kid: str) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        if kid not in self._keys:
            scope_of(kid)  # well-formed
            self._keys[kid] = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"caseforge-temporal-payloads",
                                   info=kid.encode()).derive(self._master)
        return self._keys[kid]


def parse_master(value: str) -> bytes:
    """The master key as configured: base64 (standard or URL-safe), at least 32 bytes once decoded."""
    text = value.strip()
    for decode in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = decode(text + "=" * (-len(text) % 4))
        except (binascii.Error, ValueError):
            continue
        if len(raw) >= 32:
            return raw
    raise EncryptionError("TEMPORAL_ENCRYPTION_KEY must be base64 of at least 32 bytes "
                          "(for example: python -c \"import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\").")


def new_master() -> str:
    """A fresh master key, in the form TEMPORAL_ENCRYPTION_KEY takes."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def provider() -> KeyProvider | None:
    """The configured key provider, or None when payloads are not encrypted."""
    mode = (settings.temporal_encryption or "auto").lower()
    if mode not in ("auto", "required", "off"):
        raise EncryptionError(f"TEMPORAL_ENCRYPTION must be auto, required or off, not {mode!r}.")
    if mode == "off":
        return None
    if not settings.temporal_encryption_key:
        if mode == "required":
            raise EncryptionError("TEMPORAL_ENCRYPTION=required, but no TEMPORAL_ENCRYPTION_KEY is set.")
        return None
    return DerivedKeys(parse_master(settings.temporal_encryption_key), settings.temporal_key_version)


def _codec_base():
    from temporalio.converter import PayloadCodec

    return PayloadCodec


class EncryptionCodec(_codec_base()):
    """Seals payloads with the scope's current key and opens only payloads sealed for this scope."""

    def __init__(self, scope: str, keys: KeyProvider):
        self.scope, self.keys = scope, keys

    async def encode(self, payloads: Sequence) -> list:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from temporalio.api.common.v1 import Payload

        kid, key = self.keys.current(self.scope)
        aead, out = AESGCM(key), []
        for p in payloads:
            nonce = os.urandom(NONCE_BYTES)
            sealed = aead.encrypt(nonce, p.SerializeToString(), kid.encode())
            out.append(Payload(metadata={"encoding": ENCODING, KEY_ID: kid.encode()}, data=nonce + sealed))
        return out

    async def decode(self, payloads: Sequence) -> list:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from temporalio.api.common.v1 import Payload

        out = []
        for p in payloads:
            if p.metadata.get("encoding") != ENCODING:
                out.append(p)  # written before encryption was switched on
                continue
            kid = p.metadata.get(KEY_ID, b"").decode()
            if scope_of(kid) != self.scope:
                raise EncryptionError(f"A payload sealed for {scope_of(kid)!r} reached the codec for {self.scope!r}.")
            try:
                plain = AESGCM(self.keys.key(kid)).decrypt(p.data[:NONCE_BYTES], p.data[NONCE_BYTES:], kid.encode())
            except InvalidTag as e:
                raise EncryptionError(f"A payload sealed with {kid!r} could not be opened: wrong key or altered data.") from e
            opened = Payload()
            opened.ParseFromString(plain)
            out.append(opened)
        return out


def converter_for(scope: str, keys: KeyProvider | None = None):
    """Temporal's default data converter, with this scope's encryption when encryption is on.

    Failure messages and stack traces are encoded as payloads too, so a refusal's wording (which can name a customer)
    is sealed like everything else.
    """
    from temporalio.converter import (
        DataConverter,
        DefaultFailureConverterWithEncodedAttributes,
    )

    keys = keys if keys is not None else provider()
    if keys is None:
        return DataConverter.default
    return dataclasses.replace(DataConverter.default, payload_codec=EncryptionCodec(scope, keys),
                               failure_converter_class=DefaultFailureConverterWithEncodedAttributes)


def describe() -> dict:
    """What the Platform page shows: whether payloads are sealed, and with which key version."""
    try:
        keys = provider()
    except EncryptionError as e:
        return {"encrypted": False, "problem": str(e)}
    if keys is None:
        return {"encrypted": False}
    return {"encrypted": True, "scope": "per_client" if settings.temporal_namespace_mode == "per_client" else "shared",
            "key_version": getattr(keys, "version", None), "provider": type(keys).__name__}


if __name__ == "__main__":  # python -m app.durable.encryption: print a new master key for TEMPORAL_ENCRYPTION_KEY
    print(new_master())
