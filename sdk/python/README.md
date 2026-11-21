# caseforge-client (Python)

A client for the CaseForge public API v1. It gets and renews tokens, follows cursors, adds an `Idempotency-Key` to
every write so retries are safe, waits out `429` with `Retry-After`, raises `ApiError` with the server's `code` and
`request_id`, and checks webhook signatures.

```python
from caseforge import Client, verify_webhook

cf = Client("http://localhost:8000", "cfc_...", "cfs_...", scopes=["cases:read", "alerts:write", "events:read"])
for case in cf.paginate("/cases", status="open", priority="high"):
    print(case["id"], case["state_label"])

cf.post("/alerts", {"source": "my-tm", "external_id": "A-1001", "scenario": "rapid_movement",
                    "party_id": "P10005", "score": 0.82, "explanation": "Rule 12"})

events, after = cf.events(after=0, types=["case.*"])

# in your webhook receiver
assert verify_webhook(secret, request.headers, request.body)
```

Install with `pip install ./sdk/python`. The full reference is the OpenAPI document at `/api/v1/openapi.json`.
