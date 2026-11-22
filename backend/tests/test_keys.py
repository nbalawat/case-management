"""Per-client keys: the key-service contract, the sealed format, documents at rest, rotation and crypto-shredding.

The contract tests run against every provider:
  * local      the keyring on disk, here for real;
  * transit    an in-process stand-in that answers as OpenBao's Transit engine documents it (versioned keys,
               vault:vN: tokens, deletion only once allowed). Not the real thing: see the next line;
  * openbao    the real OpenBao, when TEST_TRANSIT_ADDR and TEST_TRANSIT_TOKEN name a disposable dev server with the
               transit engine mounted (CI runs one);
  * aws-kms    AWS KMS as moto emulates it, in-process (moto implements key states, aliases, encryption context and
               on-demand rotation). The real service is checked on the cloud machine with `python -m app.cloudcheck`.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import stat

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import keys
from app.config import settings
from app.keys import KeyDestroyed, KeyServiceError, Tampered, open_sealed, rewrap, seal
from app.keys.local import LocalKeyProvider
from app.keys.transit import TransitKeyProvider
from app.storage import open_store
from app.storage.sealed import SealedStore, forget


# --- a stand-in for OpenBao Transit -------------------------------------------------------------------------------------
class FakeTransit:
    """Just enough of /v1/transit to hold the provider to Transit's behaviour."""

    def __init__(self):
        self.keys: dict[str, dict] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.headers.get("X-Vault-Token") != "root":
            return httpx.Response(403, json={"errors": ["permission denied"]})
        parts = req.url.path.split("/")[3:]  # after /v1/transit
        body = json.loads(req.content) if req.content else {}
        op, name = parts[0], parts[1]
        k = self.keys.get(name)
        if op == "keys" and len(parts) == 2:
            if req.method == "GET":
                return httpx.Response(200, json={"data": {"latest_version": len(k["v"]), "keys": {str(i + 1): 1700000000 + i for i in range(len(k["v"]))}}}) if k else httpx.Response(404, json={"errors": []})
            if req.method == "POST":
                self.keys.setdefault(name, {"v": [secrets.token_bytes(32)], "deletable": False})
                return httpx.Response(204)
            if req.method == "DELETE":
                if not k or not k["deletable"]:
                    return httpx.Response(400, json={"errors": ["deletion is not allowed for this key"]})
                del self.keys[name]
                return httpx.Response(204)
        if k is None and op == "encrypt":  # as real Transit does: encrypting under a missing key makes one ("upsert")
            k = self.keys.setdefault(name, {"v": [secrets.token_bytes(32)], "deletable": False})
        if k is None and op == "decrypt":  # as real Transit (OpenBao 2.x) answers: 400, not 404
            return httpx.Response(400, json={"errors": ["encryption key not found"]})
        if k is None:
            return httpx.Response(404, json={"errors": []})
        if op == "keys" and parts[2] == "rotate":
            k["v"].append(secrets.token_bytes(32))
            return httpx.Response(204)
        if op == "keys" and parts[2] == "config":
            k["deletable"] = bool(body.get("deletion_allowed"))
            return httpx.Response(204)
        if op == "encrypt":
            n, v = secrets.token_bytes(12), len(k["v"])
            ct = AESGCM(k["v"][v - 1]).encrypt(n, base64.b64decode(body["plaintext"]), None)
            return httpx.Response(200, json={"data": {"ciphertext": f"vault:v{v}:{base64.b64encode(n + ct).decode()}"}})
        if op == "decrypt":
            _, v, blob = body["ciphertext"].split(":", 2)
            raw = base64.b64decode(blob)
            try:
                pt = AESGCM(k["v"][int(v[1:]) - 1]).decrypt(raw[:12], raw[12:], None)
            except Exception:
                return httpx.Response(400, json={"errors": ["cipher: message authentication failed"]})
            return httpx.Response(200, json={"data": {"plaintext": base64.b64encode(pt).decode()}})
        return httpx.Response(405)


REAL = os.getenv("TEST_TRANSIT_ADDR", "")


@pytest.fixture
def kms():
    """An AWS KMS client against moto's in-process emulation, with throwaway credentials."""
    moto = pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    saved = {k: os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")}
    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_SESSION_TOKEN="testing")
    with moto.mock_aws():
        yield boto3.client("kms", region_name="us-east-1")
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _aws(kms, **kw):
    from app.keys.aws_kms import AwsKmsKeyProvider

    return AwsKmsKeyProvider("us-east-1", client=kms, sleep=lambda _: None, **kw)


@pytest.fixture(params=["local", "transit", "openbao", "aws-kms"])
def kp(request, tmp_path):
    if request.param == "aws-kms":
        return _aws(request.getfixturevalue("kms"))
    if request.param == "local":
        return LocalKeyProvider(tmp_path / "keys", base64.b64encode(secrets.token_bytes(32)).decode())
    if request.param == "transit":
        return TransitKeyProvider("http://bao.test:8200", "root", client=httpx.Client(transport=httpx.MockTransport(FakeTransit())))
    if not REAL:
        pytest.skip("set TEST_TRANSIT_ADDR and TEST_TRANSIT_TOKEN to a disposable OpenBao dev server")
    return TransitKeyProvider(REAL, os.getenv("TEST_TRANSIT_TOKEN", ""), prefix=f"cf-test-{secrets.token_hex(4)}-")


# --- the contract -------------------------------------------------------------------------------------------------------
def test_a_client_key_is_made_once_and_wraps_and_unwraps_data_keys(kp):
    assert kp.info("acme").version == 0
    first = kp.create("acme")
    assert first.version == 1 and kp.create("acme").version == 1  # idempotent
    dk = secrets.token_bytes(32)
    token = kp.wrap("acme", dk)
    assert dk not in token.encode() and base64.b64encode(dk).decode() not in token
    assert kp.unwrap("acme", token) == dk


def test_one_clients_key_does_not_open_anothers_data_keys(kp):
    kp.create("acme")
    kp.create("bolt")
    token = kp.wrap("acme", secrets.token_bytes(32))
    with pytest.raises(KeyServiceError):
        kp.unwrap("bolt", token)


def test_after_rotation_new_keys_use_the_new_version_and_old_ones_still_open(kp):
    kp.create("acme")
    old_dk = secrets.token_bytes(32)
    old = kp.wrap("acme", old_dk)
    assert kp.rotate("acme").version == 2
    new = kp.wrap("acme", secrets.token_bytes(32))
    assert ":v1:" in old and ":v2:" in new
    assert kp.unwrap("acme", old) == old_dk


def test_a_destroyed_key_opens_nothing_ever_again(kp):
    kp.create("acme")
    token = kp.wrap("acme", secrets.token_bytes(32))
    assert kp.destroy("acme").destroyed
    with pytest.raises(KeyDestroyed):
        kp.unwrap("acme", token)
    with pytest.raises(KeyServiceError):
        kp.wrap("acme", secrets.token_bytes(32))


@pytest.mark.parametrize("bad", ["", "Acme", "../etc", "a" * 41, "acme/../bolt"])
def test_a_client_id_that_is_not_one_is_refused(kp, bad):
    with pytest.raises(KeyServiceError, match="not a client id"):
        kp.create(bad)


# --- the local keyring's own guarantees ----------------------------------------------------------------------------------
def test_the_keyring_holds_no_key_in_the_clear_and_only_its_owner_can_read_it(tmp_path):
    kp = LocalKeyProvider(tmp_path / "keys", base64.b64encode(secrets.token_bytes(32)).decode())
    kp.create("acme")
    path = tmp_path / "keys" / "acme.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    ring = json.loads(path.read_text())
    assert set(ring["versions"]) == {"1"} and len(base64.b64decode(ring["versions"]["1"])) == 12 + 32 + 16  # nonce, sealed key, tag


def test_another_master_key_cannot_use_the_keyring(tmp_path):
    a = LocalKeyProvider(tmp_path / "keys", base64.b64encode(secrets.token_bytes(32)).decode())
    a.create("acme")
    token = a.wrap("acme", secrets.token_bytes(32))
    b = LocalKeyProvider(tmp_path / "keys", base64.b64encode(secrets.token_bytes(32)).decode())
    with pytest.raises(Exception):
        b.unwrap("acme", token)


def test_without_a_master_key_one_is_written_once_beside_the_keyring(tmp_path):
    kp = LocalKeyProvider(tmp_path / "keys")
    kp.create("acme")
    master = (tmp_path / "keys" / "master.key")
    first = master.read_text()
    assert stat.S_IMODE(master.stat().st_mode) == 0o600
    token = kp.wrap("acme", b"k" * 32)
    assert LocalKeyProvider(tmp_path / "keys").unwrap("acme", token) == b"k" * 32 and master.read_text() == first


@pytest.mark.parametrize("master", ["not base64!", base64.b64encode(b"short").decode()])
def test_a_bad_master_key_is_refused(tmp_path, master):
    with pytest.raises(KeyServiceError, match="KEYS_MASTER_KEY"):
        LocalKeyProvider(tmp_path / "keys", master).create("acme")


def test_a_destroyed_client_is_not_given_a_new_key_and_its_old_key_bytes_are_gone(tmp_path):
    kp = LocalKeyProvider(tmp_path / "keys", base64.b64encode(secrets.token_bytes(32)).decode())
    kp.create("acme")
    before = json.loads((tmp_path / "keys" / "acme.json").read_text())["versions"]["1"]
    kp.destroy("acme")
    after = (tmp_path / "keys" / "acme.json").read_text()
    assert before not in after and json.loads(after)["destroyed"]
    with pytest.raises(KeyDestroyed, match="not given a new key"):
        kp.create("acme")


def test_the_transit_provider_reports_a_refusal_and_an_absent_service():
    refused = TransitKeyProvider("http://bao.test:8200", "wrong", client=httpx.Client(transport=httpx.MockTransport(FakeTransit())))
    with pytest.raises(KeyServiceError, match="permission denied"):
        refused.create("acme")
    def down(_):
        raise httpx.ConnectError("refused")
    gone = TransitKeyProvider("http://bao.test:8200", "root", client=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(KeyServiceError, match="did not answer"):
        gone.info("acme")
    with pytest.raises(KeyServiceError, match="TRANSIT_ADDR"):
        TransitKeyProvider("", "root")


# --- AWS KMS's own guarantees -------------------------------------------------------------------------------------------
def test_each_client_has_its_own_tagged_kms_key_behind_an_alias(kms):
    kp = _aws(kms)
    a, b = kp.create("acme"), kp.create("bolt")
    assert a.reference != b.reference and a.reference.startswith("arn:aws:kms:us-east-1:")
    aliases = {x["AliasName"]: x["TargetKeyId"] for x in kms.list_aliases()["Aliases"]}
    assert {"alias/caseforge-client-acme", "alias/caseforge-client-bolt"} <= set(aliases)
    tags = {t["TagKey"]: t["TagValue"] for t in kms.list_resource_tags(KeyId=aliases["alias/caseforge-client-acme"])["Tags"]}
    assert tags["caseforge:client"] == "acme"
    assert kms.get_key_rotation_status(KeyId=aliases["alias/caseforge-client-acme"])["KeyRotationEnabled"]
    assert len(kms.list_keys()["Keys"]) == 2 and kp.create("acme").reference == a.reference  # made once


def test_a_kms_token_is_bound_to_its_client_by_the_encryption_context(kms):
    kp = _aws(kms)
    kp.create("acme")
    token = kp.wrap("acme", secrets.token_bytes(32))
    blob = base64.b64decode(token.split(":", 2)[2])
    arn = kp.info("acme").reference
    with pytest.raises(Exception):  # the context is part of the ciphertext: without it, KMS refuses
        kms.decrypt(KeyId=arn, CiphertextBlob=blob)
    assert len(kms.decrypt(KeyId=arn, CiphertextBlob=blob, EncryptionContext={"caseforge:client": "acme"})["Plaintext"]) == 32


def test_destroying_a_kms_key_disables_it_now_and_schedules_deletion(kms):
    kp = _aws(kms, deletion_days=7)
    kp.create("acme")
    arn = kp.info("acme").reference
    kp.destroy("acme")
    meta = kms.describe_key(KeyId=arn)["KeyMetadata"]
    assert meta["KeyState"] == "PendingDeletion" and not meta["Enabled"] and meta.get("DeletionDate")
    assert kp.info("acme").destroyed and kp.destroy("acme").destroyed  # idempotent
    with pytest.raises(KeyDestroyed, match="not given a new key"):
        kp.create("acme")


def test_a_kms_key_that_never_existed_is_missing_not_made(kms):
    kp = _aws(kms)
    with pytest.raises(KeyDestroyed, match="not in AWS KMS"):
        kp.wrap("ghost", secrets.token_bytes(32))
    assert kms.list_keys()["Keys"] == []


@pytest.mark.parametrize("token", ["vault:v1:abc", "kms:v1:not base64!", "kms:vx:AAAA"])
def test_a_token_that_is_not_a_kms_one_is_refused(kms, token):
    kp = _aws(kms)
    kp.create("acme")
    with pytest.raises(KeyServiceError):
        kp.unwrap("acme", token)


def test_kms_settings_are_checked():
    from app.keys.aws_kms import AwsKmsKeyProvider

    with pytest.raises(KeyServiceError, match="between 7 and 30"):
        AwsKmsKeyProvider(client=object(), deletion_days=3)
    with pytest.raises(KeyServiceError, match="alias prefix"):
        AwsKmsKeyProvider(client=object(), alias_prefix="aws/mine")


def test_kms_refusals_read_as_key_service_errors():
    from botocore.exceptions import ClientError, EndpointConnectionError

    from app.keys.aws_kms import AwsKmsKeyProvider

    class Denied:
        def describe_key(self, **_):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not allowed"}}, "DescribeKey")

    class Away:
        def describe_key(self, **_):
            raise EndpointConnectionError(endpoint_url="https://kms.us-east-1.amazonaws.com")

    with pytest.raises(KeyServiceError, match="missing a KMS permission"):
        AwsKmsKeyProvider(client=Denied()).info("acme")
    with pytest.raises(KeyServiceError, match="did not answer"):
        AwsKmsKeyProvider(client=Away()).info("acme")


def test_the_configured_provider_can_be_aws_kms(kms):
    saved = _provider("aws-kms")
    try:
        p = keys.provider()
        assert p.name == "aws-kms" and p.prefix == settings.kms_alias_prefix
    finally:
        _provider(saved)


# --- the sealed format ------------------------------------------------------------------------------------------------------
@pytest.fixture
def local(tmp_path):
    kp = LocalKeyProvider(tmp_path / "keys", base64.b64encode(secrets.token_bytes(32)).decode())
    kp.create("acme")
    kp.create("bolt")
    return kp


DOC = b"Passport GB 123456789, Jane Example, born 1980-01-01"


def test_a_sealed_object_holds_no_plaintext_and_opens_to_the_original(local):
    blob = seal(local, "acme", "P1/ev-1/passport.txt", DOC)
    assert b"Jane Example" not in blob and blob.startswith(b"CFK1")
    assert open_sealed(local, "acme", "P1/ev-1/passport.txt", blob) == DOC
    assert seal(local, "acme", "P1/ev-1/passport.txt", DOC) != blob  # a fresh data key and nonce every time


@pytest.mark.parametrize("change", ["flip a ciphertext bit", "other location", "other client", "damaged header"])
def test_a_changed_moved_or_borrowed_object_does_not_open(local, change):
    blob = seal(local, "acme", "P1/ev-1/passport.txt", DOC)
    tenant, where = "acme", "P1/ev-1/passport.txt"
    if change == "flip a ciphertext bit":
        blob = blob[:-5] + bytes([blob[-5] ^ 1]) + blob[-4:]
    elif change == "other location":
        where = "P2/ev-9/passport.txt"
    elif change == "other client":
        tenant = "bolt"
    else:
        blob = blob[:6] + b"][" + blob[8:]  # the header is no longer JSON
    with pytest.raises(KeyServiceError):
        open_sealed(local, tenant, where, blob)


def test_tampering_is_reported_as_such(local):
    blob = seal(local, "acme", "k", DOC)
    with pytest.raises(Tampered):
        open_sealed(local, "acme", "k", blob[:-1] + bytes([blob[-1] ^ 1]))


def test_an_object_stored_before_sealing_reads_as_it_was(local):
    assert open_sealed(local, "acme", "old.txt", b"written before keys existed") == b"written before keys existed"


def test_rewrapping_moves_an_object_to_the_new_key_version_without_touching_its_data(local):
    blob = seal(local, "acme", "k", DOC)
    local.rotate("acme")
    again = rewrap(local, "acme", blob)
    assert b":v1:" in blob[:200] and b":v2:" in again[:200] and blob[-len(DOC) - 16:] == again[-len(DOC) - 16:]
    assert open_sealed(local, "acme", "k", again) == DOC


# --- documents at rest, through the application ---------------------------------------------------------------------------
def test_the_document_store_seals_what_it_writes(tmp_path, local):
    store = SealedStore(open_store(f"file://{tmp_path / 'docs'}/"), "acme", local)
    store.write("P1/ev-1/passport.txt", DOC)
    on_disk = (tmp_path / "docs" / "P1" / "ev-1" / "passport.txt").read_bytes()
    assert b"Jane" not in on_disk and store.is_sealed("P1/ev-1/passport.txt")
    with store.open("P1/ev-1/passport.txt") as f:
        assert f.read() == DOC
    (tmp_path / "docs" / "P1" / "old.txt").write_bytes(b"legacy")
    with store.open("P1/old.txt") as f:
        assert f.read() == b"legacy"
    assert not store.rewrap("P1/old.txt") and store.rewrap("P1/ev-1/passport.txt")


def _provider(name):
    saved = settings.key_provider
    object.__setattr__(settings, "key_provider", name)
    keys.reset()
    return saved


def test_with_no_key_service_documents_are_stored_as_given(tmp_path):
    from app.storage.sealed import sealed

    saved = _provider("none")
    try:
        inner = open_store(f"file://{tmp_path}/")
        assert sealed(inner, "acme") is inner
    finally:
        _provider(saved)


def test_an_unknown_key_provider_is_refused():
    saved = _provider("kms-of-my-dreams")
    try:
        with pytest.raises(KeyServiceError, match="KEY_PROVIDER is one of"):
            keys.provider()
    finally:
        _provider(saved)


# --- the demo, over HTTP ---------------------------------------------------------------------------------------------------
from test_client_api import OP, h  # noqa: E402


def test_a_documents_bytes_in_storage_are_sealed_and_the_page_still_shows_it(client):
    import pathlib

    from sqlalchemy import select

    import app.db as appdb
    from app.domain import models as m

    with appdb.session_scope("meridian") as s:
        party = s.scalars(select(m.Party.id).order_by(m.Party.id)).first()
    secret = b"Account 004417720931, 1 High Street"
    r = client.post(f"/api/customers/{party}/evidence", headers=h("meridian", "kai"),
                    json={"kind": "proof_of_address", "title": "Electricity bill", "filename": "sealed-bill.txt", "media_type": "text/plain",
                          "content_base64": base64.b64encode(secret).decode()})
    assert r.status_code in (200, 201), r.text
    root = pathlib.Path(settings.evidence_url.removeprefix("file://"), "meridian")
    stored = next(root.rglob("sealed-bill.txt")).read_bytes()
    assert secret not in stored and stored.startswith(b"CFK1")
    eid = r.json()["id"]
    shown = client.get(f"/api/evidence/{eid}/content", headers=h("meridian", "kai"))
    assert shown.status_code == 200 and secret in shown.content


def test_the_operator_sees_every_clients_key_and_rotates_one(client):
    page = client.get("/api/platform/keys", headers=OP).json()
    assert page["provider"] == "local" and {c["tenant"] for c in page["clients"]} >= {"meridian", "kestrel", "alder"}
    before = next(c for c in page["clients"] if c["tenant"] == "meridian")
    r = client.post("/api/platform/keys/meridian/rotate", headers=OP)
    assert r.status_code == 200 and r.json()["version"] == before["version"] + 1 and r.json()["rewrapped"] == before["documents"]["sealed"]
    assert client.post("/api/platform/keys/nowhere/rotate", headers=OP).status_code == 404
    assert client.post("/api/platform/keys/meridian/rotate", headers={"X-Operator": "priya"}).status_code == 403
    assert client.get("/api/platform/keys", headers=h("meridian", "omar")).status_code == 403
    log = client.get("/api/access/managed-service", headers=h("kestrel", "pat")).json()["log"]
    assert isinstance(log, list)
    forget("meridian")
