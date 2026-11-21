"""Turns a storage URL into a store. One adapter serves every provider through fsspec.

    file:///data/lake/meridian/          a directory (the demo, and on-premise file shares)
    s3://bucket/prefix/                  Amazon S3 and anything that speaks its API (MinIO, Ceph)
    abfs://container@account/path/       Azure Data Lake Storage Gen2
    gs://bucket/prefix/                  Google Cloud Storage
    memory://name/                       in-process, for tests

No secret is ever stored with a source. Each provider's driver finds credentials the way that cloud intends:
the workload's own identity (instance role, managed identity, workload identity) or its standard environment
variables. The options below are non-secret settings only.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from .base import ObjectInfo, StorageError, safe_key

# scheme -> (provider name, fsspec protocol, driver package, options a source may set)
SCHEMES: dict[str, tuple[str, str, str, frozenset[str]]] = {
    "file": ("Local directory", "file", "fsspec", frozenset()),
    "memory": ("In-memory (tests)", "memory", "fsspec", frozenset()),
    "s3": ("Amazon S3 or S3-compatible", "s3", "s3fs", frozenset({"endpoint_url", "region_name", "anon"})),
    "abfs": ("Azure Data Lake Storage", "abfs", "adlfs", frozenset({"account_name", "anon"})),
    "abfss": ("Azure Data Lake Storage", "abfs", "adlfs", frozenset({"account_name", "anon"})),
    "gs": ("Google Cloud Storage", "gs", "gcsfs", frozenset({"project", "endpoint_url"})),
}


def provider_of(url: str) -> str:
    scheme = urlsplit(url).scheme
    if scheme not in SCHEMES:
        raise StorageError(f"Unsupported storage location '{url}'. Use one of: {', '.join(f'{s}://' for s in SCHEMES)}")
    return SCHEMES[scheme][0]


def _root_path(url: str) -> str:
    """The provider's own path for the root: 'bucket/prefix' for object stores, an absolute path for files."""
    u = urlsplit(url)
    if u.scheme == "file":
        if u.netloc not in ("", "localhost") or not u.path.startswith("/"):
            raise StorageError("A file location must be an absolute path, for example file:///data/lake/")
        return u.path.rstrip("/") or "/"
    if u.scheme == "memory":
        return "/" + (u.netloc + u.path).strip("/")
    if not u.netloc:
        raise StorageError(f"'{url}' does not name a bucket or container")
    container = u.netloc.split("@")[0] if u.scheme in ("abfs", "abfss") else u.netloc
    return (container + "/" + u.path.strip("/")).rstrip("/")


class FsspecStore:
    """Any fsspec filesystem, rooted at one location."""

    def __init__(self, url: str, fs: Any, root: str):
        self.url, self._fs, self._root = url if url.endswith("/") else url + "/", fs, root.rstrip("/")

    def _path(self, key: str) -> str:
        key = safe_key(key)
        return f"{self._root}/{key}" if key else self._root

    def _info(self, entry: dict) -> ObjectInfo:
        name = str(entry["name"])
        key = name[len(self._root):].lstrip("/") if name.startswith(self._root) else name.lstrip("/")[len(self._root.lstrip("/")):].lstrip("/")
        modified = entry.get("LastModified") or entry.get("last_modified") or entry.get("updated") or entry.get("mtime") or entry.get("created")
        if isinstance(modified, (int, float)):
            modified = datetime.fromtimestamp(modified, timezone.utc)
        elif isinstance(modified, str):
            try:
                modified = datetime.fromisoformat(modified.replace("Z", "+00:00"))
            except ValueError:
                modified = None
        if isinstance(modified, datetime):
            modified = modified.astimezone(timezone.utc).replace(tzinfo=None) if modified.tzinfo else modified
        size = int(entry.get("size") or 0)
        tag = entry.get("ETag") or entry.get("etag") or entry.get("md5Hash") or entry.get("content_md5")
        if not tag:  # plain filesystems have no content tag: size and modification time stand in
            tag = hashlib.sha1(f"{size}:{modified.isoformat() if modified else ''}".encode()).hexdigest()[:20]
        return ObjectInfo(key, size, str(tag).strip('"'), modified if isinstance(modified, datetime) else None)

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        base = self._path(prefix)
        try:
            if not self._fs.exists(base):
                return
            found = self._fs.find(base, detail=True)
        except StorageError:
            raise
        except Exception as e:  # driver errors differ per cloud; the caller needs one kind
            raise StorageError(f"Could not list {self.url}{safe_key(prefix)}: {_plain(e)}") from None
        for entry in sorted(found.values(), key=lambda e: str(e["name"])):
            if entry.get("type", "file") == "file":
                yield self._info(entry)

    @contextmanager
    def open(self, key: str) -> Iterator[BinaryIO]:
        path = self._path(key)
        try:
            f = self._fs.open(path, "rb")
        except Exception as e:
            raise StorageError(f"Could not read {self.url}{safe_key(key)}: {_plain(e)}") from None
        try:
            yield f
        finally:
            f.close()

    def write(self, key: str, data: bytes) -> ObjectInfo:
        path = self._path(key)
        if not safe_key(key):
            raise StorageError("An object needs a name")
        try:
            self._fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
            with self._fs.open(path, "wb") as f:
                f.write(data)
            self._fs.invalidate_cache(path.rsplit("/", 1)[0])
            return self._info(self._fs.info(path))
        except Exception as e:
            raise StorageError(f"Could not write {self.url}{safe_key(key)}: {_plain(e)}") from None

    def exists(self, key: str) -> bool:
        try:
            return bool(self._fs.exists(self._path(key)))
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"Could not reach {self.url}: {_plain(e)}") from None


def _plain(e: Exception) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return text[:200]


def open_store(url: str, options: dict | None = None) -> FsspecStore:
    """A store rooted at `url`. Raises StorageError for an unsupported scheme, option or missing driver."""
    scheme = urlsplit(url).scheme
    provider_of(url)
    _, protocol, driver, allowed = SCHEMES[scheme]
    options = dict(options or {})
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise StorageError(f"Option(s) not allowed for {scheme}:// locations: {', '.join(unknown)}. "
                           f"Allowed: {', '.join(sorted(allowed)) or 'none'}. Credentials are never set here.")
    root = _root_path(url)
    try:
        import fsspec

        kwargs: dict[str, Any] = {}
        if protocol == "s3":
            client = {k: options[k] for k in ("endpoint_url", "region_name") if k in options}
            kwargs = {"client_kwargs": client, "anon": bool(options.get("anon", False)), "skip_instance_cache": True}
        elif protocol == "abfs":
            account = options.get("account_name") or (urlsplit(url).netloc.split("@")[1].split(".")[0] if "@" in urlsplit(url).netloc else None)
            kwargs = {"account_name": account, "anon": bool(options.get("anon", False))}
        elif protocol == "gs":
            kwargs = {k: options[k] for k in ("project", "endpoint_url") if k in options}
        elif protocol == "file":
            kwargs = {"auto_mkdir": False}
        fs = fsspec.filesystem(protocol, **kwargs)
    except ImportError:
        raise StorageError(f"{SCHEMES[scheme][0]} needs the '{driver}' package. Run: uv sync --group {driver}") from None
    except Exception as e:
        raise StorageError(f"Could not open {url}: {_plain(e)}") from None
    return FsspecStore(url, fs, root)
