# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 cl. 8 under ``allowance`` (adv/plans F2): the usage counter fails
closed while it cannot count, and recovers on its own when it can again.

The sink pauses after three consecutive write failures. The pause used to be
permanent until a restart, which turned one tester's burst (a pool timeout) into
a refusal of every instance-paid action of every person. Now the pause is
half-open: after the cool-down ONE write is tried (a probe, or a write of work
admitted before the pause); one success resumes, a failed try doubles the
cool-down (capped).

Admission opens only after a write has actually succeeded (security review of
the first fix: admission re-opened when the cool-down ran out, so with the sink
still broken instance-paid work ran uncounted and the daily cap did not bind).
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
        self.added = []
        self.flushes = 0
        self.rollbacks = 0
        self.gate = None  # an asyncio.Event: hold an attempt open until set

    @contextlib.asynccontextmanager
    async def _session(self):
        self.attempts += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.down:
            raise TimeoutError("QueuePool limit of size 5 overflow 10 reached")
        outer = self

        class _Db:
            def add(self, row):
                outer.added.append(row)

            async def flush(self):
                outer.flushes += 1

            async def rollback(self):
                outer.rollbacks += 1

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


def _ok():
    return allowance.usage_counting_ok()


async def _probe_done():
    task = usage._probe_task
    if task is not None:
        await task


@pytest.mark.asyncio
async def test_after_the_cool_down_admission_stays_closed_while_the_sink_is_still_down(sink):
    """The review's case: the cool-down alone never re-opens admission."""
    clock, sessions = sink
    assert _ok() is True
    await _fail_three_times()
    assert _ok() is False
    facts = allowance.PayerFacts(owner_id=None, mode="instance", counting_ok=_ok())
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(facts, "model_work")
    assert exc.value.code == "allowance_unavailable" and exc.value.status_code == 503
    # Inside the cool-down: nothing is tried at all.
    clock.now += 29
    attempts = sessions.attempts
    assert _ok() is False
    await usage._persist(dict(ROW))
    await _probe_done()
    assert sessions.attempts == attempts
    # Cool-down over, sink STILL down: admission still refuses; one probe runs and fails.
    clock.now += 2
    assert _ok() is False
    await _probe_done()
    assert sessions.attempts == attempts + 1
    assert _ok() is False and usage._sink_cooldown == 60  # paused again, longer
    assert sessions.writes == 0  # nothing was counted, nothing admitted


@pytest.mark.asyncio
async def test_when_the_sink_is_back_one_probe_opens_admission_and_counts_nothing(sink):
    clock, sessions = sink
    await _fail_three_times()
    sessions.down = False
    clock.now += 31
    attempts = sessions.attempts
    assert _ok() is False  # the request that finds the cool-down over is still refused
    await _probe_done()
    assert sessions.attempts == attempts + 1  # exactly one probe
    assert sessions.flushes == 1 and sessions.rollbacks == 1 and sessions.writes == 0  # rolled back
    assert _ok() is True and usage._sink_disabled is False and usage._sink_cooldown == 30
    assert allowance.decide(
        allowance.PayerFacts(owner_id=None, mode="instance", counting_ok=_ok()), "model_work"
    ).payer == "instance"


@pytest.mark.asyncio
async def test_half_open_allows_one_try_in_flight(sink):
    """Many admissions and writes of earlier-admitted work during the half-open
    state: one try reaches the database; the rest stay closed / are dropped."""
    import asyncio

    clock, sessions = sink
    await _fail_three_times()
    sessions.down = False
    sessions.gate = asyncio.Event()
    clock.now += 31
    attempts = sessions.attempts
    writes = []
    try:
        assert [_ok() for _ in range(5)] == [False] * 5
        writes = [asyncio.ensure_future(usage._persist(dict(ROW))) for _ in range(3)]
        await asyncio.sleep(0)
        assert sessions.attempts == attempts + 1  # only the probe is in flight
    finally:  # a failing assertion must not leave the probe parked on the gate
        sessions.gate.set()
        await _probe_done()
        await asyncio.gather(*writes)
    assert sessions.attempts == attempts + 1 and sessions.writes == 0
    assert _ok() is True


@pytest.mark.asyncio
async def test_a_write_of_earlier_admitted_work_may_be_the_one_try(sink):
    clock, sessions = sink
    await _fail_three_times()
    sessions.down = False
    clock.now += 31
    await usage._persist(dict(ROW))  # no admission check came first
    assert sessions.writes == 1  # the row is counted
    assert _ok() is True


@pytest.mark.asyncio
async def test_a_failed_retry_pauses_again_with_a_longer_cool_down(sink):
    clock, sessions = sink
    await _fail_three_times()
    assert usage._sink_cooldown == 30
    clock.now += 31
    await usage._persist(dict(ROW))  # the retry fails: paused again at once
    assert usage._sink_cooldown == 60
    clock.now += 59
    attempts = sessions.attempts
    assert _ok() is False
    await _probe_done()
    assert sessions.attempts == attempts  # still cooling down: no probe
    for _ in range(6):  # failed tries never pause for longer than the cap
        clock.now += usage._sink_cooldown + 1
        assert _ok() is False
        await _probe_done()
    assert usage._sink_cooldown == usage._SINK_COOLDOWN_MAX == 300
    sessions.down = False
    clock.now += 301
    assert _ok() is False
    await _probe_done()
    assert _ok() is True and usage._sink_cooldown == 30


@pytest.mark.asyncio
async def test_one_or_two_failures_do_not_pause_the_counter(sink):
    _clock, sessions = sink
    await usage._persist(dict(ROW))
    await usage._persist(dict(ROW))
    assert usage.sink_available() is True and _ok() is True
    sessions.down = False
    await usage._persist(dict(ROW))
    assert usage._sink_failures == 0
