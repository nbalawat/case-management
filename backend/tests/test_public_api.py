"""The public API: credentials, isolation, role-based visibility, people-only decisions, the conventions and webhooks."""
import base64
import json

import pytest

from app.auth import dev_idp
from app.config import settings
from app.public import ratelimit
from app.public.webhooks import service as webhooks, signing

V1 = "/api/v1"
ADMIN = {"meridian": "omar", "kestrel": "pat", "alder": None}


def admin(tenant="meridian"):
    return {"X-Tenant": tenant, "X-Persona": ADMIN[tenant]}


def register(client, tenant="meridian", **kw):
    body = {"name": "Test app", "roles": ["investigator"], "scopes": ["cases:read", "alerts:read", "alerts:write", "customers:read",
                                                                       "transactions:read", "events:read", "webhooks:manage"]} | kw
    r = client.post("/api/developer/clients", json=body, headers=admin(tenant))
    assert r.status_code == 201, r.text
    return r.json()


def token(client, app, scope=None):
    form = {"grant_type": "client_credentials"} | ({"scope": scope} if scope else {})
    r = client.post(f"{V1}/oauth/token", data=form, auth=(app["client_id"], app["client_secret"]))
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def app_token(client):
    return token(client, register(client))


@pytest.fixture
def person(client):
    object.__setattr__(settings, "dev_idp", True)
    yield lambda tenant, who: bearer(dev_idp.mint(tenant, who))
    object.__setattr__(settings, "dev_idp", False)


# --- credentials -------------------------------------------------------------------------------------------------------

def test_only_an_access_administrator_registers_applications(client):
    r = client.post("/api/developer/clients", json={"name": "x", "roles": ["investigator"], "scopes": ["cases:read"]},
                    headers={"X-Tenant": "meridian", "X-Persona": "ivan"})
    assert r.status_code == 403
    app = register(client)
    assert app["client_id"].startswith("cfc_") and app["client_secret"].startswith("cfs_")
    shown = client.get(f"/api/developer/clients/{app['client_id']}", headers=admin()).json()
    assert app["client_secret"] not in json.dumps(shown) and shown["secrets"][0]["hint"] == app["client_secret"][-4:]


@pytest.mark.parametrize("body,why", [
    ({"roles": ["admin"], "scopes": ["cases:read"]}, "manage_access"),
    ({"roles": ["mlro"], "scopes": ["cases:read"]}, "certify_access"),
    ({"roles": ["investigator"], "scopes": ["cases:write"]}, "person's own token"),
    ({"roles": ["investigator"], "scopes": ["filings:write"]}, "person's own token"),
    ({"roles": ["investigator"], "scopes": ["data:write"]}, "load_data"),
    ({"roles": ["investigator"], "scopes": ["nonsense"]}, "Unknown scope"),
    ({"roles": ["nobody"], "scopes": ["cases:read"]}, "Unknown role"),
    ({"roles": ["investigator"], "scopes": ["cases:read"], "allowed_ips": ["not-an-ip"]}, "CIDR"),
])
def test_registration_refuses_what_an_application_must_not_hold(client, body, why):
    r = client.post("/api/developer/clients", json={"name": "Bad app"} | body, headers=admin())
    assert r.status_code == 422 and why in r.json()["detail"], r.text


def test_token_exchange(client):
    app = register(client)
    basic = token(client, app)
    post = client.post(f"{V1}/oauth/token", data={"grant_type": "client_credentials", "client_id": app["client_id"],
                                                  "client_secret": app["client_secret"], "scope": "cases:read"}).json()
    assert basic.startswith("cfat_") and post["scope"] == "cases:read" and post["token_type"] == "Bearer" and post["expires_in"] == 900
    wrong = client.post(f"{V1}/oauth/token", data={"grant_type": "client_credentials"}, auth=(app["client_id"], "cfs_wrong"))
    unknown = client.post(f"{V1}/oauth/token", data={"grant_type": "client_credentials"}, auth=("cfc_nobody", app["client_secret"]))
    assert wrong.status_code == unknown.status_code == 401 and wrong.json()["error"] == unknown.json()["error"] == "invalid_client"
    assert wrong.json()["detail"] == unknown.json()["detail"]  # nothing says which part was wrong
    more = client.post(f"{V1}/oauth/token", data={"grant_type": "client_credentials", "scope": "filings:read"}, auth=(app["client_id"], app["client_secret"]))
    assert more.status_code == 400 and more.json()["error"] == "invalid_scope"
    grant = client.post(f"{V1}/oauth/token", data={"grant_type": "password"}, auth=(app["client_id"], app["client_secret"]))
    assert grant.json()["error"] == "unsupported_grant_type"
    narrow = client.get(f"{V1}/customers", headers=bearer(post["access_token"]))
    assert narrow.status_code == 403 and narrow.json()["code"] == "insufficient_scope"


def test_no_token_no_answer(client):
    for h in ({}, {"Authorization": "Bearer cfat_forged"}, {"Authorization": "Bearer not.a.jwt"}, {"Authorization": "Basic abc"},
              {"X-Tenant": "meridian", "X-Persona": "omar"}):
        r = client.get(f"{V1}/cases", headers=h)
        assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json"), h
        assert r.json()["request_id"] and "WWW-Authenticate" in r.headers


def test_revoked_rotated_and_suspended_credentials_stop_working(client):
    app = register(client)
    t = token(client, app)
    assert client.get(f"{V1}/me", headers=bearer(t)).status_code == 200
    client.post(f"{V1}/oauth/revoke", data={"token": t}, auth=(app["client_id"], app["client_secret"]))
    assert client.get(f"{V1}/me", headers=bearer(t)).status_code == 401
    new = client.post(f"/api/developer/clients/{app['client_id']}/secret", json={"overlap_hours": 0}, headers=admin()).json()["client_secret"]
    assert client.post(f"{V1}/oauth/token", data={"grant_type": "client_credentials"}, auth=(app["client_id"], app["client_secret"])).status_code == 401
    t2 = token(client, app | {"client_secret": new})
    client.post(f"/api/developer/clients/{app['client_id']}/status", json={"status": "suspended", "reason": "test"}, headers=admin())
    assert client.get(f"{V1}/me", headers=bearer(t2)).status_code == 401
    client.post(f"/api/developer/clients/{app['client_id']}/status", json={"status": "revoked"}, headers=admin())
    assert client.post(f"/api/developer/clients/{app['client_id']}/status", json={"status": "active"}, headers=admin()).status_code == 422


def test_ip_allowlist(client):
    app = register(client, allowed_ips=["10.0.0.0/8"])
    r = client.post(f"{V1}/oauth/token", data={"grant_type": "client_credentials"}, auth=(app["client_id"], app["client_secret"]))
    assert r.status_code == 403


# --- isolation and visibility ---------------------------------------------------------------------------------------------

def test_the_client_comes_from_the_token_not_the_headers(client, app_token):
    me = client.get(f"{V1}/me", headers=bearer(app_token) | {"X-Tenant": "kestrel", "X-Persona": "joy"}).json()
    assert me["tenant"]["id"] == "meridian" and me["kind"] == "app" and me["roles"] == ["investigator"]
    kestrel = client.get("/api/cases?status=all", headers={"X-Tenant": "kestrel", "X-Persona": "joy"}).json()
    for c in kestrel[:5]:
        assert client.get(f"{V1}/cases/{c['id']}", headers=bearer(app_token)).status_code == 404
    ours = {c["id"] for c in client.get(f"{V1}/cases?status=all&limit=200", headers=bearer(app_token)).json()["data"]}
    assert ours and not ours & {c["id"] for c in kestrel}


def test_an_application_sees_what_its_roles_see_and_never_restricted_cases(client):
    auditor_app = token(client, register(client, roles=["auditor"], scopes=["cases:read"]))
    audra = {"X-Tenant": "meridian", "X-Persona": "audra"}
    everything = client.get("/api/cases?status=all", headers=audra).json()
    restricted = {c["id"] for c in everything if c["restricted"]}
    assert restricted, "the demo has restricted cases"
    seen = set()
    cursor = None
    while True:
        page = client.get(f"{V1}/cases?status=all&limit=37" + (f"&cursor={cursor}" if cursor else ""), headers=bearer(auditor_app)).json()
        ids = [c["id"] for c in page["data"]]
        assert not set(ids) & seen  # pages never repeat
        seen |= set(ids)
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == {c["id"] for c in everything} - restricted
    for cid in restricted:
        assert client.get(f"{V1}/cases/{cid}", headers=bearer(auditor_app)).status_code == 404
    kyc_app = token(client, register(client, roles=["kyc_analyst"], scopes=["cases:read"]))
    kinds = {c["case_type"] for c in client.get(f"{V1}/cases?status=all&limit=200", headers=bearer(kyc_app)).json()["data"]}
    assert kinds <= {"kyc_onboarding", "kyc_refresh"}


def test_people_decide_and_applications_cannot(client, app_token, person):
    case = next(c for c in client.get(f"{V1}/cases?case_type=tm_alert_investigation&limit=100", headers=bearer(app_token)).json()["data"]
                if not c["restricted"])
    detail = client.get(f"{V1}/cases/{case['id']}", headers=bearer(app_token))
    r = client.post(f"{V1}/cases/{case['id']}/transitions", json={"to": "closed", "note": "x"}, headers=bearer(app_token))
    assert r.status_code == 403 and r.json()["code"] == "insufficient_scope"
    ivan = person("meridian", "ivan")
    me = client.get(f"{V1}/me", headers=ivan).json()
    assert me["kind"] == "person" and me["caller"] == "ivan" and "cases:write" in me["scopes"]
    seen = client.get(f"{V1}/cases/{case['id']}", headers=ivan)
    assert seen.status_code == 200
    stale = client.put(f"{V1}/cases/{case['id']}/narrative", json={"text": "From the API"}, headers=ivan | {"If-Match": '"stale"'})
    assert stale.status_code == 412 and stale.json()["code"] == "stale_version"
    ok = client.put(f"{V1}/cases/{case['id']}/narrative", json={"text": "From the API"}, headers=ivan | {"If-Match": seen.headers["ETag"]})
    assert ok.status_code == 200, ok.text
    assert client.get(f"{V1}/cases/{case['id']}", headers=ivan).headers["ETag"] != seen.headers["ETag"]
    events = client.get(f"{V1}/cases/{case['id']}/events?limit=500", headers=bearer(app_token)).json()["data"]
    assert events[-1]["kind"] == "narrative.saved" and events[-1]["actor"] == "ivan"
    assert detail.json()["transitions"] is not None
    assert client.get(f"{V1}/me", headers=person("meridian", "ghost")).status_code == 403


# --- the resources ---------------------------------------------------------------------------------------------------------

def test_reading_across_the_platform(client, app_token):
    h = bearer(app_token)
    cust = client.get(f"{V1}/customers?limit=5", headers=h).json()
    pid = cust["data"][0]["id"]
    for url in (f"/customers/{pid}", f"/customers/{pid}/profile", f"/customers/{pid}/accounts", f"/customers/{pid}/kyc",
                f"/customers/{pid}/transactions?limit=5", f"/customers/{pid}/cases", f"/customers/{pid}/network", "/customers/search?q=an",
                "/transactions?limit=3", "/alerts?limit=3", "/me", "/scopes", "/event-types"):
        r = client.get(V1 + url, headers=h)
        assert r.status_code == 200, (url, r.text)
    txn = client.get(f"{V1}/transactions?limit=1", headers=h).json()["data"][0]
    assert client.get(f"{V1}/transactions/{txn['id']}", headers=h).json()["id"] == txn["id"]
    assert client.get(f"{V1}/transactions/{txn['id']}/lineage", headers=h).status_code == 200
    for url in ("/filings", "/case-types", "/screening/lists", "/data/sources", "/metrics/dashboard"):
        assert client.get(V1 + url, headers=h).status_code == 403, url  # scopes this token does not carry
    wide = token(client, register(client, roles=["head_fiu"] if False else ["team_lead"],
                                  scopes=["workflows:read", "screening:read", "metrics:read", "data:read", "ask", "filings:read"]))
    for url in ("/case-types", "/case-types/tm_alert_investigation", "/screening/lists", "/data/sources", "/data/runs", "/metrics/dashboard", "/filings"):
        r = client.get(V1 + url, headers=bearer(wide))
        assert r.status_code == 200, (url, r.text)
    hit = client.post(f"{V1}/screening/checks", json={"names": ["Ivan Petrovski", "Jane Ordinary"]}, headers=bearer(wide)).json()
    assert len(hit["results"]) == 2
    asked = client.post(f"{V1}/ask", json={"question": "How many open cases are there?"}, headers=bearer(wide))
    assert asked.status_code == 200, asked.text


def test_bad_input_is_a_problem_document(client, app_token):
    r = client.get(f"{V1}/cases?limit=0", headers=bearer(app_token))
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"
    r = client.get(f"{V1}/cases?cursor=garbage", headers=bearer(app_token))
    assert r.status_code == 400 and r.json()["code"] == "invalid_cursor"
    r = client.post(f"{V1}/alerts", json={"source": "x"}, headers=bearer(app_token))
    assert r.status_code == 422 and r.json()["errors"]


def test_alerts_from_an_engine_are_idempotent(client, app_token):
    h = bearer(app_token)
    alert = {"source": "partner-tm", "external_id": "API-1", "scenario": "rapid_movement", "party_id": "P10007", "score": 0.91, "explanation": "rule 7"}
    first = client.post(f"{V1}/alerts", json=alert, headers=h | {"Idempotency-Key": "key-api-0001"})
    again = client.post(f"{V1}/alerts", json=alert, headers=h | {"Idempotency-Key": "key-api-0001"})
    assert first.status_code == again.status_code == 201 and first.json() == again.json() and again.headers["idempotent-replayed"] == "true"
    other = client.post(f"{V1}/alerts", json=alert | {"score": 0.5}, headers=h | {"Idempotency-Key": "key-api-0001"})
    assert other.status_code == 422 and other.json()["code"] == "idempotency_key_reused"
    plain = client.post(f"{V1}/alerts", json=alert, headers=h).json()
    assert plain["duplicate"] and plain["alert_id"] == first.json()["alert_id"]
    batch = client.post(f"{V1}/alerts/batch", json={"alerts": [alert | {"external_id": f"API-B{i}"} for i in range(3)]}, headers=h)
    assert batch.status_code == 201 and len(batch.json()["accepted"]) == 3
    assert client.get(f"{V1}/alerts/{first.json()['alert_id']}", headers=h).json()["source"] == "partner-tm"


def test_rate_limits(client):
    app = register(client, rate_limit=3)
    t = token(client, app)
    codes = [client.get(f"{V1}/me", headers=bearer(t)) for _ in range(4)]
    assert [r.status_code for r in codes] == [200, 200, 200, 429]
    assert codes[-1].json()["code"] == "rate_limited" and int(codes[-1].headers["retry-after"]) >= 1
    assert codes[0].headers["ratelimit-limit"] == "3"


def test_security_headers_and_body_limit(client, app_token):
    r = client.get(f"{V1}/me", headers=bearer(app_token) | {"X-Request-Id": "trace-12345678"})
    for name in ("x-content-type-options", "x-frame-options", "strict-transport-security", "content-security-policy", "cache-control"):
        assert name in r.headers, name
    assert r.headers["x-request-id"] == "trace-12345678" and r.headers["cache-control"] == "no-store"
    big = client.post(f"{V1}/alerts", content=b"x" * (settings.api_max_body + 1), headers=bearer(app_token) | {"content-type": "application/json"})
    assert big.status_code == 413


def test_every_call_is_recorded(client):
    app = register(client)
    t = token(client, app)
    client.get(f"{V1}/cases?limit=1", headers=bearer(t))
    client.get(f"{V1}/cases/does-not-exist", headers=bearer(t))
    calls = client.get(f"/api/developer/clients/{app['client_id']}", headers=admin()).json()["calls"]
    assert {(c["route"], c["status"]) for c in calls} >= {("/cases", 200), ("/cases/{case_id}", 404)}
    assert all("does-not-exist" not in c["route"] for c in calls)


def test_the_openapi_document(client):
    doc = client.get(f"{V1}/openapi.json").json()
    assert doc["info"]["title"] == "CaseForge API" and set(doc["components"]["securitySchemes"]) == {"app", "person"}
    flows = doc["components"]["securitySchemes"]["app"]["flows"]["clientCredentials"]
    assert flows["tokenUrl"] == "/api/v1/oauth/token" and "cases:read" in flows["scopes"]
    ops = [(p, m, op) for p, item in doc["paths"].items() for m, op in item.items()]
    assert len(ops) >= 50
    for p, m, op in ops:
        assert "security" in op, (p, m)
        if not p.startswith("/oauth") and p not in ("/me", "/scopes", "/event-types"):
            assert op.get("x-scope"), (p, m)
    transition = doc["paths"]["/cases/{case_id}/transitions"]["post"]
    assert transition["x-people-only"] and transition["security"] == [{"person": []}]
    assert client.get(f"{V1}/docs").status_code == 200


# --- events and webhooks ----------------------------------------------------------------------------------------------------

def test_the_event_feed(client, app_token):
    h = bearer(app_token)
    first = client.get(f"{V1}/events?limit=500", headers=h).json()
    after = first["next_after"]
    while True:  # catch up to now
        page = client.get(f"{V1}/events?limit=500&after={after}", headers=h).json()
        if not page["data"]:
            break
        after = page["next_after"]
    client.post(f"{V1}/alerts", json={"source": "partner-tm", "external_id": "FEED-1", "scenario": "rapid_movement", "party_id": "P10008",
                                      "score": 0.9, "explanation": "x"}, headers=h)
    new = client.get(f"{V1}/events?after={after}&types=alert.*", headers=h).json()["data"]
    created = [e for e in new if e["type"] == "alert.created"]
    assert len(created) == 1 and created[0]["specversion"] == "1.0" and created[0]["tenantid"] == "meridian"
    assert {e["type"] for e in new} <= {"alert.created", "alert.attached"}
    audra = {"X-Tenant": "meridian", "X-Persona": "audra"}
    restricted = {c["id"] for c in client.get("/api/cases?status=all", headers=audra).json() if c["restricted"]}
    auditor = token(client, register(client, roles=["auditor"], scopes=["cases:read", "events:read"]))
    seen, after = set(), 0
    for _ in range(200):
        page = client.get(f"{V1}/events?limit=500&after={after}", headers=bearer(auditor)).json()
        seen |= {e["subject"] for e in page["data"]}
        if page["next_after"] == after:
            break
        after = page["next_after"]
    assert seen and not seen & restricted


@pytest.fixture
def receiver(monkeypatch):
    object.__setattr__(settings, "webhook_allow_private", True)
    got = []
    status = {"code": 200}

    def fake(url, body, headers):
        got.append((url, body, headers))
        return status["code"]

    old = webhooks.swap_transport(fake)
    yield got, status
    webhooks.swap_transport(old)
    object.__setattr__(settings, "webhook_allow_private", False)


def test_webhook_addresses_must_be_public(client, app_token):
    for url in ("http://example.com/hook", "https://localhost/hook", "https://127.0.0.1/hook", "https://169.254.169.254/latest",
                "https://10.1.2.3/x", "https://user:pw@example.com/x", "ftp://example.com", "https://metadata.google.internal/x"):
        r = client.post(f"{V1}/webhooks", json={"url": url}, headers=bearer(app_token))
        assert r.status_code == 422, url


def test_webhooks_are_signed_retried_and_scoped(client, app_token, receiver):
    got, status = receiver
    h = bearer(app_token)
    made = client.post(f"{V1}/webhooks", json={"url": "http://receiver.test/hook", "event_types": ["alert.*"]}, headers=h)
    assert made.status_code == 201, made.text
    ep, secret = made.json(), made.json()["secret"]
    assert secret.startswith("whsec_") and "secret" not in client.get(f"{V1}/webhooks/{ep['id']}", headers=h).json()
    client.post(f"{V1}/alerts", json={"source": "partner-tm", "external_id": "HOOK-1", "scenario": "rapid_movement", "party_id": "P10009",
                                      "score": 0.9, "explanation": "x"}, headers=h)
    webhooks.run_once()
    mine = [g for g in got if g[0] == "http://receiver.test/hook" and json.loads(g[1])["type"] == "alert.created"]
    assert len(mine) == 1 and all(json.loads(g[1])["type"].startswith("alert.") for g in got)
    _, body, hdrs = mine[0]
    assert signing.verify(secret, hdrs["webhook-id"], hdrs["webhook-timestamp"], hdrs["webhook-signature"], body)
    assert not signing.verify(secret, hdrs["webhook-id"], hdrs["webhook-timestamp"], hdrs["webhook-signature"], body + b" ")
    assert not signing.verify(secret, hdrs["webhook-id"], "1000", hdrs["webhook-signature"], body)  # stale
    # a failing receiver: the delivery waits and is retried, and can be sent again by hand
    status["code"] = 500
    client.post(f"{V1}/webhooks/{ep['id']}/test", headers=h)
    webhooks.run_once()
    pending = client.get(f"{V1}/webhooks/{ep['id']}/deliveries?status=pending", headers=h).json()["data"]
    assert pending and pending[0]["attempts"] == 1 and pending[0]["last_code"] == 500 and pending[0]["next_attempt"]
    status["code"] = 204
    client.post(f"{V1}/webhooks/{ep['id']}/deliveries/{pending[0]['id']}/retry", headers=h)
    webhooks.run_once()
    assert not client.get(f"{V1}/webhooks/{ep['id']}/deliveries?status=pending", headers=h).json()["data"]
    # rotation signs with both secrets for the overlap
    new = client.post(f"{V1}/webhooks/{ep['id']}/secret", json={"overlap_hours": 1}, headers=h).json()["secret"]
    client.post(f"{V1}/webhooks/{ep['id']}/test", headers=h)
    got.clear()
    webhooks.run_once()
    _, body, hdrs = got[-1]
    assert signing.verify(new, hdrs["webhook-id"], hdrs["webhook-timestamp"], hdrs["webhook-signature"], body)
    assert signing.verify(secret, hdrs["webhook-id"], hdrs["webhook-timestamp"], hdrs["webhook-signature"], body)
    other = token(client, register(client, scopes=["webhooks:manage"]))
    assert client.get(f"{V1}/webhooks/{ep['id']}", headers=bearer(other)).status_code == 404  # another app's endpoint does not exist for it
    assert client.delete(f"{V1}/webhooks/{ep['id']}", headers=h).status_code == 204


def test_a_webhook_stops_when_its_application_is_suspended(client, receiver):
    app = register(client)
    t = token(client, app)
    ep = client.post(f"{V1}/webhooks", json={"url": "http://receiver.test/suspended"}, headers=bearer(t)).json()
    client.post(f"/api/developer/clients/{app['client_id']}/status", json={"status": "suspended"}, headers=admin())
    webhooks.run_once()
    detail = client.get(f"/api/developer/clients/{app['client_id']}", headers=admin()).json()
    assert detail["webhooks"][0]["id"] == ep["id"] and detail["webhooks"][0]["status"] == "disabled"


def test_the_developer_overview(client):
    out = client.get("/api/developer", headers=admin()).json()
    assert out["api"]["openapi"] == "/api/v1/openapi.json" and out["clients"]
    assert "admin" not in {r["id"] for r in out["roles"]} and "investigator" in {r["id"] for r in out["roles"]}
    assert all("view_restricted" not in r["permissions"] for r in out["roles"])


@pytest.fixture(autouse=True)
def _fresh_limits():
    old = ratelimit.swap(ratelimit.MemoryRateLimiter())
    yield
    ratelimit.swap(old)


def test_basic_auth_is_decoded_safely(client):
    r = client.post(f"{V1}/oauth/token", data={"grant_type": "client_credentials"}, headers={"Authorization": "Basic " + base64.b64encode(b"\xff\xfe").decode()})
    assert r.status_code == 401


def test_the_python_sdk(client):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))
    from caseforge import ApiError, Client, verify_webhook

    app = register(client, scopes=["cases:read", "alerts:write", "events:read"])
    cf = Client("http://testserver", app["client_id"], app["client_secret"], http=client)
    ids = [c["id"] for c in cf.paginate("/cases", status="all", limit=10)]
    assert len(ids) == len(set(ids)) > 10
    a = {"source": "sdk", "external_id": "SDK-1", "scenario": "rapid_movement", "party_id": "P10011", "score": 0.7, "explanation": "x"}
    assert cf.post("/alerts", a, idempotency_key="sdk-key-0001") == cf.post("/alerts", a, idempotency_key="sdk-key-0001")
    events, after = cf.events(0, ["alert.*"])
    assert after >= 0
    with pytest.raises(ApiError) as e:
        cf.get("/customers")
    assert e.value.status == 403 and e.value.code == "insufficient_scope" and e.value.request_id
    secret = "whsec_" + base64.b64encode(b"k" * 32).decode()
    hdrs = signing.headers([secret], "msg_1", b"{}")
    assert verify_webhook(secret, hdrs, b"{}") and not verify_webhook(secret, hdrs, b"{} ")
