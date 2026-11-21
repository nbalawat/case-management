"""Per-client keys. `provider()` is the configured key service; see base.py for the contract and envelope.py for the format."""
from __future__ import annotations

import functools

from ..config import settings
from .base import KeyDestroyed, KeyInfo, KeyProvider, KeyServiceError
from .envelope import Tampered, is_sealed, open_sealed, rewrap, seal

PROVIDERS = ("local", "transit", "none")


@functools.cache
def provider() -> KeyProvider | None:
    """The key service in KEY_PROVIDER, or None when sealing is off (KEY_PROVIDER=none)."""
    kind = settings.key_provider
    if kind == "none":
        return None
    if kind == "local":
        from .local import LocalKeyProvider

        return LocalKeyProvider(settings.keys_dir, settings.keys_master_key)
    if kind == "transit":
        from .transit import TransitKeyProvider

        return TransitKeyProvider(settings.transit_addr, settings.transit_token, settings.transit_mount, settings.transit_prefix)
    raise KeyServiceError(f"KEY_PROVIDER is one of {', '.join(PROVIDERS)}, not '{kind}'")


def reset() -> None:
    """Forget the configured provider, after a settings change (tests)."""
    provider.cache_clear()


__all__ = ["KeyDestroyed", "KeyInfo", "KeyProvider", "KeyServiceError", "Tampered", "is_sealed", "open_sealed", "provider", "reset",
           "rewrap", "seal"]
