"""One contract, every kind of store: a directory, an in-memory store, and S3 through an emulated S3 API.

Azure Data Lake and Google Cloud Storage go through the same adapter with their own drivers. Those drivers are not
installed here and no real cloud account was used, so for those two only the URL handling is tested.
"""
import os
import uuid

import pytest

from app.storage import StorageError, open_store, provider_of, safe_key
from app.storage.stores import _root_path


@pytest.fixture(scope="module")
def s3_endpoint():
    moto = pytest.importorskip("moto.server")
    pytest.importorskip("s3fs")
    saved = {k: os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "NO_PROXY", "no_proxy")}
    os.environ.update(AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test", AWS_DEFAULT_REGION="us-east-1",
                      NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    server = moto.ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://127.0.0.1:{port}"
    import boto3

    boto3.client("s3", endpoint_url=endpoint).create_bucket(Bucket="client-landing")
    yield endpoint
    server.stop()
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture(params=["file", "memory", "s3"])
def store(request, tmp_path):
    if request.param == "file":
        return open_store(f"file://{tmp_path}/root")
    if request.param == "memory":
        return open_store(f"memory://{uuid.uuid4().hex}/root")
    endpoint = request.getfixturevalue("s3_endpoint")
    return open_store(f"s3://client-landing/{uuid.uuid4().hex}/root", {"endpoint_url": endpoint})


def test_empty_location_lists_nothing(store):
    assert list(store.list()) == [] and list(store.list("nowhere")) == [] and not store.exists("a.csv")


def test_write_list_read_round_trip(store):
    store.write("core/transactions/dt=2026-01-02/part-0.csv", b"a,b\n1,2\n")
    store.write("core/transactions/dt=2026-01-01/part-0.csv", b"a,b\n")
    store.write("core/customers.csv", b"x")
    keys = [o.key for o in store.list()]
    assert keys == ["core/customers.csv", "core/transactions/dt=2026-01-01/part-0.csv", "core/transactions/dt=2026-01-02/part-0.csv"]
    assert [o.key for o in store.list("core/transactions")] == keys[1:]
    assert [o.key for o in store.list("core/transactions/")] == keys[1:]
    with store.open(keys[2]) as f:
        assert f.read() == b"a,b\n1,2\n"
    info = {o.key: o for o in store.list()}
    assert info[keys[2]].size == 8 and info["core/customers.csv"].size == 1 and all(o.fingerprint for o in info.values())
    assert store.exists("core/customers.csv") and not store.exists("core/nope.csv")


def test_fingerprint_changes_when_content_changes(store):
    first = store.write("a/file.csv", b"one")
    same = next(o for o in store.list() if o.key == "a/file.csv")
    assert same.fingerprint == first.fingerprint
    second = store.write("a/file.csv", b"two two")
    assert second.fingerprint != first.fingerprint and next(o for o in store.list()).fingerprint == second.fingerprint


@pytest.mark.parametrize("key", ["../outside.csv", "a/../../outside.csv", "/etc/passwd", "a\\b.csv", "a/\x00b"])
def test_keys_cannot_leave_the_location(store, key):
    for call in (lambda: store.open(key).__enter__(), lambda: store.write(key, b"x"), lambda: store.exists(key), lambda: list(store.list(key))):
        with pytest.raises(StorageError):
            call()


def test_a_store_sees_only_its_own_root(store, tmp_path):
    """Two stores side by side under one parent: neither lists nor reads the other's objects."""
    base = store.url.rstrip("/").rsplit("/", 1)[0]
    options = {"endpoint_url": store._fs.client_kwargs["endpoint_url"]} if base.startswith("s3://") else None
    a, b = open_store(base + "/tenant-a", options), open_store(base + "/tenant-b", options)
    a.write("data.csv", b"A")
    b.write("data.csv", b"B")
    assert [o.key for o in a.list()] == ["data.csv"] and [o.key for o in b.list()] == ["data.csv"]
    with a.open("data.csv") as f:
        assert f.read() == b"A"
    with pytest.raises(StorageError):
        a.open("../tenant-b/data.csv").__enter__()
    assert not a.exists("tenant-b/data.csv")


def test_reading_a_missing_object_is_a_storage_error(store):
    with pytest.raises(StorageError) as e:
        store.open("missing.csv").__enter__()
    assert "missing.csv" in str(e.value)


def test_safe_key_normalises():
    assert safe_key("a//b/./c.csv") == "a/b/c.csv" and safe_key("") == "" and safe_key("a/b/") == "a/b"


@pytest.mark.parametrize("url,root", [
    ("s3://bucket/prefix/deep/", "bucket/prefix/deep"), ("s3://bucket", "bucket"), ("gs://bucket/landing", "bucket/landing"),
    ("abfs://container@account.dfs.core.windows.net/landing/core", "container/landing/core"), ("abfss://container@account/landing", "container/landing"),
    ("file:///data/lake/meridian/", "/data/lake/meridian"),
])
def test_urls_for_every_provider_resolve_to_a_root(url, root):
    assert _root_path(url) == root and provider_of(url)


@pytest.mark.parametrize("url,options,says", [
    ("ftp://host/path", None, "Unsupported"), ("http://example.com/x", None, "Unsupported"), ("/just/a/path", None, "Unsupported"),
    ("s3:///no-bucket", None, "bucket"), ("file://relative/path", None, "absolute"),
    ("s3://bucket/x", {"key": "AKIA", "secret": "x"}, "Credentials are never set here"), ("gs://bucket/x", {"token": "x"}, "not allowed"),
    ("file:///tmp/x", {"auto_mkdir": True}, "not allowed"),
])
def test_bad_locations_and_secret_options_are_refused(url, options, says):
    with pytest.raises(StorageError) as e:
        open_store(url, options)
    assert says in str(e.value)


def test_a_missing_cloud_driver_is_reported_plainly():
    for url, driver in (("abfs://container@account/x", "adlfs"), ("gs://bucket/x", "gcsfs")):
        try:
            __import__(driver)
        except ImportError:
            with pytest.raises(StorageError) as e:
                open_store(url)
            assert driver in str(e.value)
