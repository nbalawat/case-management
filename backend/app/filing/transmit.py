"""How a report leaves the building. Each channel is a small class; the client's profile names the one it uses."""
from __future__ import annotations

from typing import Protocol

from ..domain.models import Filing


class TransmitError(Exception):
    pass


class Channel(Protocol):
    id: str
    title: str

    def send(self, filing: Filing, extension: str) -> str:
        """Hand the document over. Returns a receipt a person could use to find it again."""


class Outbox:
    """Writes the document to the client's own object storage, where its secure-transfer job to the regulator picks it up."""

    id, title = "outbox", "Outbox in the client's storage, collected by its secure transfer to the regulator"

    def send(self, filing: Filing, extension: str) -> str:
        from ..ingest.locations import store_for
        from ..storage.base import StorageError

        key = f"regulatory/outbox/{filing.kind.lower()}/{filing.id}.{extension}"
        try:
            store_for(filing.tenant_id, "landing").write(key, filing.document.encode())
        except (StorageError, KeyError) as e:
            raise TransmitError(f"Could not write to the outbox: {e}") from None
        return key


class ByHand:
    """Nothing is sent. A person downloads the document and uploads it on the regulator's own site."""

    id, title = "by_hand", "Downloaded and uploaded by a person on the regulator's site"

    def send(self, filing: Filing, extension: str) -> str:
        return "downloaded for upload by hand"


CHANNELS: dict[str, Channel] = {c.id: c for c in (Outbox(), ByHand())}
