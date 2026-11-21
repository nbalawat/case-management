"""Single sign-on: tokens are checked properly, and the client and the person come from the token and nothing else."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.auth import dev_idp, identity
from app.auth.oidc import ALGORITHMS, AuthError, Provider, Verifier, trusted_url, unverified_issuer
from app.config import settings
from test_client_api import CLIENTS, EVERYONE, OP, PEOPLE, h

ISS, AUD = "https://login.example-bank.com/realms/fiu", "caseforge"


class Idp:
    """A stand-in identity provider: holds a key, publishes it, signs what it is asked to."""

    def __init__(self, issuer=ISS, kid="k1", key=None):
        self.issuer, self.kid, self.key = issuer, kid, key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.fetched = []

    def jwk(self):
        algo = jwt.algorithms.ECAlgorithm if isinstance(self.key, ec.EllipticCurvePrivateKey) else jwt.algorithms.RSAAlgorithm
        return algo.to_jwk(self.key.public_key(), as_dict=True) | {"kid": self.kid, "use": "sig"}

    def fetch(self, url):
        self.fetched.append(url)
        if url == self.issuer + "/.well-known/openid-configuration":
            return {"issuer": self.issuer, "jwks_uri": self.issuer + "/certs"}
        if url == self.issuer + "/certs":
            return {"keys": [self.jwk()]}
        raise OSError("unreachable")

    def sign(self, alg="RS256", kid=None, key=None, **claims):
        now = int(time.time())
        body = {"iss": self.issuer, "aud": AUD, "sub": "u-1", "preferred_username": "ivan", "iat": now, "exp": now + 300} | claims
        return jwt.encode({k: v for k, v in body.items() if v is not None}, key or self.key, algorithm=alg, headers={"kid": kid or self.kid})


@pytest.fixture
def idp():
    return Idp()


P = Provider(ISS, AUD)


def test_a_properly_signed_token_is_accepted_and_keys_are_fetched_once(idp):
    v = Verifier(idp.fetch)
    assert v.verify(idp.sign(), P)["preferred_username"] == "ivan"
    assert v.verify(idp.sign(preferred_username="lena"), P)["preferred_username"] == "lena"
    assert idp.fetched == [ISS + "/.well-known/openid-configuration", ISS + "/certs"]  # discovery, then keys, then the cache
    assert v.verify(idp.sign(aud=["other", AUD]), P)  # one of several audiences
    assert Verifier(idp.fetch).verify(idp.sign(), Provider(ISS, AUD, jwks_uri=ISS + "/certs")) and idp.fetched[-1] == ISS + "/certs"
    curve = Idp(key=ec.generate_private_key(ec.SECP256R1()))
    assert Verifier(curve.fetch).verify(curve.sign(alg="ES256"), P)


def _forged_with_public_key(idp):
    """The classic confusion attack: sign with HMAC using the provider's public key as the secret."""
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")
    now = int(time.time())
    head = b64(json.dumps({"alg": "HS256", "kid": idp.kid, "typ": "JWT"}).encode())
    body = b64(json.dumps({"iss": ISS, "aud": AUD, "preferred_username": "maya", "iat": now, "exp": now + 300}).encode())
    import hmac
    from cryptography.hazmat.primitives import serialization
    pem = idp.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return b".".join([head, body, b64(hmac.new(pem, head + b"." + body, hashlib.sha256).digest())]).decode()


def _unsigned():
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    now = int(time.time())
    return f'{b64(json.dumps({"alg": "none", "kid": "k1"}).encode())}.{b64(json.dumps({"iss": ISS, "aud": AUD, "preferred_username": "maya", "iat": now, "exp": now + 300}).encode())}.'


BAD = {
    "expired": lambda i: i.sign(exp=int(time.time()) - 120),
    "not yet valid": lambda i: i.sign(nbf=int(time.time()) + 600),
    "wrong audience": lambda i: i.sign(aud="another-app"),
    "no audience": lambda i: i.sign(aud=None),
    "wrong issuer": lambda i: i.sign(iss="https://login.example-bank.com/realms/other"),
    "no expiry": lambda i: i.sign(exp=None),
    "no issue time": lambda i: i.sign(iat=None),
    "signed by someone else": lambda i: i.sign(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)),
    "unknown key id": lambda i: i.sign(kid="k9"),
    "shared-secret algorithm": lambda i: jwt.encode({"iss": ISS, "aud": AUD, "exp": int(time.time()) + 300, "iat": int(time.time())}, "s" * 40,
                                                    algorithm="HS256", headers={"kid": i.kid}),
    "public key used as a secret": _forged_with_public_key,
    "unsigned": lambda i: _unsigned(),
    "tampered": lambda i: (lambda t: t[:-6] + ("AAAAAA" if not t.endswith("AAAAAA") else "BBBBBB"))(i.sign()),
    "payload swapped": lambda i: ".".join([i.sign().split(".")[0], i.sign(preferred_username="maya", key=rsa.generate_private_key(public_exponent=65537, key_size=2048)).split(".")[1], i.sign().split(".")[2]]),
    "not a token": lambda i: "hello",
    "empty": lambda i: "",
}


@pytest.mark.parametrize("name", BAD)
def test_a_token_that_cannot_be_trusted_is_refused(idp, name):
    with pytest.raises(AuthError):
        Verifier(idp.fetch).verify(BAD[name](idp), P)


def test_only_asymmetric_signatures_are_ever_accepted():
    assert not [a for a in ALGORITHMS if a.startswith("HS") or a == "none"]


def test_key_rotation_is_followed_but_a_flood_of_bad_tokens_does_not_hammer_the_provider(idp):
    v = Verifier(idp.fetch, refetch_after=3600)
    v.verify(idp.sign(), P)
    calls = len(idp.fetched)
    for _ in range(5):
        with pytest.raises(AuthError):
            v.verify(idp.sign(kid="nope"), P)
    assert len(idp.fetched) == calls  # unknown key ids inside the quiet period cost the provider nothing
    old, idp.kid, idp.key = idp.key, "k2", rsa.generate_private_key(public_exponent=65537, key_size=2048)
    patient = Verifier(idp.fetch, refetch_after=0)
    assert patient.verify(idp.sign(), P)
    with pytest.raises(AuthError):
        patient.verify(idp.sign(kid="k1", key=old), P)  # the retired key is no longer published, so no longer trusted


def test_a_provider_that_lies_or_is_down_fails_closed(idp):
    def lying(url):
        return {"issuer": "https://evil.example", "jwks_uri": ISS + "/certs"} if url.endswith("openid-configuration") else idp.fetch(url)

    def plain_http(url):
        return {"issuer": ISS, "jwks_uri": "http://login.example-bank.com/certs"} if url.endswith("openid-configuration") else idp.fetch(url)

    def down(url):
        raise OSError("connection refused")

    for fetch in (lying, plain_http, down, lambda url: {"unexpected": True}):
        with pytest.raises(AuthError):
            Verifier(fetch).verify(idp.sign(), P)
    assert trusted_url("https://a.example/x") and trusted_url("http://localhost:8080/realms/x") and trusted_url("http://127.0.0.1:1/")
    assert not any(trusted_url(u) for u in ("http://a.example/", "ftp://a.example/", "file:///etc/passwd", "", "https://", "localhost"))
    assert unverified_issuer(idp.sign()) == ISS
    with pytest.raises(AuthError):
        unverified_issuer("x.y.z")


@pytest.mark.parametrize("raw,message", [
    ({"issuer": "http://login.example.com/", "audience": "a"}, "https"), ({"issuer": "", "audience": "a"}, "https"),
    ({"issuer": ISS}, "audience is required"), ({"issuer": ISS, "audience": "a", "person_claim": "name"}, "person_claim"),
    ({"issuer": ISS, "audience": "a", "jwks_uri": "http://x.example/k"}, "jwks_uri"), ({"issuer": ISS, "audience": "a", "secret": "x"}, "takes"),
    ("nope", "takes")])
def test_a_bad_provider_registration_is_refused(raw, message):
    with pytest.raises(identity.IdentityError, match=message):
        identity.check_provider(raw)


def test_a_good_registration_is_normalised():
    assert identity.check_provider({"issuer": ISS + " ", "client_id": "web"}) == {
        "issuer": ISS, "audience": "web", "client_id": "web", "person_claim": "preferred_username", "jwks_uri": None}


# --- the whole application with sign-in on ----------------------------------------------------------------------------
@pytest.fixture
def sso(client):
    """Sign-in switched on, with the development provider standing in for each client's own."""
    saved = settings.auth_mode, settings.dev_idp, identity.verifier
    object.__setattr__(settings, "auth_mode", "oidc")
    object.__setattr__(settings, "dev_idp", True)
    yield client
    object.__setattr__(settings, "auth_mode", saved[0])
    object.__setattr__(settings, "dev_idp", saved[1])
    identity.verifier = saved[2]
    with_op = client.put("/api/platform/tenants/meridian/identity", headers=OP, json={"provider": None})
    assert with_op.status_code == 200


def bearer(realm, user, **claims):
    return {"Authorization": f"Bearer {dev_idp.mint(realm, user, **claims)}"}


def _routes(app):
    out = []
    for path, methods in app.openapi()["paths"].items():
        if path.startswith("/api/") and not path.startswith("/api/auth/"):
            for method in methods:
                out.append((method.upper(), path.replace("{case_id}", "x").replace("{tenant_id}", "meridian").replace("{", "").replace("}", "")))
    return out


def test_with_sign_in_on_nothing_is_answered_without_a_token(sso):
    routes = _routes(sso.app)
    assert len(routes) > 60
    for method, path in routes:
        for headers in ({}, h("meridian", "maya"), OP, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic bWF5YTp4"},
                        {"Authorization": dev_idp.mint("meridian", "maya")}):  # the scheme is required
            r = sso.request(method, path, headers=headers, json={})
            assert r.status_code == 401, f"{method} {path} answered {r.status_code} to {headers}"
            assert r.headers["www-authenticate"] == "Bearer"
    assert sso.get("/api/auth/config").json() == {"mode": "oidc", "dev_idp": True}  # the sign-in page itself needs this


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_the_token_decides_the_client_and_the_person_and_headers_change_nothing(sso, tenant, who):
    me = sso.get("/api/me", headers=bearer(tenant, who)).json()
    assert (me["id"], me["tenant"]["id"]) == (who, tenant)
    other = next(t for t in CLIENTS if t != tenant)
    someone = PEOPLE[other]["people"][0]["id"]
    spoofed = sso.get("/api/me", headers=bearer(tenant, who) | {"X-Tenant": other, "X-Persona": someone, "X-Operator": "olivia"}).json()
    assert (spoofed["id"], spoofed["tenant"]["id"]) == (who, tenant)
    mine = {c["id"] for c in sso.get("/api/cases?status=all", headers=bearer(tenant, who)).json()}
    object.__setattr__(settings, "auth_mode", "demo")
    try:
        assert mine == {c["id"] for c in sso.get("/api/cases?status=all", headers=h(tenant, who)).json()}  # the same view as before sign-in
    finally:
        object.__setattr__(settings, "auth_mode", "oidc")
    assert sso.get("/api/platform/overview", headers=bearer(tenant, who) | OP).status_code == 403  # a client's user is never platform staff
    assert sso.get("/api/tenants", headers=bearer(tenant, who)).status_code == 404  # and there is no client list to browse


def test_signed_in_but_unknown_people_get_nothing(sso):
    r = sso.get("/api/cases", headers=bearer("meridian", "mallory"))
    assert r.status_code == 403 and "has no role at Meridian Retail Bank" in r.json()["detail"]  # no default person to fall back on
    assert sso.get("/api/meta", headers=bearer("meridian", "mallory")).status_code == 200  # which client they are at is all they learn
    assert sso.get("/api/auth/whoami", headers=bearer("meridian", "mallory")).json()["known"] is False
    assert sso.get("/api/cases", headers=bearer("meridian", "joy")).status_code == 403  # a real person, at another client
    assert sso.get("/api/cases", headers={"Authorization": f"Bearer {dev_idp.mint('meridian', 'maya', ttl=-120)}"}).status_code == 401
    assert sso.get("/api/cases", headers={"Authorization": f"Bearer {dev_idp.mint('nowhere', 'maya')}"}).status_code == 401
    no_name = dev_idp.mint("meridian", "maya", preferred_username=None)
    assert sso.get("/api/cases", headers={"Authorization": f"Bearer {no_name}"}).status_code == 401


def test_platform_staff_sign_in_through_their_own_provider_and_see_no_client_data(sso):
    olivia = bearer("platform", "olivia")
    assert sso.get("/api/platform/overview", headers=olivia).status_code == 200
    assert sso.get("/api/auth/whoami", headers=olivia).json()["operator"]["name"] == "Olivia Grant"
    for path in ("/api/cases", "/api/me", "/api/dashboard", "/api/work"):
        r = sso.get(path, headers=olivia | {"X-Tenant": "meridian", "X-Persona": "maya"})
        assert r.status_code == 403 and "no access to client data" in r.text
    assert sso.get("/api/platform/overview", headers=bearer("platform", "mallory")).status_code == 403
    assert sso.get("/api/platform/overview", headers=bearer("meridian", "olivia")).status_code == 403  # the right name from the wrong provider
    assert sso.post("/api/platform/tenants/alder/status", headers=bearer("alder", "otto"), json={"status": "suspended"}).status_code == 403


def test_a_suspended_client_cannot_sign_in(sso):
    olivia = bearer("platform", "olivia")
    assert sso.post("/api/platform/tenants/alder/status", headers=olivia, json={"status": "suspended"}).status_code == 200
    try:
        assert sso.get("/api/me", headers=bearer("alder", "vera")).status_code == 403
    finally:
        sso.post("/api/platform/tenants/alder/status", headers=olivia, json={"status": "live"})
    assert sso.get("/api/me", headers=bearer("alder", "vera")).status_code == 200


def test_the_operator_registers_each_clients_own_provider(sso):
    bank = Idp()
    identity.verifier = Verifier(bank.fetch)
    url, olivia = "/api/platform/tenants/meridian/identity", bearer("platform", "olivia")
    good = {"provider": {"issuer": ISS, "audience": AUD, "client_id": "caseforge-web", "person_claim": "email"}}
    assert sso.put(url, headers=bearer("meridian", "omar"), json=good).status_code == 403  # a client's admin cannot repoint sign-in
    assert sso.put(url, headers=olivia, json={"provider": {"issuer": "http://plain.example/", "audience": "a"}}).status_code == 422
    assert sso.put("/api/platform/tenants/nowhere/identity", headers=olivia, json=good).status_code == 422
    out = sso.put(url, headers=olivia, json=good)
    assert out.status_code == 200 and out.json()["identity"]["issuer"] == ISS
    taken = sso.put("/api/platform/tenants/kestrel/identity", headers=olivia, json=good)
    assert taken.status_code == 422 and "already signs in through that issuer" in taken.text  # one provider cannot speak for two clients

    token = bank.sign(email="maya", preferred_username="someone-else")
    me = sso.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert (me["id"], me["tenant"]["id"]) == ("maya", "meridian")  # the configured claim names the person
    assert sso.get("/api/me", headers={"Authorization": f"Bearer {bank.sign(email=None)}"}).status_code == 401
    assert sso.get("/api/me", headers=bearer("meridian", "maya")).status_code == 401  # the development provider no longer speaks for this client
    assert sso.get("/api/me", headers=bearer("kestrel", "joy")).status_code == 200  # and the others are untouched
    forged = Idp().sign(email="maya")  # same issuer name, somebody else's key
    assert sso.get("/api/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    where = sso.get("/api/auth/provider/meridian").json()
    assert where == {"organisation": "meridian", "name": "Meridian Retail Bank", "issuer": ISS, "client_id": "caseforge-web",
                     "discovery": ISS + "/.well-known/openid-configuration"}
    assert sso.get("/api/auth/provider/nowhere").status_code == 404
    listed = next(t for t in sso.get("/api/platform/overview", headers=olivia).json()["tenants"] if t["id"] == "meridian")
    assert listed["identity"]["issuer"] == ISS
    assert sso.put(url, headers=olivia, json={"provider": None}).json()["identity"] is None
    assert sso.get("/api/me", headers=bearer("meridian", "maya")).status_code == 200


def _pkce():
    verifier = "v" * 50
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_the_browser_sign_in_flow_end_to_end(sso):
    """Authorization code with PKCE against the development provider, exactly as the web app does it."""
    where = sso.get("/api/auth/provider/kestrel").json()
    found = sso.get(urlparse(where["discovery"]).path).json()
    assert found["issuer"] == where["issuer"] and found["code_challenge_methods_supported"] == ["S256"]
    verifier, challenge = _pkce()
    q = {"response_type": "code", "client_id": where["client_id"], "redirect_uri": "http://localhost:5173/", "state": "st-1", "nonce": "n-1",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    auth = urlparse(found["authorization_endpoint"]).path
    page = sso.get(auth, params=q)
    assert page.status_code == 200 and "Joy Tanaka" in page.text and "Maya Chen" not in page.text  # only this client's people
    back = sso.get(auth.replace("authorize", "login"), params=q | {"user": "joy"}, follow_redirects=False)
    assert back.status_code == 302
    landed = parse_qs(urlparse(back.headers["location"]).query)
    assert back.headers["location"].startswith("http://localhost:5173/?") and landed["state"] == ["st-1"]
    form = {"grant_type": "authorization_code", "code": landed["code"][0], "redirect_uri": q["redirect_uri"], "client_id": where["client_id"]}
    tok = urlparse(found["token_endpoint"]).path
    assert sso.post(tok, data=form | {"code_verifier": "w" * 50}).status_code == 400  # wrong verifier: the code is burnt
    assert sso.post(tok, data=form | {"code_verifier": verifier}).status_code == 400  # and cannot be tried again

    back = sso.get(auth.replace("authorize", "login"), params=q | {"user": "joy"}, follow_redirects=False)
    form["code"] = parse_qs(urlparse(back.headers["location"]).query)["code"][0]
    assert sso.post(tok.replace("kestrel", "alder"), data=form | {"code_verifier": verifier}).status_code == 400  # a code is for one realm
    back = sso.get(auth.replace("authorize", "login"), params=q | {"user": "joy"}, follow_redirects=False)
    form["code"] = parse_qs(urlparse(back.headers["location"]).query)["code"][0]
    out = sso.post(tok, data=form | {"code_verifier": verifier})
    assert out.status_code == 200 and out.json()["token_type"] == "Bearer"
    claims = jwt.decode(out.json()["id_token"], options={"verify_signature": False})
    assert claims["nonce"] == "n-1" and claims["preferred_username"] == "joy" and claims["iss"] == where["issuer"]
    me = sso.get("/api/me", headers={"Authorization": f"Bearer {out.json()['access_token']}"}).json()
    assert (me["id"], me["tenant"]["id"]) == ("joy", "kestrel")
    assert sso.post(tok, data=form | {"code_verifier": verifier}).status_code == 400  # replay

    for bad in ({"redirect_uri": "https://evil.example/cb"}, {"code_challenge_method": "plain"}, {"code_challenge": ""}, {"client_id": "other"},
                {"response_type": "token"}):
        assert sso.get(auth, params=q | bad).status_code == 400, bad
    assert sso.get(auth.replace("authorize", "login"), params=q | {"user": "maya"}, follow_redirects=False).status_code == 403


def test_the_development_provider_does_not_exist_unless_switched_on(client):
    assert settings.dev_idp is False and settings.auth_mode == "demo"
    for path in ("/dev-idp/meridian/.well-known/openid-configuration", "/dev-idp/meridian/jwks", "/dev-idp/meridian/authorize",
                 "/dev-idp/meridian/login?user=maya"):
        assert client.get(path, follow_redirects=False).status_code == 404
    assert client.post("/dev-idp/meridian/token", data={}).status_code in (400, 404)
    assert client.get("/api/auth/provider/meridian").status_code == 404 and client.get("/api/auth/config").json() == {"mode": "demo", "dev_idp": False}
    assert client.get("/api/me", headers=bearer("meridian", "maya") | h("meridian", "ivan")).json()["id"] == "ivan"  # demo mode ignores tokens
    object.__setattr__(settings, "auth_mode", "oidc")
    try:  # sign-in on, development provider off, nothing registered: nobody can get in, and nothing falls back to headers
        assert client.get("/api/me", headers=bearer("meridian", "maya") | h("meridian", "maya")).status_code == 401
        assert client.get("/api/platform/overview", headers=bearer("platform", "olivia") | OP).status_code == 401
    finally:
        object.__setattr__(settings, "auth_mode", "demo")
