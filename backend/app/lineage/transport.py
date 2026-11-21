"""Where events go besides the platform's own table: an OpenLineage HTTP endpoint (Marquez, DataHub, OpenMetadata,
a cloud catalog's OpenLineage API) or a file of JSON lines. LINEAGE_URL picks: http(s)://host/api/v1/lineage, or
file:///path/events.jsonl. LINEAGE_API_KEY, when set, goes as a bearer token."""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request

log = logging.getLogger("caseforge.lineage")
_lock = threading.Lock()


def send(event: dict, url: str | None = None) -> bool:
    url = os.getenv("LINEAGE_URL", "") if url is None else url
    if not url:
        return False
    body = json.dumps(event, separators=(",", ":")).encode()
    try:
        if url.startswith("file://"):
            with _lock, open(url.removeprefix("file://"), "ab") as f:
                f.write(body + b"\n")
            return True
        if url.startswith(("http://", "https://")):
            headers = {"Content-Type": "application/json"}
            if os.getenv("LINEAGE_API_KEY"):
                headers["Authorization"] = f"Bearer {os.environ['LINEAGE_API_KEY']}"
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310  the operator sets the URL
                r.read()
            return True
        log.warning("LINEAGE_URL must be http(s):// or file://, not %s", url.split("://")[0])
    except Exception as e:  # noqa: BLE001  lineage never stops the work it describes
        log.warning("Lineage event for %s not sent: %s", event.get("job", {}).get("name"), e)
    return False
