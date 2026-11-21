"""Envelope encryption: seal and open one object under its client's key.

A sealed object is

    MAGIC (4 bytes) | header length (2 bytes, big-endian) | header (JSON) | nonce (12) | AES-256-GCM ciphertext and tag

The header holds the wrapped data key (the provider's token, which names the KEK version) and nothing secret. The
ciphertext is bound to its client and its location by GCM's associated data, so a sealed file copied to another
client's store, or to another key in the same store, does not open.

Objects written before sealing began carry no MAGIC and are returned as they are; `is_sealed` tells the two apart.
"""
from __future__ import annotations

import json
import secrets
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .base import KeyProvider, KeyServiceError

MAGIC = b"CFK1"


class Tampered(KeyServiceError):
    """The sealed bytes were changed, or belong to another client or location."""


def _aad(tenant: str, where: str) -> bytes:
    return f"caseforge|{tenant}|{where}".encode()


def is_sealed(blob: bytes) -> bool:
    return blob[:4] == MAGIC


def seal(keys: KeyProvider, tenant: str, where: str, plaintext: bytes) -> bytes:
    data_key, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
    header = json.dumps({"k": keys.wrap(tenant, data_key), "p": keys.name}, separators=(",", ":")).encode()
    return MAGIC + struct.pack(">H", len(header)) + header + nonce + AESGCM(data_key).encrypt(nonce, plaintext, _aad(tenant, where))


def header(blob: bytes) -> dict:
    (n,) = struct.unpack(">H", blob[4:6])
    return json.loads(blob[6:6 + n])


def open_sealed(keys: KeyProvider, tenant: str, where: str, blob: bytes) -> bytes:
    if not is_sealed(blob):
        return blob
    try:
        (n,) = struct.unpack(">H", blob[4:6])
        head, rest = json.loads(blob[6:6 + n]), blob[6 + n:]
    except (struct.error, ValueError):
        raise Tampered("The sealed object's header is damaged") from None
    data_key = keys.unwrap(tenant, head["k"])
    try:
        return AESGCM(data_key).decrypt(rest[:12], rest[12:], _aad(tenant, where))
    except InvalidTag:
        raise Tampered("The sealed object was changed, or belongs to another client or location") from None


def rewrap(keys: KeyProvider, tenant: str, blob: bytes) -> bytes:
    """The same object with its data key wrapped under the client's current KEK version. The data is not re-encrypted:
    after a rotation this is enough to retire the old version."""
    if not is_sealed(blob):
        raise KeyServiceError("Not a sealed object")
    (n,) = struct.unpack(">H", blob[4:6])
    head = json.loads(blob[6:6 + n])
    head["k"] = keys.wrap(tenant, keys.unwrap(tenant, head["k"]))
    new = json.dumps(head, separators=(",", ":")).encode()
    return MAGIC + struct.pack(">H", len(new)) + new + blob[6 + n:]
