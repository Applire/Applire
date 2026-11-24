# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 cl. 8 under ``allowance`` (adv/plans F2): the usage counter fails
closed while it cannot count, and recovers on its own when it can again.

The sink pauses after three consecutive write failures. The pause used to be
permanent until a restart, which turned one tester's burst (a pool timeout) into
a refusal of every instance-paid action of every person. Now the pause is
half-open: after the cool-down the next write tries again; one success resumes,
a failed retry doubles the cool-down (capped).
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock

import pytest

from applire.config import settings
from applire.providers.llm import usage
from applire.services import allowance

ROW = {"payer": "instance", "total_tokens": 10}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Sessions:
    """``AsyncSessionLocal`` stand-in: fails while ``down``, writes otherwise."""

    def __init__(self) -> None:
        self.down = True
        self.writes = 0
        self.attempts = 0

    @contextlib.asynccontextmanager
    async def _session(self):
        self.attempts += 1
        if self.down:
            raise TimeoutError("QueuePool limit of size 5 overflow 10 reached")
        outer = self

        class _Db:
            def add(self, row):
                pass

            async def commit(self):
                outer.writes += 1

        yield _Db()

    def __call__(self):
        return self._session()


@pytest.fixture
def sink(monkeypatch):
    clock, sessions = Clock(), Sessions()
    usage.reset_sink_state()
    monkeypatch.setattr(usage, "_sink_clock", clock)
    monkeypatch.setattr("applire.db.session.AsyncSessionLocal", sessions)
    monkeypatch.setattr(allowance, "add_daily_tokens", AsyncMock())
    monkeypatch.setattr("applire.models.llm_usage.LlmUsage", lambda **row: row)
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    yield clock, sessions
    usage.reset_sink_state()


async def _fail_three_times():
    for _ in range(3):
        await usage._persist(dict(ROW))


@pytest.mark.asyncio
async def test_an_outage_fails_closed_then_the_counter_recovers_on_its_own(sink):
    clock, sessions = sink
    assert allowance.usage_counting_ok() is True
    await _fail_three_times()
    # During the outage: the counter cannot count → instance-paid work refused.
    assert usage.sink_available() is False and allowance.usage_counting_ok() is False
    facts = allowance.PayerFacts(owner_id=None, mode="instance", counting_ok=allowance.usage_counting_ok())
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(facts, "model_work")
    assert exc.value.code == "allowance_unavailable" and exc.value.status_code == 503
    # Still inside the cool-down: no write is even tried.
    clock.now += 29
    attempts = sessions.attempts
    await usage._persist(dict(ROW))
    assert sessions.attempts == attempts and allowance.usage_counting_ok() is False
    # The database is back; after the cool-down the next write tries and succeeds.
    sessions.down = False
    clock.now += 2
    assert allowance.usage_counting_ok() is True  # half-open: admission may try again
    await usage._persist(dict(ROW))
    assert sessions.writes == 1
    assert usage._sink_disabled is False and usage._sink_failures == 0
    assert allowance.decide(
        allowance.PayerFacts(owner_id=None, mode="instance", counting_ok=allowance.usage_counting_ok()),
        "model_work",
    ).payer == "instance"


@pytest.mark.asyncio
async def test_a_failed_retry_pauses_again_with_a_longer_cool_down(sink):
    clock, sessions = sink
    await _fail_three_times()
    assert usage._sink_cooldown == 30
    clock.now += 31
    await usage._persist(dict(ROW))  # the retry fails: paused again at once
    assert usage.sink_available() is False and usage._sink_cooldown == 60
    clock.now += 59
    assert usage.sink_available() is False
    clock.now += 2
    assert usage.sink_available() is True
    for _ in range(6):  # failed retries never pause for longer than the cap
        clock.now += usage._sink_cooldown + 1
        await usage._persist(dict(ROW))
    assert usage._sink_cooldown == usage._SINK_COOLDOWN_MAX == 300
    sessions.down = False
    clock.now += 301
    await usage._persist(dict(ROW))
    assert usage._sink_disabled is False and usage._sink_cooldown == 30


@pytest.mark.asyncio
async def test_one_or_two_failures_do_not_pause_the_counter(sink):
    _clock, sessions = sink
    await usage._persist(dict(ROW))
    await usage._persist(dict(ROW))
    assert usage.sink_available() is True and allowance.usage_counting_ok() is True
    sessions.down = False
    await usage._persist(dict(ROW))
    assert usage._sink_failures == 0
