# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The login/setup throttle (ADR-091 cl. 13; RD-8; SF-IAM.6): delay-only, keyed
on (email, client), 5 free failures in 15 min, then 1 s doubling to 30 s."""

from types import SimpleNamespace

import pytest

from applire.auth.throttle import (
    MAX_DELAY_SECONDS,
    WINDOW_SECONDS,
    Throttle,
    client_of,
    login_key,
    login_throttle,
    setup_throttle,
)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_five_free_failures_then_doubling_to_the_cap():
    clock = Clock()
    th = Throttle(clock=clock)
    key = ("a@example.org", "10.0.0.2")
    delays = []
    for _ in range(12):
        delays.append(th.delay_for(key))
        th.record_failure(key)
        clock.t += 1
    assert delays == [0, 0, 0, 0, 0, 1, 2, 4, 8, 16, 30, 30]
    assert max(delays) == MAX_DELAY_SECONDS


def test_failures_age_out_of_the_window():
    clock = Clock()
    th = Throttle(clock=clock)
    key = ("a", "c")
    for _ in range(6):
        th.record_failure(key)
    assert th.delay_for(key) == 2
    clock.t += WINDOW_SECONDS + 1
    assert th.delay_for(key) == 0
    assert len(th) == 0


def test_success_clears_and_keys_are_independent_per_client():
    th = Throttle(clock=Clock())
    attacker = ("admin@example.org", "10.0.0.66")
    owner = ("admin@example.org", "10.0.0.2")
    for _ in range(10):
        th.record_failure(attacker)
    assert th.delay_for(attacker) > 0
    assert th.delay_for(owner) == 0  # no lockout of the owner from another client
    th.record_success(attacker)
    assert th.delay_for(attacker) == 0


def test_lru_eviction_bounds_memory():
    th = Throttle(clock=Clock(), max_keys=3)
    for i in range(5):
        th.record_failure((str(i), "c"))
    assert len(th) == 3
    assert th.delay_for(("0", "c")) == 0 and th.delay_for(("4", "c")) == 0  # 4 kept, 0 evicted
    assert ("4", "c") in th._entries and ("0", "c") not in th._entries


@pytest.mark.asyncio
async def test_wait_sleeps_the_delay():
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    th = Throttle(clock=Clock(), sleep=fake_sleep)
    key = ("k", "c")
    for _ in range(5):
        assert await th.wait(key) == 0
        th.record_failure(key)
    assert await th.wait(key) == 1
    assert slept == [1]


def _req(headers=None, host="10.0.0.9"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


def test_client_is_x_real_ip_else_the_peer_and_email_is_casefolded():
    assert client_of(_req({"x-real-ip": "192.168.1.5"})) == "192.168.1.5"
    assert client_of(_req()) == "10.0.0.9"
    assert login_key("  Alice@Example.ORG ", _req()) == ("alice@example.org", "10.0.0.9")


def test_login_and_setup_are_separate_buckets():
    assert login_throttle is not setup_throttle
