"""SSRF (Server-Side Request Forgery) protection.

Blocks requests to private IP ranges, loopback, link-local, and cloud
metadata endpoints before any outbound HTTP call.

Usage::

    from umabot.security.ssrf import check_ssrf, SSRFError

    try:
        check_ssrf(url)
    except SSRFError as exc:
        return ToolResult(content=f"Blocked: {exc}")
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger("umabot.security.ssrf")

# IPv4 ranges that are always blocked
_BLOCKED_V4 = [
    ipaddress.IPv4Network("127.0.0.0/8"),       # loopback
    ipaddress.IPv4Network("10.0.0.0/8"),         # private
    ipaddress.IPv4Network("172.16.0.0/12"),      # private
    ipaddress.IPv4Network("192.168.0.0/16"),     # private
    ipaddress.IPv4Network("169.254.0.0/16"),     # link-local / cloud metadata (AWS, GCP, Azure)
    ipaddress.IPv4Network("100.64.0.0/10"),      # shared address space (RFC 6598)
    ipaddress.IPv4Network("192.0.2.0/24"),       # TEST-NET-1
    ipaddress.IPv4Network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.IPv4Network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.IPv4Network("0.0.0.0/8"),          # "this" network
]

# IPv6 ranges that are always blocked
_BLOCKED_V6 = [
    ipaddress.IPv6Network("::1/128"),            # loopback
    ipaddress.IPv6Network("fc00::/7"),           # ULA (unique local)
    ipaddress.IPv6Network("fe80::/10"),          # link-local
    ipaddress.IPv6Network("::/128"),             # unspecified
]

# Explicit hostnames that are always blocked regardless of resolution
_BLOCKED_HOSTS = {
    "metadata.google.internal",     # GCP metadata
    "169.254.169.254",              # AWS/Azure/GCP IMDS
    "instance-data",                # some cloud metadata aliases
}


class SSRFError(ValueError):
    """Raised when a URL is blocked by SSRF protection."""


def check_ssrf(url: str) -> None:
    """Raise ``SSRFError`` if the URL targets a private/internal address.

    Resolves the hostname to its IP addresses and checks each against
    the blocked ranges. Raises on the first blocked address found.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise SSRFError(f"Cannot parse host from URL: {url!r}")

    # Block well-known metadata hostnames before DNS resolution
    if host.lower() in _BLOCKED_HOSTS:
        raise SSRFError(f"Blocked: {host} is a cloud metadata endpoint")

    # Resolve and check all addresses
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        logger.debug("SSRF check: DNS resolution failed for %s: %s", host, exc)
        # Fail open on DNS error — don't block legitimate requests that may
        # have transient DNS issues. The request will fail anyway.
        return

    for info in infos:
        addr_str = info[4][0]
        _check_addr(host, addr_str)


def _check_addr(host: str, addr_str: str) -> None:
    """Raise SSRFError if addr_str falls into a blocked range."""
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return  # Not a parseable IP, skip

    # Unwrap IPv4-mapped IPv6 (::ffff:x.x.x.x) to check as IPv4
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped

    if isinstance(addr, ipaddress.IPv4Address):
        for network in _BLOCKED_V4:
            if addr in network:
                raise SSRFError(
                    f"Blocked: {host} resolves to {addr} which is in private range {network}"
                )
    elif isinstance(addr, ipaddress.IPv6Address):
        for network in _BLOCKED_V6:
            if addr in network:
                raise SSRFError(
                    f"Blocked: {host} resolves to {addr} which is in blocked range {network}"
                )
