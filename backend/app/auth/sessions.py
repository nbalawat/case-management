"""Browser sign-in held by this server (backend for frontend): the browser never sees a token.

    /api/auth/login     finds the organisation's identity provider, sends the browser there (code flow with PKCE)
    /api/auth/callback  exchanges the code here, checks the ID token and its nonce, and sets an httpOnly cookie
    every request       the cookie finds the session; its access token is checked exactly as a bearer token is,
                        and is refreshed with the refresh token shortly before it expires
    /api/auth/logout    ends the session here, revokes the refresh token, and signs out at the provider too

A session ends after SESSION_IDLE_MINUTES without use, SESSION_MAX_HOURS after sign-in, or when the provider
refuses to refresh it (the user was disabled there, or signed out elsewhere). Tokens are sealed at rest with a key
derived from API_SECRET_KEY, and the row is found by the SHA-256 of the cookie, never the cookie itself.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, replace
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..domain.models import now
from ..public import credentials
from ..tenants.registry import UnknownTenant, runtime
from . import identity, oidc
from .models import AuthSession
from .oidc import AuthError, Provider

COOKIE = "cf_session"
PENDING = "cf_signin"
PENDING_TTL = 600  # seconds a sign-in may take at the provider
REFRESH_AHEAD = 30  # refresh when the access token has less than this many seconds left
SEEN_EVERY = 60  # last_seen is written at most this often, not on every request
SCOPE = "openid profile email"


@dataclass
class Http:
    """How this server talks to identity providers. Tests put the application's own test client here."""
    get: oidc.Fetch = oidc.http_fetch
    post: object = oidc.http_post_form


http = Http()


# ---- sealing --------------------------------------------------------------------------------------------------------
def _box() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"caseforge-sessions:" + credentials._key()).digest()))


def seal(value: str) -> str:
    return _box().encrypt(value.encode()).decode()


def unseal(value: str, ttl: int | None = None) -> str:
    try:
        return _box().decrypt(value.encode(), ttl=ttl).decode()
    except InvalidToken:
        raise AuthError("Sign-in could not be completed. Please try again.") from None


def digest(cookie: str) -> str:
    return hashlib.sha256(cookie.encode()).hexdigest()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# ---- which provider -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Organisation:
    realm: str  # a client's id, or identity.PLATFORM
    name: str
    provider: Provider


def organisation(name: str) -> Organisation:
    """Where a named organisation signs in. Raises LookupError if it does not sign in here."""
    key = name.strip().lower()
    if key == identity.PLATFORM:
        p, title = identity.operator_provider(), "Platform staff"
    else:
        try:
            rt = runtime(key)
        except UnknownTenant:
            raise LookupError("No organisation with that name signs in here") from None
        p, title = identity.provider_of(rt), rt.name
    if p is None:
        raise LookupError("No organisation with that name signs in here")
    return Organisation(key, title, p)


def discover(p: Provider) -> dict:
    try:
        meta = http.get(p.issuer.rstrip("/") + "/.well-known/openid-configuration")
    except AuthError:
        raise
    except Exception as e:
        raise AuthError("Identity provider could not be reached") from e
    if meta.get("issuer") != p.issuer:
        raise AuthError("The identity provider did not identify itself correctly")
    for k in ("authorization_endpoint", "token_endpoint"):
        if not oidc.trusted_url(meta.get(k) or ""):
            raise AuthError("The identity provider's addresses are not trusted")
    return meta


def callback_url() -> str:
    return settings.public_url.rstrip("/") + "/api/auth/callback"


def home_url() -> str:
    return settings.public_url.rstrip("/") + "/"


# ---- signing in -----------------------------------------------------------------------------------------------------
def begin(name: str) -> tuple[str, str]:
    """Where to send the browser, and the sealed value of the short-lived cookie that remembers this attempt."""
    org = organisation(name)
    meta = discover(org.provider)
    verifier, state, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    q = {"response_type": "code", "client_id": org.provider.client_id, "redirect_uri": callback_url(), "scope": SCOPE,
         "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    pending = seal(json.dumps({"realm": org.realm, "verifier": verifier, "state": state, "nonce": nonce}))
    return f"{meta['authorization_endpoint']}?{urlencode(q)}", pending


def complete(s: Session, code: str, state: str, pending: str | None, user_agent: str = "") -> str:
    """Finish a sign-in the provider sent back. Returns the new session's cookie value."""
    if not pending:
        raise AuthError("Sign-in could not be completed. Please try again.")
    p = json.loads(unseal(pending, ttl=PENDING_TTL))
    if not state or not secrets.compare_digest(state, p["state"]):
        raise AuthError("Sign-in could not be completed. Please try again.")
    try:
        org = organisation(p["realm"])
    except LookupError:
        raise AuthError("That organisation no longer signs in here") from None
    meta = discover(org.provider)
    out = http.post(meta["token_endpoint"], {"grant_type": "authorization_code", "code": code, "redirect_uri": callback_url(),
                                             "client_id": org.provider.client_id, "code_verifier": p["verifier"]})
    access, id_token = out.get("access_token"), out.get("id_token")
    if not isinstance(access, str) or not isinstance(id_token, str):
        raise AuthError("The identity provider did not return a sign-in")
    # The ID token is for this application (its audience is our client id) and answers this attempt (its nonce).
    claims = identity.verifier.verify(id_token, replace(org.provider, audience=org.provider.client_id))
    if not secrets.compare_digest(str(claims.get("nonce") or ""), p["nonce"]):
        raise AuthError("Sign-in could not be completed. Please try again.")
    who = identity.authenticate(f"Bearer {access}")  # checked exactly as every later request will check it
    if (identity.PLATFORM if who.operator else who.tenant.id) != org.realm:
        raise AuthError("The identity provider signed someone in for a different organisation")
    cookie = secrets.token_urlsafe(32)
    s.add(AuthSession(id=digest(cookie), realm=org.realm, subject=who.subject, access_sealed=seal(access),
                      refresh_sealed=seal(out["refresh_token"]) if out.get("refresh_token") else None, id_token_sealed=seal(id_token),
                      access_expires=_expires(out), token_endpoint=meta["token_endpoint"],
                      end_session_endpoint=meta.get("end_session_endpoint"), revocation_endpoint=meta.get("revocation_endpoint"),
                      client_id=org.provider.client_id))
    s.flush()
    return cookie


def _expires(out: dict):
    try:
        seconds = max(0, int(out.get("expires_in") or 300))
    except (TypeError, ValueError):
        seconds = 300
    return now() + timedelta(seconds=seconds)


# ---- every request --------------------------------------------------------------------------------------------------
def access_token(s: Session, cookie: str) -> str:
    """The session's current access token, refreshed first if it is about to expire. Raises AuthError if it has ended."""
    row = s.scalars(select(AuthSession).where(AuthSession.id == digest(cookie)).with_for_update()).first()
    if row is None or row.ended is not None:
        raise AuthError("Sign in to continue")
    at = now()
    if row.last_seen < at - timedelta(minutes=settings.session_idle_minutes):
        _end(row, "idle")
        raise AuthError("You were signed out after a period without activity")
    if row.created < at - timedelta(hours=settings.session_max_hours):
        _end(row, "too old")
        raise AuthError("Your sign-in has expired. Please sign in again.")
    if row.access_expires <= at + timedelta(seconds=REFRESH_AHEAD):
        _refresh(row)
    if (at - row.last_seen).total_seconds() >= SEEN_EVERY:
        row.last_seen = at
    return unseal(row.access_sealed)


def _refresh(row: AuthSession) -> None:
    if not row.refresh_sealed:
        _end(row, "expired")
        raise AuthError("Your sign-in has expired. Please sign in again.")
    try:
        out = http.post(row.token_endpoint, {"grant_type": "refresh_token", "refresh_token": unseal(row.refresh_sealed),
                                             "client_id": row.client_id, "scope": SCOPE})
        if not isinstance(out.get("access_token"), str):
            raise AuthError("The identity provider did not return a sign-in")
    except AuthError:
        _end(row, "refresh refused")  # disabled at the provider, or signed out there
        raise AuthError("Your sign-in has ended. Please sign in again.") from None
    row.access_sealed, row.access_expires, row.refreshed = seal(out["access_token"]), _expires(out), row.refreshed + 1
    if out.get("refresh_token"):  # providers that rotate refresh tokens send a new one each time
        row.refresh_sealed = seal(out["refresh_token"])
    if out.get("id_token"):
        row.id_token_sealed = seal(out["id_token"])


def _end(row: AuthSession, why: str) -> None:
    row.ended, row.ended_why = now(), why
    row.access_sealed, row.refresh_sealed = "", None  # nothing left that could be used


# ---- signing out ----------------------------------------------------------------------------------------------------
def sign_out(s: Session, cookie: str | None) -> str:
    """End the session here and at the provider. Returns where to send the browser next."""
    row = s.get(AuthSession, digest(cookie)) if cookie else None
    if row is None or row.ended is not None:
        return home_url()
    if row.refresh_sealed and row.revocation_endpoint:
        try:  # RFC 7009. Best effort: the session ends here whatever the provider says.
            http.post(row.revocation_endpoint, {"token": unseal(row.refresh_sealed), "token_type_hint": "refresh_token", "client_id": row.client_id})
        except AuthError:
            pass
    hint = unseal(row.id_token_sealed) if row.id_token_sealed else None
    _end(row, "signed out")
    if not row.end_session_endpoint or not oidc.trusted_url(row.end_session_endpoint):
        return home_url()
    q = {"post_logout_redirect_uri": home_url(), "client_id": row.client_id} | ({"id_token_hint": hint} if hint else {})
    sep = "&" if urlparse(row.end_session_endpoint).query else "?"
    return f"{row.end_session_endpoint}{sep}{urlencode(q)}"


def end_all(s: Session, realm: str, subject: str, why: str) -> int:
    """End every live session of one person (deactivated at the provider, offboarded, or signed out everywhere)."""
    r = s.execute(update(AuthSession).where(AuthSession.realm == realm, AuthSession.subject == subject, AuthSession.ended.is_(None))
                  .values(ended=now(), ended_why=why, access_sealed="", refresh_sealed=None))
    return r.rowcount or 0


def cookie_secure() -> bool:
    return urlparse(settings.public_url).scheme == "https"

