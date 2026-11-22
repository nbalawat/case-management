"""`python -m app.cloudcheck`: the one command that proves storage and keys on a real account, run here on emulators."""
import base64
import os
import secrets

import pytest

from app import cloudcheck, keys
from app.config import settings
from app.keys.local import LocalKeyProvider


def _run(monkeypatch, provider, *argv):
    monkeypatch.setattr(keys, "provider", lambda: provider)
    lines: list[str] = []
    return cloudcheck.main(list(argv), out=lines.append), lines


def test_every_check_passes_on_a_directory_and_the_local_keyring(tmp_path, monkeypatch):
    kp = LocalKeyProvider(tmp_path / "keys", base64.b64encode(secrets.token_bytes(32)).decode())
    code, lines = _run(monkeypatch, kp, "all", f"file://{tmp_path}/evidence")
    assert code == 0 and lines[-1] == "PASSED", "\n".join(lines)
    assert not any(l.startswith("FAIL") for l in lines)
    assert not list((tmp_path / "evidence").rglob("*.csv"))  # the probe cleans up after itself


def test_storage_and_aws_kms_on_emulated_aws(monkeypatch):
    moto_server = pytest.importorskip("moto.server")
    import boto3

    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    server = moto_server.ThreadedMotoServer(port=0, verbose=False)
    server.start()
    try:
        endpoint = f"http://127.0.0.1:{server.get_host_and_port()[1]}"
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        boto3.client("s3", endpoint_url=endpoint).create_bucket(Bucket="caseforge-data")
        from app.keys.aws_kms import AwsKmsKeyProvider

        kms = boto3.client("kms", endpoint_url=endpoint, region_name="us-east-1")
        kp = AwsKmsKeyProvider("us-east-1", client=kms, sleep=lambda _: None)
        code, lines = _run(monkeypatch, kp, "all", "s3://caseforge-data/check", "--endpoint-url", endpoint)
        assert code == 0, "\n".join(lines)
        assert any("aws-kms" in l for l in lines)
        states = [kms.describe_key(KeyId=k["KeyId"])["KeyMetadata"]["KeyState"] for k in kms.list_keys()["Keys"]]
        assert states == ["PendingDeletion"]  # the probe key is shredded, not left running
        assert boto3.client("s3", endpoint_url=endpoint).list_objects_v2(Bucket="caseforge-data").get("KeyCount", 0) == 0
    finally:
        server.stop()


def test_a_failing_step_is_reported_and_fails_the_run(tmp_path, monkeypatch):
    code, lines = _run(monkeypatch, None, "storage", "ftp://nowhere/x")
    assert code == 1 and lines[-1].startswith("FAILED")


def test_with_no_key_service_the_key_check_is_skipped(monkeypatch):
    code, lines = _run(monkeypatch, None, "keys")
    assert code == 0 and any("skipped" in l for l in lines)
