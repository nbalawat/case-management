# Public API (v1)

CaseForge exposes a versioned public API at `/api/v1` so other teams and third parties can build on the platform:
case portals, warehouse syncs, workforce tools, partner monitoring engines, chat assistants. It is a separate
application from the web interface's internal API, with its own credentials, OpenAPI document and conventions, and it
trusts nothing the caller says about who they are.

- Interactive reference: `/api/v1/docs`. OpenAPI 3.1 document: `/api/v1/openapi.json`.
- In the product: **Administration → Developer** (needs the *manage access* permission).
- Clients: `sdk/python` (`caseforge-client`) and `sdk/typescript` (`@caseforge/client`); or generate one from the spec.

## Who can call, and as whom

| Caller | Credential | Acts as | Can |
|---|---|---|---|
| Application | OAuth 2.0 client credentials (`POST /api/v1/oauth/token`), opaque token, 15 minutes by default, at most 1 hour | Roles from the client's own access policy, chosen by its administrator, narrowed by scopes | Read, send alerts, run pipelines, ask, brief cases, assign, webhooks |
| Person | Their own token from the client's identity provider (OIDC JWT), sent by the application they use | Exactly who they are on the web interface | Everything their roles allow, including the decisions below |

**People decide.** Moving a case, completing its tasks, writing its narrative, raising information requests, and
sending or answering filings are scopes marked *people only*. An application token can never hold them; the workflow's
four-eyes and segregation rules apply unchanged to people calling through the API.

**Never for applications.** A role that grants *manage access* or *recertify access* cannot be given to an application,
and applications never see restricted cases, whatever their roles say.

## Security controls

| Control | How |
|---|---|
| Tenant isolation | The client always follows from the credential (token lookup or the identity provider that signed the JWT). Headers such as `X-Tenant` are ignored. Every query runs in a tenant session (`app/tenancy.py`). |
| Role-based visibility | Every case read goes through the viewer filter (`app/visibility.py`) for the caller's roles; an unseen case is a 404. |
| Least privilege | Scopes only narrow; each write also needs the access-policy permission a person would need. |
| Credential storage | Client secrets and tokens are stored only as HMAC-SHA256 under `API_SECRET_KEY`; webhook signing secrets are encrypted (Fernet) under a key derived from it. Secrets are shown once, have recognisable prefixes (`cfs_`, `cfat_`, `whsec_`) for secret scanners, and rotate with an overlap. |
| Revocation | Suspending, revoking or narrowing an application ends its tokens at once; RFC 7009 token revocation. |
| Network | Optional per-application IP allowlists (CIDR). |
| Abuse | Token-bucket rate limits per caller and per client (`RateLimit-*`, `Retry-After`); 2 MB body cap; 500-item batch cap. |
| Errors | RFC 9457 problem documents with a stable `code` and `request_id`; no stack traces or internal messages leak. Credential failures never say which part was wrong. |
| Headers | `Cache-Control: no-store`, HSTS, `nosniff`, `X-Frame-Options: DENY`, a restrictive CSP, request ids. |
| Audit | Every call is recorded (caller, route template, status, latency, address); every change also lands on the case's append-only audit trail with the caller's id (`app:<client_id>` or the person). |
| Webhook SSRF | https only; private, loopback, link-local, carrier-NAT and metadata addresses refused at registration and again at delivery against the resolved address (DNS rebinding); the connection is pinned to the checked address; redirects are not followed. |
| Webhook integrity | Standard Webhooks signatures (HMAC-SHA256 over id, timestamp and body), replay window, both secrets during rotation. |

## Conventions

- **Pagination**: `limit` and an opaque `cursor`; responses carry `next_cursor` and `has_more`. Keyset-based, so deep pages stay fast.
- **Idempotency**: send `Idempotency-Key` on any write; a retry within 24 hours returns the first answer (`Idempotent-Replayed: true`), a different body with the same key is refused.
- **Concurrency**: case reads return an `ETag`; send it as `If-Match` on a write to get 412 instead of overwriting someone's change.
- **Versioning**: `/api/v1` changes only by adding fields and endpoints. Anything breaking goes into `/api/v2`.

## Resources

Cases (list, read, audit trail, alerts, transactions, children, transitions, narrative, tasks, information requests,
assignment, agent briefings) · Alerts (list, read, send, batch) · Customers (list, search, profile, accounts, KYC,
transactions, cases, network, paths) · Transactions (list, read, lineage) · Regulatory filings (register, read, document,
submission, acknowledgement, rejection) · Screening (lists, name checks) · Case types · Ask anything · Data pipelines
(sources, runs) · Metrics · Events (feed, types) · Webhooks (register, change, pause, rotate, test, deliveries, retry).

## Events and webhooks

Every committed change already becomes a CloudEvents 1.0 message in the transactional outbox. `GET /api/v1/events`
reads it in commit order with a `sequence` to resume from; webhooks deliver the same envelopes. Both show a caller
only what it may see: case events need `cases:read` and a visible case, alert events need `alerts:read`.

Deliveries are retried after 30 s, 2 min, 10 min, 30 min, 1 h, 3 h, 6 h, 12 h and 24 h. An endpoint failing 50
attempts in a row is switched off with the reason; failed deliveries can be retried by hand. Run the worker with
`python -m app.public.webhooks.worker`, or inside the API with `WEBHOOK_WORKERS=1`.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `API_SECRET_KEY` | development key (refused when `AUTH_MODE=oidc` without `DEV_IDP`) | Key for credential hashes and webhook secret encryption; at least 32 characters, from the deployment's secret store |
| `API_TOKEN_TTL` | 900 | Access token lifetime in seconds |
| `API_RATE_LIMIT` / `API_TENANT_RATE_LIMIT` | 600 / 3000 | Requests a minute per application / per client |
| `API_MAX_BODY` | 2097152 | Largest request body in bytes |
| `WEBHOOK_WORKERS` | 0 | Deliver webhooks from inside the API process |
| `WEBHOOK_ALLOW_PRIVATE` | 0 | Allow http and private addresses (a laptop receiver only) |

The rate limiter is an interface (`app/public/ratelimit.py`); a shared store such as Redis or Valkey slots in for
multi-replica deployments.
