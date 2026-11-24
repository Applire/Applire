# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 cl. 8 under ``allowance`` (adv/plans F2, re-probe N2/N3): the usage
counter fails closed while it cannot count, recovers on its own when it can
again, and never loses a row while the process lives.

* A model call only queues its row; ONE writer task per process writes the
  queue. The caller never takes a second database connection (under a burst
  every caller held one connection and waited for a second: the pool ran dry).
* A row that cannot be written now is kept and written after recovery (it used
  to be dropped: instance-paid work the daily cap never saw).
* The writer pauses after three consecutive failures; admission is refused while
  paused. After the cool-down ONE try is made (the queue's head, or a probe when
  the queue is empty); one success resumes, a failed try doubles the cool-down
  (capped). Admission opens only after a write or probe has actually succeeded
  (security review of the first fix: admission re-opened on the clock alone).
* The daily cap counts queued tokens; a long backlog refuses admission; a full
  queue drops its oldest row with an ERROR.
"""

from __future__ import annotations

import contextlib
import logging
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
        self.commits = 0
        self.attempts = 0
        self.added = []
        self.written = []
        self.flushes = 0
        self.rollbacks = 0
        self.reject = None  # a predicate: a row it accepts makes the commit fail

    @contextlib.asynccontextmanager
    async def _session(self):
        self.attempts += 1
        if self.down:
            raise TimeoutError("QueuePool limit of size 5 overflow 10 reached")
        outer = self
        pending = []

        class _Db:
            def add(self, row):
                outer.added.append(row)
                pending.append(row)

            async def flush(self):
                outer.flushes += 1

            async def rollback(self):
                outer.rollbacks += 1
                pending.clear()

            async def commit(self):
                if outer.reject and any(outer.reject(r) for r in pending):
                    raise ValueError("row refused")
                outer.commits += 1
                outer.written.extend(pending)

        yield _Db()

    def __call__(self):
        return self._session()


@pytest.fixture
def sink(monkeypatch):
    clock, sessions = Clock(), Sessions()
    usage.reset_sink_state()
    monkeypatch.setattr(usage, "_autostart", False)  # the test drives the writer
    monkeypatch.setattr(usage, "_sink_clock", clock)
    monkeypatch.setattr("applire.db.session.AsyncSessionLocal", sessions)
    daily = AsyncMock()
    monkeypatch.setattr(allowance, "add_daily_tokens", daily)
    monkeypatch.setattr("applire.models.llm_usage.LlmUsage", lambda **row: row)
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    yield clock, sessions
    usage.reset_sink_state()


def _ok():
    return allowance.usage_counting_ok()


async def _queue_and_fail_three_times(n=3):
    for _ in range(n):
        await usage._persist(dict(ROW))
    for _ in range(3):
        assert await usage._step() is False


def _written_rows(sessions):
    return [r for r in sessions.written if r.get("stage") != "sink_probe"]


# ── a model call never waits for the database ───────────────────────────────


@pytest.mark.asyncio
async def test_a_model_calls_row_is_queued_without_opening_a_session(sink):
    """N3: the call that recorded the row holds its own connection; it must not
    ask the pool for a second one."""
    _clock, sessions = sink
    sessions.down = False
    await usage._persist(dict(ROW))
    assert sessions.attempts == 0 and len(usage._queue) == 1
    assert await usage._step() is True
    assert len(_written_rows(sessions)) == 1 and not usage._queue


@pytest.mark.asyncio
async def test_a_burst_is_written_in_one_transaction(sink):
    _clock, sessions = sink
    sessions.down = False
    for _ in range(50):
        await usage._persist(dict(ROW))
    assert await usage._step() is True
    assert sessions.attempts == 1 and sessions.commits == 1 and len(_written_rows(sessions)) == 50


# ── no row is lost ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rows_that_failed_to_write_are_written_after_recovery(sink):
    """N2: 20 parallel analyses lost 8 of 40 rows under pool pressure."""
    clock, sessions = sink
    await _queue_and_fail_three_times(n=5)
    assert _ok() is False and len(usage._queue) == 5
    for _ in range(4):  # rows of work admitted before the pause keep coming
        await usage._persist(dict(ROW))
    sessions.down = False
    clock.now += 31
    assert await usage._step() is True  # the one try: a single row (the head)
    assert _ok() is True
    while usage._queue:
        assert await usage._step() is True
    assert len(_written_rows(sessions)) == 9


@pytest.mark.asyncio
async def test_drain_writes_everything_queued(sink):
    _clock, sessions = sink
    sessions.down = False
    for _ in range(450):
        await usage._persist(dict(ROW))
    assert await usage.drain(timeout=5) == 0
    assert len(_written_rows(sessions)) == 450


# ── fail closed, recover after a success only (security review) ──────────────


@pytest.mark.asyncio
async def test_after_the_cool_down_admission_stays_closed_while_the_sink_is_still_down(sink):
    """The review's case: the cool-down alone never re-opens admission."""
    clock, sessions = sink
    assert _ok() is True
    await _queue_and_fail_three_times()
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
    assert await usage._step() is False
    assert sessions.attempts == attempts
    # Cool-down over, sink STILL down: still refused; the one try fails.
    clock.now += 2
    assert _ok() is False
    assert await usage._step() is False
    assert sessions.attempts == attempts + 2  # the head, then a probe (is it the row?)
    assert _ok() is False and usage._sink_cooldown == 60  # paused again, longer
    assert sessions.commits == 0 and len(usage._queue) == 4  # nothing counted, nothing lost


@pytest.mark.asyncio
async def test_with_an_empty_queue_one_probe_opens_admission_and_counts_nothing(sink):
    clock, sessions = sink
    await _queue_and_fail_three_times()
    usage._queue.clear()  # (only a probe can tell now)
    sessions.down = False
    clock.now += 31
    attempts = sessions.attempts
    assert _ok() is False  # the request that finds the cool-down over is still refused
    assert await usage._step() is True
    assert sessions.attempts == attempts + 1  # exactly one probe
    assert sessions.flushes == 1 and sessions.rollbacks == 1 and sessions.commits == 0  # rolled back
    assert _ok() is True and usage._sink_disabled is False and usage._sink_cooldown == 30
    assert allowance.decide(
        allowance.PayerFacts(owner_id=None, mode="instance", counting_ok=_ok()), "model_work"
    ).payer == "instance"


@pytest.mark.asyncio
async def test_while_paused_admission_checks_and_new_rows_try_nothing(sink):
    """One writer: admission checks and new rows never reach the database
    themselves, so the half-open state has exactly one try in flight."""
    clock, sessions = sink
    await _queue_and_fail_three_times()
    sessions.down = False
    clock.now += 31
    attempts = sessions.attempts
    assert [_ok() for _ in range(5)] == [False] * 5
    for _ in range(3):
        await usage._persist(dict(ROW))
    assert sessions.attempts == attempts
    assert await usage._step() is True
    assert sessions.attempts == attempts + 1 and _ok() is True


@pytest.mark.asyncio
async def test_a_failed_retry_pauses_again_with_a_longer_cool_down(sink):
    clock, sessions = sink
    await _queue_and_fail_three_times()
    assert usage._sink_cooldown == 30
    clock.now += 31
    assert await usage._step() is False  # the retry fails: paused again at once
    assert usage._sink_cooldown == 60
    clock.now += 59
    attempts = sessions.attempts
    assert _ok() is False
    assert await usage._step() is False
    assert sessions.attempts == attempts  # still cooling down: no try
    for _ in range(6):  # failed tries never pause for longer than the cap
        clock.now += usage._sink_cooldown + 1
        assert _ok() is False
        await usage._step()
    assert usage._sink_cooldown == usage._SINK_COOLDOWN_MAX == 300
    sessions.down = False
    clock.now += 301
    assert _ok() is False
    assert await usage._step() is True
    assert _ok() is True and usage._sink_cooldown == 30


@pytest.mark.asyncio
async def test_one_or_two_failures_do_not_pause_the_counter(sink):
    _clock, sessions = sink
    await usage._persist(dict(ROW))
    assert await usage._step() is False
    assert await usage._step() is False
    assert usage.sink_available() is True and _ok() is True
    sessions.down = False
    assert await usage._step() is True
    assert usage._sink_failures == 0 and len(_written_rows(sessions)) == 1


# ── bounded ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_daily_cap_counts_queued_tokens(sink, async_db, monkeypatch):
    """A backlog never under-counts the cap: queued instance-paid tokens count;
    a user-paid row does not."""
    monkeypatch.setattr(settings, "allowance_tokens_per_day", 100)
    assert await allowance.day_cap_reached(async_db) is False
    for _ in range(9):
        await usage._persist({"payer": "instance", "total_tokens": 10})
    await usage._persist({"payer": "user", "total_tokens": 500})
    assert usage.pending_instance_tokens() == 90
    assert await allowance.day_cap_reached(async_db) is False
    await usage._persist({"payer": "system", "total_tokens": 10})
    assert await allowance.day_cap_reached(async_db) is True


@pytest.mark.asyncio
async def test_a_long_backlog_refuses_admission(sink, monkeypatch):
    monkeypatch.setattr(usage, "_QUEUE_HIGH", 5)
    for _ in range(4):
        await usage._persist(dict(ROW))
    assert _ok() is True
    await usage._persist(dict(ROW))
    assert _ok() is False and usage._sink_disabled is False  # not a failure, a backlog
    _clock, sessions = sink
    sessions.down = False
    assert await usage._step() is True
    assert _ok() is True


@pytest.mark.asyncio
async def test_a_full_queue_drops_its_oldest_row_with_an_error(sink, monkeypatch, caplog):
    monkeypatch.setattr(usage, "_QUEUE_MAX", 3)
    for n in range(5):
        await usage._persist({"payer": "instance", "total_tokens": n})
    assert [r["total_tokens"] for r in usage._queue] == [2, 3, 4]
    assert usage._dropped == 2
    assert any(r.levelno == logging.ERROR and "backlog full" in r.getMessage() for r in caplog.records)


# ── a row that can never be written does not stop the counter ────────────────


@pytest.mark.asyncio
async def test_a_row_of_an_erased_account_is_written_unattributed(sink):
    """Its account was erased while the row was queued: the foreign key would
    have set it to NULL; the tokens still count."""
    import uuid

    _clock, sessions = sink
    sessions.down = False
    gone = uuid.uuid4()
    sessions.reject = lambda r: r.get("user_id") == gone
    await usage._persist({**ROW, "user_id": gone})
    await usage._persist(dict(ROW))
    assert await usage._step() is False  # the batch fails
    assert await usage._step() is True  # the head alone fails, a probe succeeds
    assert usage._queue[0].get("user_id") is None
    assert await usage.drain(timeout=5) == 0
    assert [r.get("user_id") for r in _written_rows(sessions)] == [None, None]
    assert _ok() is True and usage._sink_failures == 0


@pytest.mark.asyncio
async def test_a_malformed_row_is_dropped_and_the_rest_is_written(sink, caplog):
    _clock, sessions = sink
    sessions.down = False
    sessions.reject = lambda r: r.get("total_tokens") == -1
    await usage._persist({"payer": "instance", "total_tokens": -1})
    for _ in range(3):
        await usage._persist(dict(ROW))
    assert await usage.drain(timeout=5) == 0
    assert len(_written_rows(sessions)) == 3 and _ok() is True
    assert any("dropped" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


@pytest.mark.asyncio
async def test_a_bad_row_while_paused_does_not_keep_the_counter_shut(sink):
    clock, sessions = sink
    sessions.reject = lambda r: r.get("total_tokens") == -1
    await usage._persist({"payer": "instance", "total_tokens": -1})
    for _ in range(3):
        assert await usage._step() is False
    assert _ok() is False
    sessions.down = False
    clock.now += 31
    assert await usage._step() is True  # the head fails, the probe succeeds: the row goes
    assert _ok() is True and not usage._queue


# ── the writer task ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_writer_starts_on_its_own_and_writes_the_rows(sink, monkeypatch):
    _clock, sessions = sink
    sessions.down = False
    monkeypatch.setattr(usage, "_autostart", True)
    for _ in range(7):
        await usage._persist(dict(ROW))
    assert usage._writer is not None
    await usage._writer
    assert len(_written_rows(sessions)) == 7 and not usage._queue


@pytest.mark.asyncio
async def test_a_paused_writer_probes_after_the_cool_down_on_its_own(sink, monkeypatch):
    clock, sessions = sink
    monkeypatch.setattr(usage, "_autostart", True)
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock.now += seconds
        if len(slept) > 3:
            sessions.down = False

    monkeypatch.setattr(usage, "_sleep", fake_sleep)
    await usage._persist(dict(ROW))
    await usage._writer
    assert _ok() is True and len(_written_rows(sessions)) == 1 and slept
