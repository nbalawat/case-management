"""Write the Keycloak realms Docker Compose imports (`make sso`): one per demo client, and one for platform staff.

Each client's realm holds the people its access policy names, so the same names sign in as in demo mode, plus a few
people only the identity provider knows, in groups, to show roles given by group. Run from the repository root:

    cd backend && PYTHONPATH=. uv run python ../deploy/keycloak/make_realms.py

Local use only: every password is "caseforge".
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.seed import DEMO_CLIENTS, PROVIDER_STAFF
from app.tenants.templates import templates

APP = "http://localhost:8000"
OUT = Path(__file__).resolve().parent / "realms"
CLIENT_ID = "caseforge-web"
# People only the identity provider has: they get a role here only through a group the access policy maps.
GROUP_PEOPLE = {"AML Investigators": [("zara", "Zara Khan")], "Internal Audit": [("ines.audit", "Ines Duarte")]}
STAFF = [("olivia", "Olivia Grant")] + [(m["id"], m["name"]) for m in PROVIDER_STAFF]  # the console operator and the analysts


def client() -> dict:
    return {"clientId": CLIENT_ID, "name": "CaseForge", "publicClient": True, "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False, "implicitFlowEnabled": False,
            "redirectUris": [f"{APP}/api/auth/callback"], "webOrigins": [APP],
            "attributes": {"pkce.code.challenge.method": "S256", "post.logout.redirect.uris": f"{APP}/*"},
            "protocolMappers": [
                {"name": "caseforge audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
                 "config": {"included.client.audience": CLIENT_ID, "access.token.claim": "true", "id.token.claim": "false"}},
                {"name": "groups", "protocol": "openid-connect", "protocolMapper": "oidc-group-membership-mapper",
                 "config": {"claim.name": "groups", "full.path": "false", "access.token.claim": "true", "id.token.claim": "true",
                            "userinfo.token.claim": "true"}}]}


def user(pid: str, name: str, domain: str, groups: list[str] = ()) -> dict:
    first, _, last = name.partition(" ")
    return {"username": pid, "enabled": True, "firstName": first, "lastName": last or first, "email": f"{pid}@{domain}",
            "emailVerified": True, "credentials": [{"type": "password", "value": "caseforge", "temporary": False}],
            "groups": [f"/{g}" for g in groups]}


def realm(rid: str, title: str, people: list[dict]) -> dict:
    return {"realm": rid, "enabled": True, "displayName": title, "loginTheme": "keycloak",
            "accessTokenLifespan": 300, "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 43200,
            "revokeRefreshToken": True, "refreshTokenMaxReuse": 0,  # each refresh token is used once
            "clients": [client()], "groups": [{"name": g} for g in sorted(GROUP_PEOPLE)],
            "users": people}


def build() -> dict[str, dict]:
    """Every realm, by file name."""
    out, tpl = {}, templates()
    for c in DEMO_CLIENTS:
        policy = yaml.safe_load(tpl[c["template"]].access_source)
        domain = f"{c['tenant_id']}.example"
        people = [user(p["id"], p["name"], domain) for p in policy["people"]]
        people += [user(pid, name, domain, [g]) for g, members in GROUP_PEOPLE.items() for pid, name in members]
        out[f"{c['tenant_id']}.json"] = realm(c["tenant_id"], c["name"], people)
    out["platform.json"] = realm("platform", "Platform staff", [user(pid, name, "provider.example") for pid, name in STAFF]) | {"groups": []}
    return out


def render(body: dict) -> str:
    return json.dumps(body, indent=2) + "\n"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, body in build().items():
        (OUT / name).write_text(render(body))


if __name__ == "__main__":
    main()
