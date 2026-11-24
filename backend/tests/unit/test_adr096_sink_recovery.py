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
        self.reject_with = None  # the exception the commit raises for a rejected row
        self.users = set()  # account ids that exist
        self.daily_writes = []  # (payer, tokens) added outside a row write

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

            async def get(self, _model, ident):
                return object() if ident in outer.users else None

            async def commit(self):
                if outer.reject and any(outer.reject(r) for r in pending):
                    raise (outer.reject_with or _integrity())
                outer.commits += 1
                outer.written.extend(pending)

        yield _Db()

    def __call__(self):
        return self._session()


def _integrity():
    from sqlalchemy.exc import IntegrityError

    return IntegrityError("INSERT INTO llm_usage", {}, Exception("violates foreign key constraint"))


def _data_error():
    from sqlalchemy.exc import DataError

    return DataError("INSERT INTO llm_usage", {}, Exception("value out of range"))


@pytest.fixture
def sink(monkeypatch):
    clock, sessions = Clock(), Sessions()
    usage.reset_sink_state()
    monkeypatch.setattr(usage, "_autostart", False)  # the test drives the writer
    monkeypatch.setattr(usage, "_sink_clock", clock)
    monkeypatch.setattr("applire.db.session.AsyncSessionLocal", sessions)
    async def daily(db, *, payer, tokens, now=None):  # committed with the session, like the real one
        db.add({"__daily__": True, "payer": payer, "tokens": tokens, "now": now})

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
    return [r for r in sessions.written if r.get("stage") != "sink_probe" and not r.get("__daily__")]


def _daily(sessions):
    """(payer, tokens, now) of every committed daily-total increment."""
    return [(r["payer"], r["tokens"], r["now"]) for r in sessions.written if r.get("__daily__")]


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
    assert sessions.attempts == attempts + 1  # the one try (a timeout is never the row's fault)
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
    assert await usage._step() is True  # the head alone fails; the account is gone
    assert usage._queue[0].get("user_id") is None
    assert await usage.drain(timeout=5) == 0
    assert [r.get("user_id") for r in _written_rows(sessions)] == [None, None]
    assert _ok() is True and usage._sink_failures == 0


@pytest.mark.asyncio
async def test_an_integrity_error_of_a_living_account_keeps_its_attribution(sink, caplog):
    """The account exists: the row's attribution is never dropped to make a
    write pass. The row cannot be written; its tokens still count."""
    import uuid

    _clock, sessions = sink
    sessions.down = False
    alive = uuid.uuid4()
    sessions.users.add(alive)
    sessions.reject = lambda r: r.get("user_id") == alive
    await usage._persist({"payer": "instance", "total_tokens": 42, "user_id": alive})
    assert await usage._step() is True
    assert not usage._queue and _written_rows(sessions) == []
    assert [(p, t) for p, t, _ in _daily(sessions)] == [("instance", 42)]
    assert any("dropped" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transient",
    [
        TimeoutError("QueuePool limit of size 5 overflow 10 reached"),
        ConnectionResetError("connection was closed in the middle of operation"),
        "operational",
    ],
    ids=["pool-timeout", "connection-reset", "operational-error"],
)
async def test_a_transient_failure_of_one_row_is_a_sink_failure_and_keeps_the_row(sink, transient):
    """Review: a timeout, a lost connection or a serialization failure says
    nothing about the row. It stays queued, with its attribution, and counts
    toward the pause."""
    import uuid

    from sqlalchemy.exc import OperationalError

    if transient == "operational":
        transient = OperationalError("INSERT", {}, Exception("could not serialize access"))
    _clock, sessions = sink
    sessions.down = False
    who = uuid.uuid4()
    sessions.reject = lambda r: True
    sessions.reject_with = transient
    await usage._persist({**ROW, "user_id": who})
    for _ in range(3):
        assert await usage._step() is False
    assert len(usage._queue) == 1 and usage._queue[0]["user_id"] == who
    assert _ok() is False and usage._sink_disabled is True


@pytest.mark.asyncio
async def test_a_malformed_row_is_dropped_its_tokens_count_and_the_rest_is_written(sink, caplog):
    _clock, sessions = sink
    sessions.down = False
    sessions.reject = lambda r: r.get("total_tokens") == 7
    sessions.reject_with = _data_error()
    await usage._persist({"payer": "instance", "total_tokens": 7})
    for _ in range(3):
        await usage._persist(dict(ROW))
    assert await usage.drain(timeout=5) == 0
    assert len(_written_rows(sessions)) == 3 and _ok() is True
    tokens = [t for _, t, _ in _daily(sessions)]
    assert 7 in tokens and sum(tokens) == 37  # the dropped row's tokens count, once
    assert any("dropped" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


@pytest.mark.asyncio
async def test_a_bad_row_while_paused_does_not_keep_the_counter_shut(sink):
    clock, sessions = sink
    sessions.reject = lambda r: r.get("total_tokens") == 7
    sessions.reject_with = _data_error()
    await usage._persist({"payer": "instance", "total_tokens": 7})
    for _ in range(3):
        assert await usage._step() is False
    assert _ok() is False
    sessions.down = False
    clock.now += 31
    assert await usage._step() is True  # the head fails per row; its tokens are written: open
    assert _ok() is True and not usage._queue


# ── the day a row counts for ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rows_count_for_the_local_day_they_ran_on(sink):
    """Two calls either side of midnight in Berlin (one UTC date) are two days
    of the cap; a row queued before midnight and written after it still counts
    for the day it ran on, the same day function the cap reads."""
    from datetime import datetime, timezone

    _clock, sessions = sink
    sessions.down = False
    before = datetime(2026, 11, 15, 22, 59, tzinfo=timezone.utc)  # 23:59 Berlin
    after = datetime(2026, 11, 15, 23, 1, tzinfo=timezone.utc)  # 00:01 Berlin, next day
    await usage._persist({**ROW, "total_tokens": 5, "created_at": before})
    await usage._persist({**ROW, "total_tokens": 3, "created_at": after})
    assert await usage._step() is True
    days = sorted((allowance._today_local(now), tokens) for _, tokens, now in _daily(sessions))
    assert days == [(allowance._today_local(before), 5), (allowance._today_local(after), 3)]
    assert days[0][0] != days[1][0]


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
