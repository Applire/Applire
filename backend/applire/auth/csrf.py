# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Origin check for unsafe requests (ADR-091 cl. 12, revision 3; SF-IAM.5).

An unsafe request (POST/PUT/PATCH/DELETE) must carry ``Origin`` or ``Referer``;
neither, or ``Origin: null``, is 403 ``origin_mismatch``. The netloc (host
**and** port, default ports normalised) must equal the request ``Host`` — or
the netloc of ``APPLIRE_BASE_URL`` when that is set and not the shipped default
``http://localhost:8001``. With such a base URL set, an unsafe request whose
``Host`` is neither that netloc nor ``localhost``/``127.0.0.1`` is refused too
(DNS rebinding against ``/api/setup``).

Where it runs:

* ``require_origin`` — a dependency on every **public** unsafe route (login,
  setup, link redeem/inspect, forgot); the route-auth inventory asserts it.
* ``applire.auth.deps`` — the five auth dependencies call ``check_origin``
  for every unsafe request that carries a ``Cookie`` and no ``Authorization``
  header (a cookie is the ambient credential CSRF abuses). A request with an
  ``Authorization`` header never has its cookie read (cl. 12): an invalid or
  wrong-scope bearer is 401, so only a **valid** ``api`` bearer gets through
  without an origin — the exemption the ADR grants.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from applire.config import settings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SHIPPED_DEFAULT_BASE_URL = "http://localhost:8001"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
_DEFAULT_PORTS = {"http": "80", "https": "443"}

ORIGIN_MISMATCH_MESSAGE = (
    "This request did not come from the address Applire is served on. "
    "Your reverse proxy must forward the Host header, or set APPLIRE_BASE_URL "
    "to the address you open."
)


def origin_mismatch(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error_code": "origin_mismatch",
            "message": f"{reason} {ORIGIN_MISMATCH_MESSAGE}",
        },
    )


def _norm_netloc(netloc: str, scheme: str | None) -> str:
    """Lower-case, and drop a port that is the scheme's default."""
    netloc = netloc.strip().lower()
    if scheme and ":" in netloc and not netloc.endswith("]"):
        host, _, port = netloc.rpartition(":")
        if _DEFAULT_PORTS.get(scheme) == port:
            return host
    return netloc


def _hostname(netloc: str) -> str:
    if netloc.startswith("["):
        return netloc.split("]")[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc


def configured_base_netloc() -> tuple[str, str] | None:
    """``(scheme, netloc)`` of ``APPLIRE_BASE_URL`` when set and not the shipped default."""
    base = (settings.applire_base_url or "").strip().rstrip("/")
    if not base or base == SHIPPED_DEFAULT_BASE_URL:
        return None
    parts = urlsplit(base)
    if not parts.scheme or not parts.netloc:
        return None
    return parts.scheme.lower(), _norm_netloc(parts.netloc, parts.scheme.lower())


def check_origin(request: Request) -> None:
    """Raise 403 ``origin_mismatch`` unless an unsafe request is same-origin.

    Safe methods return at once.
    """
    if request.method.upper() not in UNSAFE_METHODS:
        return
    host_header = request.headers.get("host", "")
    base = configured_base_netloc()

    if base is not None:
        # Anti-rebinding Host allow-list.
        host_n = host_header.strip().lower()
        if host_n != base[1] and _norm_netloc(host_n, base[0]) != base[1] and (
            _hostname(host_n) not in _LOOPBACK_HOSTS
        ):
            raise origin_mismatch(f"Host '{host_header}' is not the configured address.")

    origin = request.headers.get("origin")
    source = origin
    if origin is None:
        source = request.headers.get("referer")
        if not source:
            raise origin_mismatch("The request carried neither Origin nor Referer.")
    if source.strip().lower() == "null":
        raise origin_mismatch("The request came from an opaque origin (Origin: null).")
    parts = urlsplit(source.strip())
    if not parts.scheme or not parts.netloc:
        raise origin_mismatch("The request's Origin could not be read.")
    scheme = parts.scheme.lower()
    origin_netloc = _norm_netloc(parts.netloc, scheme)

    allowed = {_norm_netloc(host_header, scheme)} if host_header else set()
    if base is not None:
        allowed.add(base[1])
    if origin_netloc not in allowed:
        raise origin_mismatch(
            f"Origin '{scheme}://{origin_netloc}' does not match Host '{host_header}'."
        )


def needs_cookie_csrf_check(request: Request) -> bool:
    """True for an unsafe request whose credential could be an ambient cookie."""
    return (
        request.method.upper() in UNSAFE_METHODS
        and "authorization" not in request.headers
        and bool(request.headers.get("cookie"))
    )


async def require_origin(request: Request) -> None:
    """Dependency for public unsafe routes (login, setup, link redeem, forgot)."""
    check_origin(request)
