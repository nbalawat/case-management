"""Browser sign-in held by the server: the code flow done here, an httpOnly cookie, refresh, idle and absolute
timeouts, cross-site protection and sign-out at the provider. The development provider plays the identity provider,
reached through the application's own test client exactly as a real one is reached over HTTP."""
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from app.auth import dev_idp, sessions
from app.auth.models import AuthSession
from app.auth.oidc import AuthError, backchannel
from app.config import settings
from app.db import session_scope
from app.domain.models import now

ORIGIN = {"Origin": settings.public_url.rstrip("/")}


class Through:
    """The identity provider, reached through the test client instead of the network."""

    def __init__(self, client):
        self.client, self.posts = client, []

    def _path(self, url):
        u = urlparse(url)
        return u.path + (f"?{u.query}" if u.query else "")

    def get(self, url):
        r = self.client.get(self._path(url))
        if r.status_code != 200:
            raise AuthError("Identity provider could not be reached")
        return r.json()

    def post(self, url, form):
        self.posts.append((urlparse(url).path, form.get("grant_type") or "revoke"))
        r = self.client.post(self._path(url), content=urlencode(form), headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            raise AuthError(f"The identity provider refused: {r.json().get('detail')}")
        return r.json()


@pytest.fixture
def web(client):
    saved = settings.auth_mode, settings.dev_idp, sessions.http
    object.__setattr__(settings, "auth_mode", "oidc")
    object.__setattr__(settings, "dev_idp", True)
    sessions.http = Through(client)
    client.cookies.clear()
    yield client
    client.cookies.clear()
    sessions.http = saved[2]
    object.__setattr__(settings, "auth_mode", saved[0])
    object.__setattr__(settings, "dev_idp", saved[1])


def sign_in(web, org="meridian", user="maya"):
    """What a browser does: start, follow the provider's page, come back with the code."""
    start = web.get("/api/auth/login", params={"organisation": org}, follow_redirects=False)
    assert start.status_code == 303, start.text
    assert "httponly" in start.headers["set-cookie"].lower() and "samesite=lax" in start.headers["set-cookie"].lower()
    q = parse_qs(urlparse(start.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == [sessions.callback_url()]
    login = web.get(urlparse(start.headers["location"]).path.replace("/authorize", "/login"),
                    params={k: v[0] for k, v in q.items()} | {"user": user}, follow_redirects=False)
    back = urlparse(login.headers["location"])
    done = web.get(back.path, params={k: v[0] for k, v in parse_qs(back.query).items()}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == sessions.home_url(), done.headers.get("location")
    LAST["id"] = sessions.digest(web.cookies[sessions.COOKIE])
    return done


LAST = {}


def row():
    """The session the test browser signed in to last."""
    with session_scope(platform=True) as s:
        return s.get(AuthSession, LAST["id"])


def test_signing_in_leaves_only_an_httponly_cookie_in_the_browser(web):
    done = sign_in(web)
    cookie = next(c for c in done.headers.get_list("set-cookie") if c.startswith(f"{sessions.COOKIE}="))
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    assert "token" not in done.text and not done.headers["location"].count("code=")
    assert web.get("/api/auth/whoami").json()["subject"] == "maya"
    assert web.get("/api/me").json()["id"] == "maya"
    r = row()
    assert r.id == sessions.digest(web.cookies[sessions.COOKIE]) and r.id != web.cookies[sessions.COOKIE]  # only a hash is kept
    assert "eyJ" not in r.access_sealed and r.refresh_sealed and r.realm == "meridian"  # tokens sealed at rest


def test_platform_staff_sign_in_the_same_way(web):
    sign_in(web, "platform", "olivia")
    assert web.get("/api/platform/overview").status_code == 200
    assert web.get("/api/me", headers={"X-Tenant": "meridian"}).status_code == 403


def test_a_tampered_or_replayed_callback_is_refused(web):
    start = web.get("/api/auth/login", params={"organisation": "meridian"}, follow_redirects=False)
    q = parse_qs(urlparse(start.headers["location"]).query)
    login = web.get("/dev-idp/meridian/login", params={k: v[0] for k, v in q.items()} | {"user": "maya"}, follow_redirects=False)
    back = parse_qs(urlparse(login.headers["location"]).query)
    wrong = web.get("/api/auth/callback", params={"code": back["code"][0], "state": "forged"}, follow_redirects=False)
    assert wrong.status_code == 303 and "signin_error" in wrong.headers["location"]
    assert sessions.COOKIE not in web.cookies
    web.cookies.clear()  # no pending cookie: another browser cannot finish this sign-in
    stolen = web.get("/api/auth/callback", params={"code": back["code"][0], "state": back["state"][0]}, follow_redirects=False)
    assert "signin_error" in stolen.headers["location"] and sessions.COOKIE not in web.cookies
    cancelled = web.get("/api/auth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "Sign-in+was+cancelled" in cancelled.headers["location"]


def test_an_unknown_organisation_has_nowhere_to_sign_in(web):
    assert web.get("/api/auth/login", params={"organisation": "nowhere"}, follow_redirects=False).status_code == 404


def test_the_access_token_is_refreshed_before_it_expires(web):
    sign_in(web)
    first = row()
    with session_scope(platform=True) as s:
        s.get(AuthSession, first.id).access_expires = now() + timedelta(seconds=5)
    calls = len(sessions.http.posts)
    assert web.get("/api/me").status_code == 200
    assert sessions.http.posts[calls:] == [("/dev-idp/meridian/token", "refresh_token")]
    again = row()
    assert again.refreshed == 1 and again.access_expires > now() + timedelta(minutes=30)
    assert again.refresh_sealed != first.refresh_sealed  # rotated
    assert web.get("/api/me").status_code == 200 and len(sessions.http.posts) == calls + 1  # not on every request


def test_a_refresh_the_provider_refuses_ends_the_session(web, client):
    sign_in(web)
    with session_scope(platform=True) as s:
        s.get(AuthSession, row().id).access_expires = now()
    dev_idp._refresh.clear()  # e.g. the user was disabled at the provider, or signed out everywhere
    r = web.get("/api/me")
    assert r.status_code == 401 and "ended" in r.json()["detail"]
    assert row().ended is not None and row().ended_why == "refresh refused" and row().access_sealed == ""
    assert web.get("/api/me").status_code == 401


@pytest.mark.parametrize("field,delta,why", [("last_seen", timedelta(minutes=31), "idle"), ("created", timedelta(hours=13), "too old")])
def test_idle_and_old_sessions_end(web, field, delta, why):
    sign_in(web)
    with session_scope(platform=True) as s:
        setattr(s.get(AuthSession, row().id), field, now() - delta)
    assert web.get("/api/me").status_code == 401
    assert row().ended_why == why


def test_a_change_sent_from_another_site_is_refused(web):
    sign_in(web, "meridian", "omar")
    body = {"label": "x"}
    assert web.post("/api/directory/tokens", json=body).status_code == 403  # no Origin: not shown to be our page
    assert web.post("/api/directory/tokens", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert web.post("/api/directory/tokens", json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert web.post("/api/directory/tokens", json=body, headers=ORIGIN).status_code == 201
    assert web.post("/api/directory/tokens", json=body, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 201
    # A bearer token is not sent by a browser on its own, so it needs no such check.
    web.cookies.clear()
    bearer = {"Authorization": f"Bearer {dev_idp.mint('meridian', 'omar')}"}
    assert web.post("/api/directory/tokens", json=body, headers=bearer).status_code == 201


def test_signing_out_ends_the_session_here_and_at_the_provider(web):
    sign_in(web)
    held = row()
    out = web.post("/api/auth/logout", headers=ORIGIN)
    url = urlparse(out.json()["redirect"])
    assert url.path == "/dev-idp/meridian/logout" and parse_qs(url.query)["post_logout_redirect_uri"] == [sessions.home_url()]
    assert "id_token_hint" in parse_qs(url.query)
    assert ("/dev-idp/meridian/revoke", "revoke") in sessions.http.posts
    assert sessions.COOKIE not in web.cookies and row().ended_why == "signed out"
    web.cookies.set(sessions.COOKIE, "replayed-after-sign-out")
    assert web.get("/api/me").status_code == 401
    with session_scope(platform=True) as s:
        assert s.get(AuthSession, held.id).refresh_sealed is None
    assert web.post("/api/auth/logout").json()["redirect"] == sessions.home_url()  # nothing to end: just home


def test_every_session_of_a_person_can_be_ended(web):
    sign_in(web)
    with session_scope(platform=True) as s:
        assert sessions.end_all(s, "meridian", "maya", "deactivated") >= 1
    assert web.get("/api/me").status_code == 401


def test_the_backchannel_only_reroutes_the_configured_addresses():
    saved = settings.oidc_backchannel
    object.__setattr__(settings, "oidc_backchannel", "http://localhost:8080=http://keycloak:8080")
    try:
        assert backchannel("http://localhost:8080/realms/meridian/protocol/openid-connect/token") == \
            "http://keycloak:8080/realms/meridian/protocol/openid-connect/token"
        assert backchannel("https://login.example.com/x") == "https://login.example.com/x"
    finally:
        object.__setattr__(settings, "oidc_backchannel", saved)
