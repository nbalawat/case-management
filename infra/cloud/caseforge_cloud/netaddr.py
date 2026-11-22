"""The operator's public address, so the machine's firewall can admit SSH from it alone."""

from __future__ import annotations

import ipaddress
import urllib.request
from typing import Callable, Optional, Sequence

Fetch = Callable[[str], str]

DEFAULT_SOURCES = ("https://checkip.amazonaws.com", "https://api.ipify.org", "https://ifconfig.me/ip")


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "caseforge-cloud"})
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - fixed https URLs
        return resp.read(64).decode("ascii", "replace")


def public_ipv4(sources: Sequence[str] = DEFAULT_SOURCES, fetch: Fetch = _fetch) -> Optional[str]:
    """The first answer from `sources` that is a public IPv4 address, or None."""
    for url in sources:
        try:
            text = fetch(url).strip()
            addr = ipaddress.IPv4Address(text)
        except (OSError, ValueError):
            continue
        if addr.is_global:
            return str(addr)
    return None


def normalise_cidr(value: str) -> str:
    """'203.0.113.7' -> '203.0.113.7/32'; refuses the whole internet."""
    net = ipaddress.ip_network(value if "/" in value else f"{value}/32" if ":" not in value else f"{value}/128", strict=False)
    if net.prefixlen == 0:
        raise ValueError("refusing to open SSH to the whole internet")
    return str(net)
