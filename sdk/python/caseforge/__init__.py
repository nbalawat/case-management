"""CaseForge public API client.

    from caseforge import Client
    cf = Client("https://caseforge.example.com", client_id, client_secret, scopes=["cases:read", "alerts:write"])
    for case in cf.paginate("/cases", status="open", priority="high"):
        ...
    cf.post("/alerts", {...})   # an Idempotency-Key is added, so a retried call never sends the alert twice
"""
from .client import ApiError, Client
from .webhooks import verify_webhook

__all__ = ["ApiError", "Client", "verify_webhook"]
