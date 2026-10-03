# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local password hashing and policy (ADR-091 cl. 5; RD-8; SF-IAM.6, SF-IAM.12)."""

import asyncio
import hashlib

import pytest

from applire.auth import passwords
from applire.auth.passwords import (
    check_password_policy,
    hash_password,
    needs_rehash,
    verify_password,
)


@pytest.mark.asyncio
async def test_hash_format_and_round_trip():
    stored = await hash_password("correct horse battery staple")
    scheme, log2n, r, p, salt, digest = stored.split("$")
    assert (scheme, log2n, r, p) == ("scrypt", "15", "8", "1")
    assert await verify_password("correct horse battery staple", stored) is True
    assert await verify_password("correct horse battery stapl", stored) is False
    assert stored != await hash_password("correct horse battery staple")  # salted


def test_the_parameters_need_the_explicit_maxmem():
    """n=2^15, r=8 is rejected by the stdlib default maxmem — why cl. 5 names 64 MiB."""
    with pytest.raises(ValueError):
        hashlib.scrypt(b"x", salt=b"s" * 16, n=2**15, r=8, p=1, dklen=32)
    assert passwords.SCRYPT_MAXMEM == 64 * 1024 * 1024


@pytest.mark.asyncio
async def test_no_stored_hash_still_spends_a_hash_and_is_false(monkeypatch):
    calls = []
    real = passwords._verify_sync

    def spy(pw, stored):
        calls.append(stored)
        return real(pw, stored)

    monkeypatch.setattr(passwords, "_verify_sync", spy)
    assert await verify_password("anything at all", None) is False
    assert calls == [passwords._DUMMY_HASH]
    assert passwords._parse(passwords._DUMMY_HASH)[:3] == (15, 8, 1)  # same cost as real


@pytest.mark.asyncio
async def test_a_malformed_stored_value_is_false():
    assert await verify_password("x" * 12, "md5$abc") is False
    assert await verify_password("x" * 12, "scrypt$15$8$1$!!$!!") is False


@pytest.mark.asyncio
async def test_weaker_parameters_ask_for_a_rehash():
    stored = await hash_password("correct horse battery staple")
    assert needs_rehash(stored) is False
    weak = stored.replace("scrypt$15$", "scrypt$14$", 1)
    assert needs_rehash(weak) is True
    assert needs_rehash("garbage") is True


@pytest.mark.parametrize(
    "password,email,ok",
    [
        ("a" * 11, "x@example.org", False),
        ("a" * 12, "x@example.org", True),
        ("a" * 256, "x@example.org", True),
        ("a" * 257, "x@example.org", False),
        ("longer@example.org", "Longer@Example.org", False),
        ("no composition rules at all", "x@example.org", True),
    ],
)
def test_policy(password, email, ok):
    if ok:
        check_password_policy(password, email)
    else:
        with pytest.raises(ValueError):
            check_password_policy(password, email)


@pytest.mark.asyncio
async def test_at_most_four_hashes_run_at_once(monkeypatch):
    """SF-IAM.12: the global semaphore bounds concurrent scrypt work."""
    active = 0
    peak = 0
    import threading
    import time

    lock = threading.Lock()

    def slow(pw):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return "scrypt$15$8$1$AA==$AA=="

    monkeypatch.setattr(passwords, "_hash_sync", slow)
    await asyncio.gather(*(hash_password("p" * 12) for _ in range(12)))
    assert peak == passwords.HASH_CONCURRENCY == 4
