"""The directory: SCIM provisioning from a client's identity provider, groups mapped to roles, deactivation, MFA policy.

Payloads are the ones Entra ID and Okta actually send (capitalised ops, "False" as a string, path-less PATCH values,
member filters), so a provider pointed at this service works as it does against any other SCIM 2.0 server.
"""
import pytest
import yaml

from app.auth import dev_idp, identity
from app.config import settings
from app.directory.scim import ERROR, GROUP, LIST, MEDIA, PATCH, USER
from app.domain.access import AccessPolicyError, parse_policy
from test_client_api import OP, PEOPLE, h

SCIM = "/api/scim/v2"
GROUPS = {"AML Investigators": {"roles": ["investigator"], "queues": ["aml-l2"]},
          "Fraud Team": {"roles": ["fraud_analyst"], "queues": ["fraud"]},
          "Auditors": {"roles": ["auditor"]}}


@pytest.fixture(scope="module")
def meridian(client):
    """Meridian maps three identity provider groups to roles and requires a second factor of its MLROs."""
    page = client.get("/api/access", headers=h("meridian", "omar")).json()
    raw = yaml.safe_load(page["yaml"])
    raw["version"] = "9.0.0"
    raw["groups"], raw["sign_in"] = GROUPS, {"mfa": ["mlro"]}
    r = client.post("/api/access/publish", json={"source": yaml.safe_dump(raw, sort_keys=False)}, headers=h("meridian", "omar"))
    assert r.status_code == 200, r.text
    assert set(r.json()["changes"]) >= {"Group 'Auditors' gives auditor", "Sign-in: A second factor for: mlro"}
    return client


def token(client, tenant="meridian", admin="omar"):
    r = client.post("/api/directory/tokens", json={"label": "Entra ID"}, headers=h(tenant, admin))
    assert r.status_code == 201, r.text
    return r.json()


def bearer(value):
    return {"Authorization": f"Bearer {value}", "Content-Type": MEDIA}


@pytest.fixture(scope="module")
def scim(meridian):
    return bearer(token(meridian)["token"])


def entra_user(name, **extra):
    return {"schemas": [USER, "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"], "externalId": f"oid-{name}",
            "userName": name, "active": True, "displayName": name.split("@")[0].title(),
            "emails": [{"primary": True, "type": "work", "value": name}], "name": {"givenName": "A", "familyName": "B"}} | extra


def patch(*ops):
    return {"schemas": [PATCH], "Operations": list(ops)}


def make_user(client, scim, name, **extra):
    r = client.post(f"{SCIM}/Users", json=entra_user(name, **extra), headers=scim)
    assert r.status_code == 201, r.text
    assert r.headers["content-type"].startswith(MEDIA) and r.headers["location"].endswith(f"/Users/{r.json()['id']}")
    return r.json()["id"]


def make_group(client, scim, name, members=()):
    r = client.post(f"{SCIM}/Groups", json={"schemas": [GROUP], "displayName": name, "members": [{"value": m} for m in members]}, headers=scim)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def me(client, who, tenant="meridian"):
    return client.get("/api/me", headers=h(tenant, who))


# ---- the policy's groups and sign-in rules ----------------------------------------------------------------------------
@pytest.mark.parametrize("change,message", [
    ({"groups": {"G": {"roles": ["wizard"]}}}, "unknown role 'wizard'"),
    ({"groups": {"G": {"roles": []}}}, "at least one role"),
    ({"groups": {"G": {"roles": ["auditor"], "queues": ["aml-l1"]}}}, "no role that can work cases"),
    ({"groups": {"G": {"roles": ["analyst"], "queues": ["nowhere"]}}}, "unknown queue"),
    ({"groups": {"G": {"roles": ["analyst"], "admin": True}}}, "takes roles and queues"),
    ({"groups": ["G"]}, "maps each identity provider group"),
    ({"sign_in": {"mfa": "sometimes"}}, "none, all, or a list of roles"),
    ({"sign_in": {"mfa": ["wizard"]}}, "unknown role 'wizard'"),
    ({"sign_in": {"mfa": "all", "methods": ["pwd"]}}, "second-factor 'amr' values"),
    ({"sign_in": {"mfa": "all", "remember": True}}, "sign_in takes"),
])
def test_a_bad_groups_or_sign_in_section_is_refused(change, message):
    with pytest.raises(AccessPolicyError, match=message):
        parse_policy(PEOPLE["meridian"] | change)


def test_the_sign_in_rule_says_who_needs_a_second_factor():
    policy = parse_policy(PEOPLE["meridian"] | {"sign_in": {"mfa": ["mlro"], "acr": ["gold"]}})
    assert policy.sign_in.requires_mfa(policy.people["maya"]) and not policy.sign_in.requires_mfa(policy.people["ivan"])
    assert parse_policy(PEOPLE["meridian"] | {"sign_in": {"mfa": "all"}}).sign_in.requires_mfa(policy.people["ivan"])
    assert not parse_policy(PEOPLE["meridian"]).sign_in.requires_mfa(policy.people["maya"])
    who = identity.Identity("maya", amr=("pwd",), acr="gold")
    assert who.second_factor(policy.sign_in.methods, policy.sign_in.acr) and not who.second_factor(policy.sign_in.methods, ())
    assert identity.Identity("maya", amr=("pwd", "otp")).second_factor(policy.sign_in.methods, ())


# ---- tokens -------------------------------------------------------------------------------------------------------------
def test_only_the_clients_own_administrators_make_scim_tokens(meridian):
    assert meridian.post("/api/directory/tokens", json={}, headers=h("meridian", "ivan")).status_code == 403
    assert meridian.post("/api/directory/tokens", json={}, headers=h("meridian", "maya")).status_code == 403
    made = token(meridian)
    assert made["token"].startswith("cfscim_meridian_") and made["hint"] in made["token"]
    listed = meridian.get("/api/directory", headers=h("meridian", "omar")).json()["tokens"]
    assert made["id"] in [t["id"] for t in listed] and all("token" not in t for t in listed)  # shown once, never again
    assert meridian.get("/api/directory", headers=h("meridian", "ivan")).status_code == 403


def test_nothing_is_provisioned_without_a_good_token(meridian, scim):
    for headers in ({}, h("meridian", "omar"), OP, {"Authorization": "Bearer nonsense"}, bearer("cfscim_meridian_forged"),
                    bearer("cfscim_nowhere_x"), {"Authorization": f"Basic {scim['Authorization'].split()[1]}"}):
        r = meridian.get(f"{SCIM}/Users", headers=headers)
        assert r.status_code == 401, headers
        assert r.json()["schemas"] == [ERROR] and r.headers["content-type"].startswith(MEDIA)
    assert meridian.get(f"{SCIM}/Users", headers=scim).status_code == 200


def test_a_token_works_only_for_the_client_that_made_it(meridian, scim):
    kestrel = token(meridian, "kestrel", "pat")["token"]
    make_user(meridian, bearer(kestrel), "only.kestrel@kestrel.example")
    mine = meridian.get(f"{SCIM}/Users", headers=scim).json()
    assert "only.kestrel@kestrel.example" not in [u["userName"] for u in mine["Resources"]]
    # Naming another client in a token does not help: the token is checked against that client's own.
    forged = kestrel.replace("cfscim_kestrel_", "cfscim_meridian_")
    assert meridian.get(f"{SCIM}/Users", headers=bearer(forged)).status_code == 401


def test_a_revoked_token_stops_working_at_once(meridian):
    made = token(meridian)
    assert meridian.get(f"{SCIM}/Users", headers=bearer(made["token"])).status_code == 200
    assert meridian.delete(f"/api/directory/tokens/{made['id']}", headers=h("meridian", "ivan")).status_code == 403
    assert meridian.delete(f"/api/directory/tokens/{made['id']}", headers=h("meridian", "omar")).status_code == 200
    assert meridian.get(f"{SCIM}/Users", headers=bearer(made["token"])).status_code == 401
    assert meridian.delete(f"/api/directory/tokens/{made['id']}", headers=h("meridian", "omar")).status_code == 404


def test_a_suspended_client_cannot_be_provisioned(meridian, scim):
    alder = bearer(token(meridian, "alder", "otto")["token"])
    assert meridian.post("/api/platform/tenants/alder/status", json={"status": "suspended"}, headers=OP).status_code == 200
    try:
        assert meridian.get(f"{SCIM}/Users", headers=alder).status_code == 403
    finally:
        assert meridian.post("/api/platform/tenants/alder/status", json={"status": "live"}, headers=OP).status_code == 200
    assert meridian.get(f"{SCIM}/Users", headers=alder).status_code == 200


# ---- what identity providers send ---------------------------------------------------------------------------------------
def test_discovery_says_what_is_supported(meridian, scim):
    cfg = meridian.get(f"{SCIM}/ServiceProviderConfig", headers=scim).json()
    assert cfg["patch"]["supported"] and not cfg["bulk"]["supported"] and cfg["filter"]["maxResults"] == 200
    types = meridian.get(f"{SCIM}/ResourceTypes", headers=scim).json()
    assert [t["endpoint"] for t in types["Resources"]] == ["/Users", "/Groups"]


def test_entra_id_provisions_a_person_whose_group_gives_them_a_role(meridian, scim):
    # Entra ID first looks the person up, then creates them, then adds them to a group.
    found = meridian.get(f"{SCIM}/Users", params={"filter": 'userName eq "zara@meridian.example"'}, headers=scim).json()
    assert found["schemas"] == [LIST] and found["totalResults"] == 0
    assert me(meridian, "zara@meridian.example").status_code == 403
    uid = make_user(meridian, scim, "zara@meridian.example", title="Investigator")
    assert me(meridian, "zara@meridian.example").status_code == 403  # in no mapped group yet
    gid = make_group(meridian, scim, "AML Investigators")
    r = meridian.patch(f"{SCIM}/Groups/{gid}", json=patch({"op": "Add", "path": "members", "value": [{"value": uid}]}), headers=scim)
    assert r.status_code == 204, r.text
    body = me(meridian, "zara@meridian.example").json()
    assert [q["id"] for q in body["queues"]] == ["aml-l2"] and "investigator" in str(body)
    user = meridian.get(f"{SCIM}/Users/{uid}", headers=scim).json()
    assert user["groups"][0]["display"] == "AML Investigators" and user["emails"][0]["value"] == "zara@meridian.example"
    by_external = meridian.get(f"{SCIM}/Users", params={"filter": 'externalId eq "oid-zara@meridian.example"'}, headers=scim).json()
    assert [u["id"] for u in by_external["Resources"]] == [uid]

    # Entra ID deactivates with a string "False". The person is out at once.
    off = patch({"op": "Replace", "path": "active", "value": "False"})
    assert meridian.patch(f"{SCIM}/Users/{uid}", json=off, headers=scim).json()["active"] is False
    assert me(meridian, "zara@meridian.example").status_code == 403
    on = patch({"op": "Replace", "path": "active", "value": "True"})
    assert meridian.patch(f"{SCIM}/Users/{uid}", json=on, headers=scim).status_code == 200
    assert me(meridian, "zara@meridian.example").status_code == 200

    # Removing them from their only mapped group takes the role away too.
    gone = patch({"op": "Remove", "path": f'members[value eq "{uid}"]'})
    assert meridian.patch(f"{SCIM}/Groups/{gid}", json=gone, headers=scim).status_code == 204
    assert me(meridian, "zara@meridian.example").status_code == 403


def test_okta_style_updates_replace_whole_people_and_groups(meridian, scim):
    a = make_user(meridian, scim, "amir@meridian.example")
    b = make_user(meridian, scim, "bea@meridian.example")
    gid = make_group(meridian, scim, "Fraud Team", [a, b])
    assert me(meridian, "amir@meridian.example").status_code == 200 and me(meridian, "bea@meridian.example").status_code == 200
    # Okta's PUT carries the whole group; leaving bea out removes her.
    r = meridian.put(f"{SCIM}/Groups/{gid}", json={"schemas": [GROUP], "displayName": "Fraud Team", "members": [{"value": a}]}, headers=scim)
    assert [m["value"] for m in r.json()["members"]] == [a]
    assert me(meridian, "bea@meridian.example").status_code == 403
    # A path-less PATCH renames, and a PUT on a user changes their details.
    renamed = patch({"op": "replace", "value": {"id": gid, "displayName": "Fraud Team (EMEA)"}})
    assert meridian.patch(f"{SCIM}/Groups/{gid}", json=renamed, headers=scim).status_code == 204
    assert me(meridian, "amir@meridian.example").status_code == 403  # the policy maps "Fraud Team", not the new name
    back = patch({"op": "replace", "value": {"displayName": "Fraud Team"}})
    assert meridian.patch(f"{SCIM}/Groups/{gid}", json=back, headers=scim).status_code == 204
    put = meridian.put(f"{SCIM}/Users/{a}", json=entra_user("amir@meridian.example", displayName="Amir Haddad", active=False), headers=scim)
    assert put.json()["displayName"] == "Amir Haddad" and me(meridian, "amir@meridian.example").status_code == 403
    listed = meridian.get(f"{SCIM}/Groups", params={"excludedAttributes": "members", "filter": 'displayName eq "Fraud Team"'}, headers=scim).json()
    assert listed["totalResults"] == 1 and "members" not in listed["Resources"][0]


def test_a_path_less_user_patch_and_paging(meridian, scim):
    uid = make_user(meridian, scim, "cy@meridian.example")
    r = meridian.patch(f"{SCIM}/Users/{uid}", headers=scim, json=patch(
        {"op": "Replace", "value": {"displayName": "Cy Okafor", "title": "Auditor", "name.givenName": "Cy",
                                    'emails[type eq "work"].value': "cy.okafor@meridian.example"}}))
    assert r.status_code == 200 and r.json()["displayName"] == "Cy Okafor" and r.json()["emails"][0]["value"] == "cy.okafor@meridian.example"
    total = meridian.get(f"{SCIM}/Users", headers=scim).json()["totalResults"]
    page = meridian.get(f"{SCIM}/Users", params={"startIndex": 2, "count": 1}, headers=scim).json()
    assert page["startIndex"] == 2 and page["itemsPerPage"] == 1 and page["totalResults"] == total


@pytest.mark.parametrize("method,path,body,status,scim_type", [
    ("get", "/Users?filter=userName co \"a\"", None, 400, "invalidFilter"),
    ("get", "/Users?filter=title eq \"x\"", None, 400, "invalidFilter"),
    ("post", "/Users", {"schemas": [GROUP], "userName": "x"}, 400, "invalidSyntax"),
    ("post", "/Users", entra_user("ms.olivia"), 400, "invalidValue"),
    ("post", "/Users", entra_user("has space"), 400, "invalidValue"),
    ("post", "/Users", entra_user("x@y.example", active="maybe"), 400, "invalidValue"),
    ("get", "/Users/nobody", None, 404, None),
    ("get", "/Groups/nobody", None, 404, None),
    ("post", "/Groups", {"schemas": [GROUP], "displayName": "G", "members": [{"value": "nobody"}]}, 400, "invalidValue"),
])
def test_what_is_not_supported_is_refused_in_scims_terms(meridian, scim, method, path, body, status, scim_type):
    r = meridian.request(method.upper(), SCIM + path, json=body, headers=scim)
    assert r.status_code == status, r.text
    assert r.json()["schemas"] == [ERROR] and r.json().get("scimType") == scim_type


def test_duplicates_and_bad_patches_are_refused(meridian, scim):
    uid = make_user(meridian, scim, "dup@meridian.example")
    again = meridian.post(f"{SCIM}/Users", json=entra_user("dup@meridian.example"), headers=scim)
    assert again.status_code == 409 and again.json()["scimType"] == "uniqueness"
    for op, kind in (({"op": "Replace", "path": "password", "value": "x"}, "invalidPath"),
                     ({"op": "Remove", "path": "userName"}, "mutability"), ({"op": "Move", "path": "title"}, "invalidSyntax")):
        r = meridian.patch(f"{SCIM}/Users/{uid}", json=patch(op), headers=scim)
        assert r.status_code == 400 and r.json()["scimType"] == kind
    assert meridian.patch(f"{SCIM}/Users/{uid}", json={"Operations": []}, headers=scim).status_code == 400
    gid = make_group(meridian, scim, "Dup Group")
    assert meridian.post(f"{SCIM}/Groups", json={"schemas": [GROUP], "displayName": "Dup Group"}, headers=scim).status_code == 409
    bad = meridian.patch(f"{SCIM}/Groups/{gid}", json=patch({"op": "add", "path": "owner", "value": "x"}), headers=scim)
    assert bad.status_code == 400 and bad.json()["scimType"] == "invalidPath"


def test_deleting_a_person_or_group_takes_their_roles(meridian, scim):
    uid = make_user(meridian, scim, "eve@meridian.example")
    gid = make_group(meridian, scim, "Auditors", [uid])
    assert me(meridian, "eve@meridian.example").status_code == 200
    assert meridian.delete(f"{SCIM}/Groups/{gid}", headers=scim).status_code == 204
    assert me(meridian, "eve@meridian.example").status_code == 403
    assert meridian.delete(f"{SCIM}/Users/{uid}", headers=scim).status_code == 204
    assert meridian.get(f"{SCIM}/Users/{uid}", headers=scim).status_code == 404


def test_the_policy_still_decides_for_people_it_names(meridian, scim):
    # ivan is an investigator by name. Being in the Auditors group at the identity provider does not change that.
    uid = make_user(meridian, scim, "ivan")
    gid = make_group(meridian, scim, "Auditors 2", [uid])
    assert me(meridian, "ivan").json()["role"] == "investigator"
    overview = meridian.get("/api/directory", headers=h("meridian", "omar")).json()
    row = next(p for p in overview["people"] if p["user_name"] == "ivan")
    assert row["status"] == "named in the policy" and row["roles"] == ["investigator"]
    assert overview["sign_in"] == "A second factor for: mlro" and overview["groups"]["Fraud Team"]["queues"] == ["fraud"]
    # But if the identity provider switches them off, they are off here too.
    meridian.patch(f"{SCIM}/Users/{uid}", json=patch({"op": "replace", "path": "active", "value": False}), headers=scim)
    assert me(meridian, "ivan").status_code == 403
    meridian.patch(f"{SCIM}/Users/{uid}", json=patch({"op": "replace", "path": "active", "value": True}), headers=scim)
    assert me(meridian, "ivan").status_code == 200
    meridian.delete(f"{SCIM}/Groups/{gid}", headers=scim)
    meridian.delete(f"{SCIM}/Users/{uid}", headers=scim)


def test_every_directory_change_is_on_the_access_log(meridian, scim):
    uid = make_user(meridian, scim, "logged@meridian.example")
    meridian.patch(f"{SCIM}/Users/{uid}", json=patch({"op": "replace", "path": "active", "value": "False"}), headers=scim)
    from app.db import session_scope
    from app.domain.models import AccessEvent
    from sqlalchemy import select

    with session_scope("meridian") as s:
        kinds = [e.kind for e in s.scalars(select(AccessEvent).where(AccessEvent.subject == "logged@meridian.example"))]
    assert kinds == ["directory.user_created", "directory.user_deactivated"]


# ---- signing in ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def sso(meridian):
    saved = settings.auth_mode, settings.dev_idp
    object.__setattr__(settings, "auth_mode", "oidc")
    object.__setattr__(settings, "dev_idp", True)
    yield meridian
    object.__setattr__(settings, "auth_mode", saved[0])
    object.__setattr__(settings, "dev_idp", saved[1])


def signed(user, realm="meridian", **claims):
    return {"Authorization": f"Bearer {dev_idp.mint(realm, user, **claims)}"}


def test_a_token_listing_groups_gives_their_roles_to_someone_never_provisioned(sso):
    assert sso.get("/api/me", headers=signed("walk.in@meridian.example")).status_code == 403
    r = sso.get("/api/me", headers=signed("walk.in@meridian.example", groups=["Fraud Team", "Unmapped"], name="Walk In"))
    assert r.status_code == 200 and [q["id"] for q in r.json()["queues"]] == ["fraud"] and r.json()["name"] == "Walk In"
    keycloak = sso.get("/api/me", headers=signed("walk.in@meridian.example", groups=["/Auditors"]))  # a full group path
    assert keycloak.status_code == 200 and keycloak.json()["read_only"] is True  # auditors only read
    assert sso.get("/api/me", headers=signed("walk.in@meridian.example", groups=["Nope"])).status_code == 403
    # Groups in a token never change someone the policy names, and never reach another client.
    assert sso.get("/api/me", headers=signed("ivan", groups=["Auditors"])).json()["role"] == "investigator"
    assert sso.get("/api/me", headers=signed("walk.in@meridian.example", realm="kestrel", groups=["Fraud Team"])).status_code == 403


def test_a_provisioned_person_is_who_the_directory_says_whatever_their_token_lists(sso, scim):
    uid = make_user(sso, scim, "fay@meridian.example")
    assert sso.get("/api/me", headers=signed("fay@meridian.example", groups=["Fraud Team"])).status_code == 403
    gid = make_group(sso, scim, "Auditors 3")
    sso.patch(f"{SCIM}/Groups/{gid}", json=patch({"op": "add", "path": "members", "value": [{"value": uid}]}), headers=scim)
    sso.delete(f"{SCIM}/Groups/{gid}", headers=scim)
    sso.patch(f"{SCIM}/Users/{uid}", json=patch({"op": "replace", "path": "active", "value": False}), headers=scim)
    r = sso.get("/api/me", headers=signed("fay@meridian.example", groups=["Fraud Team"]))
    assert r.status_code == 403 and "deactivated" in r.json()["detail"]


def test_a_second_factor_is_required_where_the_policy_says(sso):
    assert sso.get("/api/me", headers=signed("maya")).status_code == 200  # the development provider reports pwd + otp
    r = sso.get("/api/me", headers=signed("maya", amr=["pwd"]))
    assert r.status_code == 401 and "insufficient_user_authentication" in r.headers["www-authenticate"]
    assert sso.get("/api/me", headers=signed("maya", amr=["pwd", "hwk"])).status_code == 200
    assert sso.get("/api/me", headers=signed("ivan", amr=["pwd"])).status_code == 200  # investigators are not asked


def test_platform_staff_always_need_a_second_factor(sso):
    assert sso.get("/api/platform/overview", headers=signed("olivia", realm=identity.PLATFORM)).status_code == 200
    r = sso.get("/api/platform/overview", headers=signed("olivia", realm=identity.PLATFORM, amr=["pwd"]))
    assert r.status_code == 401 and "second factor" in r.json()["detail"]
    saved = settings.oidc_operator_mfa
    object.__setattr__(settings, "oidc_operator_mfa", False)
    try:
        assert sso.get("/api/platform/overview", headers=signed("olivia", realm=identity.PLATFORM, amr=["pwd"])).status_code == 200
    finally:
        object.__setattr__(settings, "oidc_operator_mfa", saved)
