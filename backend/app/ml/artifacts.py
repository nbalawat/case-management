"""Where trained models are kept: any object store (MODEL_STORE_URL). A model is loaded only if its bytes still have
the digest recorded when it was trained, so a file changed in the store is refused rather than run."""
from __future__ import annotations

import hashlib
import io
import pickle
from functools import lru_cache
from pathlib import Path

from ..config import settings
from ..storage import open_store


class ArtifactError(RuntimeError):
    pass


def _store():
    url = settings.model_store_url
    if url.startswith("file://"):
        Path(url.removeprefix("file://")).mkdir(parents=True, exist_ok=True)
    return open_store(url)


def save(key: str, obj: dict) -> tuple[str, str]:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    _store().write(key, data)
    return key, hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=16)
def load(key: str, sha256: str) -> dict:
    with _store().open(key) as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ArtifactError(f"The model file {key} has changed since it was trained; it will not be loaded")
    return pickle.loads(io.BytesIO(data).read())  # noqa: S301  our own artifact, digest checked above
