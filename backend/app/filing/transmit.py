"""How a report leaves the building. Each channel is a small class; the client's profile names the one it uses.

Two channels work today: the outbox (the client's own secure transfer collects the file) and by hand. The direct
connections to regulators' systems, FinCEN's BSA E-Filing secure direct transfer and an FIU's goAML web service, are
adapters with the same shape that refuse cleanly: each needs an account the regulator issues to the filer, its
credentials and the regulator's test cycle, none of which this platform has. Connecting one is filling in `send`.
"""
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


class NotConnected:
    """A direct connection to a regulator's own system. It refuses until it has been set up with the regulator."""

    def __init__(self, id: str, title: str, needs: str):
        self.id, self.title, self.needs = id, title, needs

    def send(self, filing: Filing, extension: str) -> str:
        raise TransmitError(f"{self.title} is not connected on this platform. It needs {self.needs}. "
                            "Until then, use the outbox or send the report by hand.")


CHANNELS: dict[str, Channel] = {c.id: c for c in (
    Outbox(), ByHand(),
    NotConnected("fincen_sdtm", "FinCEN BSA E-Filing secure direct transfer (SFTP)",
                 "a BSA E-Filing account approved for system-to-system filing, its transmitter control code and SFTP credentials, "
                 "and passing FinCEN's test-environment submissions"),
    NotConnected("goaml_web_service", "The FIU's goAML web service",
                 "an organisation registered with the FIU on goAML Web, a web service user and certificate the FIU issues, "
                 "and the FIU's test site"),
)}
