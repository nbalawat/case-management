"""Server-side request forgery guard for webhook addresses.

A webhook URL is chosen by a caller, so the platform must never be talked into calling something inside its own
network (a metadata service, a database, the admin port of a neighbour). Addresses are checked when an endpoint is
registered and again at every delivery, against the address actually connected to, so a DNS name that later
resolves somewhere private (DNS rebinding) is refused too. Redirects are never followed.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from ...config import settings

MAX_URL = 500
BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata", "instance-data"}


class UnsafeAddress(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    path: str
    ip: str


def _public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
                or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10")))  # carrier-grade NAT


def check_url(url: str) -> str:
    """The URL, if it is acceptable as a webhook address in form. Says why not otherwise."""
    if not isinstance(url, str) or len(url) > MAX_URL:
        raise UnsafeAddress(f"The address must be at most {MAX_URL} characters")
    u = urlsplit(url.strip())
    allowed = ("https", "http") if settings.webhook_allow_private else ("https",)
    if u.scheme not in allowed:
        raise UnsafeAddress("Webhook addresses must use https")
    if not u.hostname or u.username or u.password:
        raise UnsafeAddress("The address needs a host name and must not carry credentials")
    if u.fragment:
        raise UnsafeAddress("The address must not have a fragment")
    host = u.hostname.lower().rstrip(".")
    if not settings.webhook_allow_private:
        if host in BLOCKED_HOSTS or host.endswith((".local", ".internal", ".localhost")):
            raise UnsafeAddress("Webhook addresses must be public")
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None  # a name; resolved and checked at delivery
        if literal is not None and not _public(literal):
            raise UnsafeAddress("Webhook addresses must be public")
    if u.port is not None and not (1 <= u.port <= 65535):
        raise UnsafeAddress("Bad port")
    return url.strip()


def resolve(url: str) -> Target:
    """Resolve the host once and check every address it gives; the delivery connects to the checked one."""
    check_url(url)
    u = urlsplit(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UnsafeAddress(f"Cannot resolve {u.hostname}") from None
    ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    if not ips:
        raise UnsafeAddress(f"Cannot resolve {u.hostname}")
    if not settings.webhook_allow_private and not all(_public(ip) for ip in ips):
        raise UnsafeAddress(f"{u.hostname} resolves to a private address")
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return Target(u.scheme, u.hostname, port, path, str(ips[0]))


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, target: Target, timeout: float):
        super().__init__(target.host, target.port, timeout=timeout, context=ssl.create_default_context())
        self._ip = target.ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # the certificate must still match the name


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, target: Target, timeout: float):
        super().__init__(target.host, target.port, timeout=timeout)
        self._ip = target.ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def post(url: str, body: bytes, headers: dict[str, str], timeout: float = 10.0) -> int:
    """POST to a checked address and return the status. The response body is read only up to 4 KB and ignored."""
    t = resolve(url)
    conn = (_PinnedHTTPS if t.scheme == "https" else _PinnedHTTP)(t, timeout)
    try:
        conn.request("POST", t.path, body=body, headers=headers)
        resp = conn.getresponse()
        resp.read(4096)
        return resp.status
    finally:
        conn.close()
