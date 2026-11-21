"""Object storage behind one interface. See `base` for the contract and `stores` for how a URL becomes a store."""
from .base import ObjectInfo, ObjectStore, StorageError, safe_key
from .stores import SCHEMES, open_store, provider_of

__all__ = ["ObjectInfo", "ObjectStore", "StorageError", "safe_key", "SCHEMES", "open_store", "provider_of"]
