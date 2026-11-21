"""A keyring on disk: one file per client holding every version of its key-encryption key, each sealed by a master key.

For development, the demo and single-node installs. The master key comes from KEYS_MASTER_KEY (32 bytes, base64); when
that is not set a random one is written next to the keyring on first use, which keeps the demo working but puts the
master key beside what it protects. A production install points KEY_PROVIDER at a key service instead (transit).

Destroying a client's key overwrites its file and leaves a tombstone saying when, so a later attempt to read reports
"destroyed" rather than "missing".
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .base import KeyDestroyed, KeyInfo, KeyServiceError

log = logging.getLogger(__name__)
_TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_lock = threading.Lock()


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class LocalKeyProvider:
    name = "local"

    def __init__(self, directory: Path, master_key: str = ""):
        self.dir = Path(directory)
        self._master_b64 = master_key

    # --- the master key -------------------------------------------------------------------------------------------------
    def _master(self) -> AESGCM:
        raw = self._master_b64
        if not raw:
            path = self.dir / "master.key"
            if not path.exists():
                self.dir.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(_b64(secrets.token_bytes(32)))
                log.warning("No KEYS_MASTER_KEY: wrote a random master key to %s. Fine for a demo; use a key service in production.", path)
            raw = path.read_text().strip()
        try:
            key = base64.b64decode(raw, validate=True)
        except ValueError:
            raise KeyServiceError("KEYS_MASTER_KEY is not base64") from None
        if len(key) != 32:
            raise KeyServiceError("KEYS_MASTER_KEY must be 32 bytes")
        return AESGCM(key)

    # --- one client's keyring file --------------------------------------------------------------------------------------
    def _path(self, tenant: str) -> Path:
        if not _TENANT.match(tenant or ""):
            raise KeyServiceError(f"'{tenant}' is not a client id")
        return self.dir / f"{tenant}.json"

    def _load(self, tenant: str) -> dict | None:
        p = self._path(tenant)
        return json.loads(p.read_text()) if p.exists() else None

    def _save(self, tenant: str, ring: dict) -> None:
        p = self._path(tenant)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(ring, f)
        os.replace(tmp, p)  # a reader sees the old ring or the new one, never half of one

    def _new_version(self, tenant: str, ring: dict) -> None:
        version = max((int(v) for v in ring["versions"]), default=0) + 1
        kek, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
        sealed = self._master().encrypt(nonce, kek, f"{tenant}:{version}".encode())
        ring["versions"][str(version)] = _b64(nonce + sealed)
        ring["current"] = version

    def _kek(self, tenant: str, ring: dict, version: int) -> AESGCM:
        blob = ring["versions"].get(str(version))
        if blob is None:
            raise KeyServiceError(f"{tenant}'s key has no version {version}")
        raw = base64.b64decode(blob)
        return AESGCM(self._master().decrypt(raw[:12], raw[12:], f"{tenant}:{version}".encode()))

    def _ring(self, tenant: str) -> dict:
        ring = self._load(tenant)
        if ring is None:
            raise KeyServiceError(f"{tenant} has no key. Onboarding creates one.")
        if ring.get("destroyed"):
            raise KeyDestroyed(f"{tenant}'s key was destroyed on {ring['destroyed']}")
        return ring

    # --- the contract -----------------------------------------------------------------------------------------------------
    def create(self, tenant: str) -> KeyInfo:
        with _lock:
            ring = self._load(tenant)
            if ring is None:
                ring = {"tenant": tenant, "versions": {}, "current": 0, "created": _now(), "rotated": None, "destroyed": None}
                self._new_version(tenant, ring)
                self._save(tenant, ring)
            elif ring.get("destroyed"):
                raise KeyDestroyed(f"{tenant}'s key was destroyed on {ring['destroyed']}; a destroyed client is not given a new key")
        return self.info(tenant)

    def wrap(self, tenant: str, data_key: bytes) -> str:
        ring = self._ring(tenant)
        version, nonce = ring["current"], secrets.token_bytes(12)
        sealed = self._kek(tenant, ring, version).encrypt(nonce, data_key, tenant.encode())
        return f"local:v{version}:{_b64(nonce + sealed)}"

    def unwrap(self, tenant: str, token: str) -> bytes:
        ring = self._ring(tenant)
        try:
            kind, version, blob = token.split(":", 2)
            assert kind == "local" and version.startswith("v")
            raw = base64.b64decode(blob)
            return self._kek(tenant, ring, int(version[1:])).decrypt(raw[:12], raw[12:], tenant.encode())
        except KeyServiceError:
            raise
        except Exception:
            raise KeyServiceError("A data key could not be unwrapped with this client's key") from None

    def rotate(self, tenant: str) -> KeyInfo:
        with _lock:
            ring = self._ring(tenant)
            self._new_version(tenant, ring)
            ring["rotated"] = _now()
            self._save(tenant, ring)
        return self.info(tenant)

    def destroy(self, tenant: str) -> KeyInfo:
        with _lock:
            ring = self._load(tenant) or {"tenant": tenant, "created": None}
            if not ring.get("destroyed"):
                stamp = _now()
                p = self._path(tenant)
                if p.exists():  # overwrite before replacing, so the old key bytes are not simply unlinked
                    p.write_bytes(secrets.token_bytes(max(p.stat().st_size, 64)))
                self._save(tenant, {"tenant": tenant, "versions": {}, "current": 0, "created": ring.get("created"),
                                    "rotated": ring.get("rotated"), "destroyed": stamp})
        return self.info(tenant)

    def info(self, tenant: str) -> KeyInfo:
        ring = self._load(tenant)
        if ring is None:
            return KeyInfo(tenant, self.name, 0, reference=str(self._path(tenant)))
        when = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
        return KeyInfo(tenant, self.name, int(ring.get("current") or 0), when(ring.get("created")), when(ring.get("rotated")),
                       bool(ring.get("destroyed")), str(self._path(tenant)))
