"""The public API: a versioned, documented interface for other teams and third parties to build on.

Mounted at /api/v1 as its own application with its own OpenAPI document. It shares the domain services with the
web interface but none of its trust: the client, the caller and what they may do come only from a verified
credential, never from a header.

    credentials.py   API client ids, secrets and access tokens; keyed hashing and encryption at rest
    scopes.py        the scope catalogue and what each needs from the access policy
    principal.py     who is calling: an API client acting under roles the client's admin chose, or a person
    clients.py       registering, rotating and revoking API clients
    guard.py         request guards: authentication, scopes, permissions, people-only decisions
    middleware.py    request ids, security headers, body limits, rate limits, idempotency, call records
    conventions.py   errors as RFC 9457 problem documents, cursor pagination, ETags
    v1/              the resources
    webhooks/        signed event delivery with retries, and the address guard
"""
