"""Clouds behind one interface. Adding one: a module here with a `Cloud` subclass, registered in CLOUDS, plus
infra/modules/<cloud>/machine (the machine contract) and infra/stacks/<cloud>/machine."""

from __future__ import annotations

from typing import Dict, Type

from .aws import Aws
from .base import Cloud, NotSupported, PendingCloud

CLOUDS: Dict[str, Type[Cloud]] = {"aws": Aws}
PENDING = ("azure", "gcp")


def get_cloud(name: str) -> Cloud:
    if name in CLOUDS:
        return CLOUDS[name]()
    if name in PENDING:
        raise PendingCloud(name).missing()
    raise NotSupported(f"unknown cloud '{name}' (known: {', '.join(sorted(CLOUDS) + list(PENDING))})")


__all__ = ["Cloud", "CLOUDS", "NotSupported", "PENDING", "get_cloud"]
