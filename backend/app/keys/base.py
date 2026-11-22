"""The key-management contract: one key-encryption key per client, held by a key service, never by the application.

Client data is sealed with envelope encryption (app/keys/envelope.py): each object gets its own data key, the data
key is wrapped by the client's key-encryption key (KEK), and only the wrapped form is stored beside the data. The KEK
never leaves the key service, so whoever holds the service decides who can read a client's data:

  * rotate   new data is sealed under a new KEK version; older versions still unwrap what they sealed.
  * destroy  the KEK is gone, so nothing sealed under it can ever be opened again (crypto-shredding). This is how a
             client's data is erased at offboarding even from backups and object versions nobody can find.

Providers: `local` (a keyring on disk under a master key, for development and single-node installs), `transit`
(OpenBao or HashiCorp Vault's Transit engine, the open-source key service) and `aws-kms` (AWS Key Management Service).
Azure Key Vault and Google Cloud KMS are one more class each with these six methods, selected the same way in
`keys.provider()`; nothing else in the application changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class KeyServiceError(RuntimeError):
    """The key service could not do what was asked. Safe to show an operator."""


class KeyDestroyed(KeyServiceError):
    """The client's key was destroyed: what it sealed cannot be opened, by design."""


@dataclass(frozen=True)
class KeyInfo:
    tenant: str
    provider: str
    version: int  # the version new data is sealed under; 0 when there is no key
    created: datetime | None = None
    rotated: datetime | None = None
    destroyed: bool = False
    reference: str = ""  # where the key lives, for the operator: a file, a Transit key name

    def describe(self) -> str:
        if self.destroyed:
            return "destroyed: data sealed under it can no longer be read"
        if not self.version:
            return "no key yet"
        return f"version {self.version} in {self.provider}"


class KeyProvider(Protocol):
    name: str

    def create(self, tenant: str) -> KeyInfo:
        """Make the client's key if it does not exist. Idempotent."""
        ...

    def wrap(self, tenant: str, data_key: bytes) -> str:
        """Encrypt a data key under the client's current KEK. The token carries the KEK version."""
        ...

    def unwrap(self, tenant: str, token: str) -> bytes:
        """Recover a data key wrapped by `wrap`, under whichever version wrapped it."""
        ...

    def rotate(self, tenant: str) -> KeyInfo: ...

    def destroy(self, tenant: str) -> KeyInfo:
        """Destroy every version of the client's KEK. Irreversible."""
        ...

    def info(self, tenant: str) -> KeyInfo: ...
