"""The Keycloak realms Compose imports (`make sso`) are generated from the demo clients' access policies. If a policy
gains a person, regenerate them: cd backend && PYTHONPATH=. uv run python ../deploy/keycloak/make_realms.py"""
import importlib.util
from pathlib import Path

import yaml

from app.seed import DEMO_CLIENTS
from app.tenants.templates import templates

HERE = Path(__file__).resolve().parents[2] / "deploy" / "keycloak"


def _maker():
    spec = importlib.util.spec_from_file_location("make_realms", HERE / "make_realms.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_committed_realms_are_what_the_generator_writes():
    mod = _maker()
    built = mod.build()
    assert sorted(built) == sorted(p.name for p in (HERE / "realms").glob("*.json"))
    for name, body in built.items():
        assert (HERE / "realms" / name).read_text() == mod.render(body), f"{name} is stale: regenerate the realms"


def test_every_person_a_demo_policy_names_can_sign_in_at_keycloak_and_the_client_is_locked_down():
    built = _maker().build()
    for c in DEMO_CLIENTS:
        realm = built[f"{c['tenant_id']}.json"]
        names = {u["username"] for u in realm["users"]}
        policy = yaml.safe_load(templates()[c["template"]].access_source)
        assert {p["id"] for p in policy["people"]} <= names
        web = realm["clients"][0]
        assert web["publicClient"] and not web["directAccessGrantsEnabled"] and not web["implicitFlowEnabled"]
        assert web["attributes"]["pkce.code.challenge.method"] == "S256"
        assert web["redirectUris"] == ["http://localhost:8000/api/auth/callback"]
        assert {m["protocolMapper"] for m in web["protocolMappers"]} == {"oidc-audience-mapper", "oidc-group-membership-mapper"}
    assert "olivia" in {u["username"] for u in built["platform.json"]["users"]}
