# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local password hashing and policy (ADR-091 cl. 5; D-7, RD-8).

``hashlib.scrypt`` (stdlib — no native wheel), n = 2^15, r = 8, p = 1, dklen 32,
16-byte random salt, **explicit** ``maxmem`` of 64 MiB (the stdlib default of
32 MiB rejects n = 2^15 · r = 8, which needs 128·n·r = 32 MiB plus overhead).
Stored as ``scrypt$<log2 n>$<r>$<p>$<salt b64>$<hash b64>``; a login that
verifies a hash with weaker parameters re-hashes it (``needs_rehash``).

Every hash runs in ``asyncio.to_thread`` under a **global semaphore of 4** so a
burst of logins cannot exhaust a small NAS (32 MiB per hash) or block the event
loop (SF-IAM.12).

``verify_password(raw, None)`` hashes against a dummy of identical parameters
and returns False: an unknown email, a pending invite and an OIDC-only account
cost the same time as a wrong password (SF-IAM.6, RD-8).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os

from applire.schemas.auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

SCRYPT_LOG2_N = 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
SCRYPT_MAXMEM = 64 * 1024 * 1024
HASH_CONCURRENCY = 4

_SCHEME = "scrypt"

_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _sem() -> asyncio.Semaphore:
    # One semaphore per running loop (tests run many loops; production one).
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(HASH_CONCURRENCY)
        _semaphore_loop = loop
    return _semaphore


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _scrypt(password: str, salt: bytes, log2_n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**log2_n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=SCRYPT_MAXMEM,
    )


def _hash_sync(password: str) -> str:
    salt = os.urandom(SCRYPT_SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN)
    return f"{_SCHEME}${SCRYPT_LOG2_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


#: Same parameters as a real hash, so verifying against it costs the same.
_DUMMY_HASH = _hash_sync("applire-dummy-password-never-valid")


def _parse(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _SCHEME:
        return None
    try:
        log2_n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, digest = _unb64(parts[4]), _unb64(parts[5])
    except (ValueError, TypeError):
        return None
    if not (1 <= log2_n <= 20 and 1 <= r <= 32 and 1 <= p <= 16 and digest):
        return None
    return log2_n, r, p, salt, digest


def _verify_sync(password: str, stored: str) -> bool:
    parsed = _parse(stored)
    if parsed is None:
        # Malformed stored value: still spend a hash so timing does not tell.
        _verify_sync(password, _DUMMY_HASH)
        return False
    log2_n, r, p, salt, digest = parsed
    candidate = _scrypt(password, salt, log2_n, r, p, len(digest))
    return hmac.compare_digest(candidate, digest)


async def hash_password(raw: str) -> str:
    """Hash ``raw`` for storage (policy is the caller's job: ``check_password_policy``)."""
    async with _sem():
        return await asyncio.to_thread(_hash_sync, raw)


async def verify_password(raw: str, stored: str | None) -> bool:
    """True iff ``raw`` matches ``stored``. ``None`` → dummy hash, always False."""
    async with _sem():
        if stored is None:
            await asyncio.to_thread(_verify_sync, raw, _DUMMY_HASH)
            return False
        return await asyncio.to_thread(_verify_sync, raw, stored)


def needs_rehash(stored: str) -> bool:
    """True when ``stored`` was made with weaker parameters than today's."""
    parsed = _parse(stored)
    if parsed is None:
        return True
    log2_n, r, p, _salt, digest = parsed
    return (log2_n, r, p) < (SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P) or len(digest) < SCRYPT_DKLEN


def check_password_policy(password: str, email: str) -> None:
    """RD-8: 12–256 characters, no composition rules, not equal to the email.

    Raises ``ValueError`` with a short English reason; routes map it to
    422 ``password_policy``.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"The password needs at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"The password can have at most {PASSWORD_MAX_LENGTH} characters.")
    if email and password.strip().lower() == email.strip().lower():
        raise ValueError("The password cannot be the account's email address.")
