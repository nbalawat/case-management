"""A small OpenID Connect provider for laptops and tests. Never enable it in a real deployment.

It exists so that sign-in can be shown and tested end to end without running Keycloak: discovery, published keys,
the authorization-code flow with PKCE, and signed tokens, one realm per client plus one for platform staff.
"Signing in" is choosing a name from the client's own people; there are no passwords. Enabled with DEV_IDP=1.
"""
from __future__ import annotations

import base64
import hashlib
import html
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import settings
from ..tenants.registry import UnknownTenant, runtime
from .oidc import Provider

CLIENT_ID = "caseforge-web"
CODE_TTL, TOKEN_TTL = 60, 3600
router = APIRouter(prefix="/dev-idp")
_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "dev-" + secrets.token_hex(4)
_codes: dict[str, dict] = {}


def issuer(realm: str) -> str:
    return f"{settings.public_url.rstrip('/')}/dev-idp/{realm}"


def jwks() -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_private.public_key(), as_dict=True)
    return {"keys": [jwk | {"kid": KID, "use": "sig", "alg": "RS256"}]}


def provider(realm: str) -> Provider:
    return Provider(issuer(realm), CLIENT_ID, "preferred_username", CLIENT_ID, jwks=tuple(jwks()["keys"]))


def mint(realm: str, user: str, *, ttl: int = TOKEN_TTL, **claims) -> str:
    at = int(time.time())
    body = {"iss": issuer(realm), "aud": CLIENT_ID, "sub": f"{realm}:{user}", "preferred_username": user, "iat": at, "exp": at + ttl} | claims
    return jwt.encode(body, _private, algorithm="RS256", headers={"kid": KID})


def _people(realm: str) -> list[tuple[str, str, str]]:
    if not settings.dev_idp:
        raise HTTPException(404, "Not found")
    if realm == "platform":
        from ..api.deps import OPERATORS

        return [(o["id"], o["name"], o["title"]) for o in OPERATORS.values()]
    try:
        return [(p.id, p.name, p.title) for p in runtime(realm).policy.people.values()]
    except UnknownTenant:
        raise HTTPException(404, "No such realm") from None


def _realm_name(realm: str) -> str:
    return "Platform staff" if realm == "platform" else runtime(realm).name


def _redirect_ok(uri: str) -> bool:
    u, home = urlparse(uri), urlparse(settings.public_url)
    return u.scheme in ("http", "https") and (u.hostname in ("localhost", "127.0.0.1") or (u.scheme, u.netloc) == (home.scheme, home.netloc))


@router.get("/{realm}/.well-known/openid-configuration")
def discovery(realm: str):
    _people(realm)
    base = issuer(realm)
    return {"issuer": base, "authorization_endpoint": f"{base}/authorize", "token_endpoint": f"{base}/token", "jwks_uri": f"{base}/jwks",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code"], "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["S256"]}


@router.get("/{realm}/jwks")
def get_jwks(realm: str):
    _people(realm)
    return jwks()


def _check_request(response_type: str, client_id: str, redirect_uri: str, code_challenge: str, code_challenge_method: str) -> None:
    if response_type != "code" or client_id != CLIENT_ID:
        raise HTTPException(400, "Unsupported sign-in request")
    if not _redirect_ok(redirect_uri):
        raise HTTPException(400, "Redirect address is not allowed")
    if code_challenge_method != "S256" or not code_challenge:
        raise HTTPException(400, "PKCE with S256 is required")


@router.get("/{realm}/authorize", response_class=HTMLResponse)
def authorize(realm: str, response_type: str = "", client_id: str = "", redirect_uri: str = "", state: str = "", nonce: str = "",
              code_challenge: str = "", code_challenge_method: str = ""):
    people = _people(realm)
    _check_request(response_type, client_id, redirect_uri, code_challenge, code_challenge_method)
    q = {"response_type": response_type, "client_id": client_id, "redirect_uri": redirect_uri, "state": state, "nonce": nonce,
         "code_challenge": code_challenge, "code_challenge_method": code_challenge_method}
    rows = "".join(f'<a href="login?{html.escape(urlencode(q | {"user": pid}))}"><b>{html.escape(name)}</b><span>{html.escape(title)}</span></a>'
                   for pid, name, title in people)
    return f"""<!doctype html><meta charset="utf-8"><title>Sign in</title>
<style>body{{font:15px/1.5 system-ui;background:#f4f5f7;color:#1c2330;display:grid;place-items:center;min-height:100vh;margin:0}}
main{{background:#fff;border:1px solid #dfe3ea;border-radius:12px;padding:28px 32px;width:420px}}h1{{font-size:20px;margin:0 0 4px}}
p{{color:#5b6677;margin:0 0 16px}}a{{display:flex;justify-content:space-between;padding:10px 12px;border:1px solid #e6e9ef;border-radius:8px;
margin:6px 0;color:inherit;text-decoration:none}}a:hover{{border-color:#2f5bea;background:#f5f8ff}}span{{color:#5b6677}}
small{{display:block;margin-top:16px;color:#8a94a6}}</style>
<main><h1>{html.escape(_realm_name(realm))}</h1><p>Choose who to sign in as.</p>{rows}
<small>Development identity provider. A real deployment sends you to your organisation's own sign-in page.</small></main>"""


@router.get("/{realm}/login")
def login(realm: str, user: str, response_type: str = "", client_id: str = "", redirect_uri: str = "", state: str = "", nonce: str = "",
          code_challenge: str = "", code_challenge_method: str = ""):
    if user not in {pid for pid, _, _ in _people(realm)}:
        raise HTTPException(403, "Not a user of this organisation")
    _check_request(response_type, client_id, redirect_uri, code_challenge, code_challenge_method)
    code = secrets.token_urlsafe(24)
    _codes[code] = {"realm": realm, "user": user, "redirect_uri": redirect_uri, "challenge": code_challenge, "nonce": nonce, "at": time.time()}
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}", status_code=302)


@router.post("/{realm}/token")
async def token(realm: str, request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    grant_type, code, redirect_uri = form.get("grant_type", ""), form.get("code", ""), form.get("redirect_uri", "")
    client_id, code_verifier = form.get("client_id", ""), form.get("code_verifier", "")
    grant = _codes.pop(code, None)  # a code works once
    if grant_type != "authorization_code" or client_id != CLIENT_ID or grant is None:
        raise HTTPException(400, "invalid_grant")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    if grant["realm"] != realm or grant["redirect_uri"] != redirect_uri or time.time() - grant["at"] > CODE_TTL \
            or not secrets.compare_digest(challenge, grant["challenge"]):
        raise HTTPException(400, "invalid_grant")
    extra = {"nonce": grant["nonce"]} if grant["nonce"] else {}
    signed = mint(realm, grant["user"], **extra)
    return {"access_token": signed, "id_token": signed, "token_type": "Bearer", "expires_in": TOKEN_TTL}
