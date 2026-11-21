"""OpenBao (or HashiCorp Vault) Transit: the key never leaves the key service; the application only asks it to wrap
and unwrap data keys.

One Transit key per client, named `<prefix><client>`, of type aes256-gcm96. Transit versions keys itself: a wrapped
token reads `vault:v3:...`, and decrypting it uses version 3 whatever the current version is. Destroying a client's
key allows deletion on it and deletes it, which Transit does for every version at once.

Settings: TRANSIT_ADDR (https://bao.example:8200), TRANSIT_TOKEN, TRANSIT_MOUNT (default transit), TRANSIT_PREFIX
(default caseforge-). The token needs create, update, read and delete on <mount>/keys/<prefix>*, and update on
<mount>/encrypt/<prefix>*, <mount>/decrypt/<prefix>*, <mount>/keys/<prefix>*/rotate and /config.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime

import httpx

from .base import KeyDestroyed, KeyInfo, KeyServiceError

_TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class TransitKeyProvider:
    name = "transit"

    def __init__(self, addr: str, token: str, mount: str = "transit", prefix: str = "caseforge-", client: httpx.Client | None = None):
        if not addr:
            raise KeyServiceError("TRANSIT_ADDR is not set")
        self.addr, self.mount, self.prefix = addr.rstrip("/"), mount.strip("/"), prefix
        self.http = client or httpx.Client(timeout=10.0)
        self.headers = {"X-Vault-Token": token}

    def _key(self, tenant: str) -> str:
        if not _TENANT.match(tenant or ""):
            raise KeyServiceError(f"'{tenant}' is not a client id")
        return f"{self.prefix}{tenant}"

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            r = self.http.request(method, f"{self.addr}/v1/{self.mount}/{path}", headers=self.headers, json=body)
        except httpx.HTTPError as e:
            raise KeyServiceError(f"The key service at {self.addr} did not answer: {type(e).__name__}") from None
        if r.status_code == 404:
            return {}
        if r.status_code >= 400:
            errors = "; ".join(r.json().get("errors", [])) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
            raise KeyServiceError(f"The key service refused {method} {path}: {errors or r.status_code}")
        return r.json() if r.content else {}

    def _read(self, tenant: str) -> dict:
        return self._call("GET", f"keys/{self._key(tenant)}").get("data") or {}

    def create(self, tenant: str) -> KeyInfo:
        if not self._read(tenant):
            self._call("POST", f"keys/{self._key(tenant)}", {"type": "aes256-gcm96", "exportable": False, "allow_plaintext_backup": False})
        return self.info(tenant)

    def wrap(self, tenant: str, data_key: bytes) -> str:
        # Transit makes a key on encrypt if there is none ("upsert"), which would quietly give a client whose key was
        # destroyed a new one. So the key must exist first. Setting disable_upsert on the mount closes the same door
        # on the server's side; this check holds whether or not the operator did.
        if not self._read(tenant):
            raise self._missing(tenant)
        out = self._call("POST", f"encrypt/{self._key(tenant)}", {"plaintext": base64.b64encode(data_key).decode()})
        if not out:
            raise self._missing(tenant)
        return out["data"]["ciphertext"]

    def unwrap(self, tenant: str, token: str) -> bytes:
        try:
            out = self._call("POST", f"decrypt/{self._key(tenant)}", {"ciphertext": token})
        except KeyServiceError:
            if not self._read(tenant):  # Transit answers a missing key with 400 "encryption key not found"
                raise self._missing(tenant) from None
            raise
        if not out:
            raise self._missing(tenant)
        return base64.b64decode(out["data"]["plaintext"])

    def rotate(self, tenant: str) -> KeyInfo:
        if not self._read(tenant):
            raise self._missing(tenant)
        self._call("POST", f"keys/{self._key(tenant)}/rotate")
        return self.info(tenant)

    def destroy(self, tenant: str) -> KeyInfo:
        if self._read(tenant):
            self._call("POST", f"keys/{self._key(tenant)}/config", {"deletion_allowed": True})
            self._call("DELETE", f"keys/{self._key(tenant)}")
        return KeyInfo(tenant, self.name, 0, destroyed=True, reference=self._key(tenant))

    def info(self, tenant: str) -> KeyInfo:
        data = self._read(tenant)
        if not data:
            return KeyInfo(tenant, self.name, 0, reference=self._key(tenant))
        times = data.get("keys") or {}
        created = min((self._when(v) for v in times.values()), default=None)
        latest = int(data.get("latest_version") or 0)
        rotated = self._when(times.get(str(latest))) if latest > 1 else None
        return KeyInfo(tenant, self.name, latest, created, rotated, False, f"{self.mount}/keys/{self._key(tenant)}")

    def _missing(self, tenant: str) -> KeyServiceError:
        return KeyDestroyed(f"{tenant}'s key is not in the key service: it was destroyed, or never made")

    @staticmethod
    def _when(v) -> datetime | None:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None
