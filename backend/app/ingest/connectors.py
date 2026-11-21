"""Source connectors. Each connector yields raw records as dicts; mapping happens downstream."""
from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol


class Connector(Protocol):
    name: str

    def columns(self) -> list[str]: ...
    def records(self) -> Iterator[dict]: ...


class CsvConnector:
    """Delimited file or text. Stands in for SFTP and object-storage drops in the demo."""

    def __init__(self, name: str, text: str):
        self.name = name
        self._text = text

    @classmethod
    def from_path(cls, path: Path) -> "CsvConnector":
        return cls(path.name, path.read_text())

    def columns(self) -> list[str]:
        return next(csv.reader(io.StringIO(self._text)), [])

    def records(self) -> Iterator[dict]:
        yield from csv.DictReader(io.StringIO(self._text))


# Catalogue shown on the Integrations page. "live" connectors run in this demo build.
CONNECTOR_CATALOGUE = [
    {"id": "csv", "name": "File drop (CSV)", "kind": "batch", "status": "live", "note": "SFTP or object storage via the S3 API"},
    {"id": "rest", "name": "REST and webhooks", "kind": "stream", "status": "live", "note": "This API accepts alerts and case actions"},
    {"id": "cdc", "name": "Database CDC (Debezium)", "kind": "stream", "status": "planned", "note": "Outbound capture of this platform's own outbox is configured; capture from a client's core database is not built"},
    {"id": "kafka", "name": "Kafka topics", "kind": "stream", "status": "live", "note": "Each client's transactions and third-party alerts, when an event bus is connected"},
    {"id": "iso20022", "name": "ISO 20022 and SWIFT MT", "kind": "stream", "status": "planned", "note": "Milestone 3"},
    {"id": "tm-alerts", "name": "Third-party alert engines", "kind": "stream", "status": "live", "note": "Canonical alert format via POST /api/alerts"},
]
