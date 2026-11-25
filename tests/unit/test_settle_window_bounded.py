# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""This unit tree runs with a bounded render settle window and a ceiling on
every failure mark (backend/tests/support/settle_window.py; Sorbet invite-gate
round 7). CI's unit job has no database, so an unbounded retry against the
default URL cost 60 s per affected test."""

import asyncio
import uuid

import pytest

from applire.services import render_slots


def test_an_ordinary_unit_test_runs_one_try_and_a_ceiled_mark():
    assert render_slots.SETTLE_SECONDS == 0.0
    assert getattr(render_slots._mark_failed, "__settle_window_ceiling__", None) == 5.0


@pytest.mark.real_settle
def test_a_marked_test_keeps_the_real_window():
    assert render_slots.SETTLE_SECONDS == 60.0
    assert render_slots.SHUTDOWN_WAIT_SECONDS == 65.0


@pytest.mark.real_settle
@pytest.mark.asyncio
async def test_a_mark_that_retries_past_the_ceiling_is_cut_off(bounded_settle_window, monkeypatch):
    """The real 60 s window against a database that never answers: the
    ceiling ends the mark after 5 s, and the test would fail by name."""

    def refused():
        raise OSError("connection refused (no database in the unit tier)")

    monkeypatch.setattr("applire.db.session.AsyncSessionLocal", refused)
    monkeypatch.setattr(render_slots, "SETTLE_RETRY_SECONDS", 0.05)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with pytest.raises(asyncio.TimeoutError):
        await render_slots._mark_failed("cv", uuid.uuid4(), uuid.uuid4(), ("generating",), "stopped")
    assert 4.5 < loop.time() - t0 < 8
    assert len(bounded_settle_window) == 1  # the guard saw it ...
    bounded_settle_window.clear()  # ... and this test, which provoked it, passes
