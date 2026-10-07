# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""One safe outbound fetcher (ADR-092 cl. 15, RD-7, SF-SCRAPER.2 / SF-SCRAPER.6).

Every server-side fetch of a URL that a user (or a posting a user supplied) can
steer goes through :func:`safe_get`. Per hop it

1. accepts only ``http``/``https`` with a host;
2. **resolves the host itself** (``getaddrinfo``) and refuses the hop when ANY
   resolved address is not globally routable — loopback, RFC 1918 / ULA private,
   link-local (incl. the cloud metadata address ``169.254.169.254`` and
   ``fe80::/10``), CGNAT ``100.64.0.0/10``, multicast, unspecified, reserved,
   documentation ranges — including those ranges hidden inside IPv6
   (IPv4-mapped ``::ffff:a.b.c.d``, 6to4, Teredo, NAT64 ``64:ff9b::/96``);
3. **connects to the address it checked** (the request URL carries the IP
   literal, ``Host`` and TLS SNI carry the name, so the certificate is still
   verified against the name) — a second DNS answer (rebinding) is never used;
4. follows redirects **manually**, re-running 1–3 on every ``Location``.

A refused URL or hop raises :class:`UnsafeFetchRefused`; callers (scraper, colour
detection) catch that name. Environment proxies are ignored (``trust_env=False``):
a proxy would resolve the name again and defeat step 3. No operator escape hatch
in Strawberry (ADR-092 cl. 15 Negative: intranet job boards are not fetched).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Mapping
from urllib.parse import urljoin, urlsplit

import httpx

__all__ = ["UnsafeFetchRefused", "check_address", "resolve_checked", "safe_get"]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

#: Test seam: an ``httpx`` transport used instead of the network (unit tests).
_TRANSPORT: httpx.AsyncBaseTransport | None = None


class UnsafeFetchRefused(Exception):
    """The URL (or a redirect hop) is not fetchable: wrong scheme, no host, a
    refused address range, or too many redirects."""


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def check_address(address: str) -> None:
    """Raise :class:`UnsafeFetchRefused` unless ``address`` is a public unicast IP."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        raise UnsafeFetchRefused(f"not an IP address: {address!r}") from None
    candidates = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_ipv4(ip)
        if inner is not None:
            candidates.append(inner)
    for cand in candidates:
        if cand.is_multicast or not cand.is_global:
            raise UnsafeFetchRefused(f"refused address range: {cand}")


async def _resolve(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to (test seam — patched in unit tests)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _split(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeFetchRefused(f"only http and https URLs are fetched, got {scheme!r}")
    host = parts.hostname
    if not host:
        raise UnsafeFetchRefused(f"URL has no host: {url!r}")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise UnsafeFetchRefused(f"invalid port in {url!r}") from None
    return scheme, host, port


async def resolve_checked(url: str) -> str:
    """Resolve ``url``'s host and return the ONE checked address to connect to.

    Refuses when the scheme is not http(s), the name does not resolve, or ANY
    resolved address is in a refused range (a mixed public/private answer is a
    rebinding set-up, not a reason to pick the public one).
    """
    _scheme, host, port = _split(url)
    try:
        addresses = await _resolve(host, port)
    except (OSError, UnicodeError) as exc:
        raise UnsafeFetchRefused(f"host does not resolve: {host!r} ({exc})") from None
    if not addresses:
        raise UnsafeFetchRefused(f"host does not resolve: {host!r}")
    for address in addresses:
        check_address(address)
    return addresses[0]


def _pinned_request(url: str, address: str, headers: Mapping[str, str] | None) -> httpx.Request:
    """A GET for ``url`` whose connection goes to ``address`` (name kept in Host + SNI)."""
    scheme, host, port = _split(url)
    parts = urlsplit(url)
    ip_host = f"[{address}]" if ":" in address else address
    default_port = 443 if scheme == "https" else 80
    netloc = ip_host if port == default_port else f"{ip_host}:{port}"
    pinned = parts._replace(netloc=netloc, fragment="").geturl()
    host_header = parts.hostname if port == default_port else f"{parts.hostname}:{port}"
    req_headers = dict(headers or {})
    req_headers["Host"] = host_header  # type: ignore[assignment]
    extensions = {"sni_hostname": host} if scheme == "https" else {}
    return httpx.Request("GET", pinned, headers=req_headers, extensions=extensions)


async def safe_get(
    url: str,
    *,
    timeout: float | httpx.Timeout,
    headers: Mapping[str, str] | None,
    max_redirects: int = 5,
) -> httpx.Response:
    """GET ``url`` and return the final response, every hop checked and pinned.

    The returned response's ``.url`` is the final hop's NAME-based URL (not the
    pinned IP form), so callers resolve relative links as before.
    """
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout,
        trust_env=False,
        transport=_TRANSPORT,
    ) as client:
        for _hop in range(max_redirects + 1):
            address = await resolve_checked(current)
            request = _pinned_request(current, address, headers)
            response = await client.send(request)
            location = response.headers.get("location")
            if response.status_code in _REDIRECT_STATUSES and location:
                await response.aclose()
                current = urljoin(current, location)
                continue
            # Present the response as fetched from the NAME, not the pinned IP.
            response.request = httpx.Request("GET", current, headers=request.headers)
            return response
    raise UnsafeFetchRefused(f"more than {max_redirects} redirects from {url!r}")
