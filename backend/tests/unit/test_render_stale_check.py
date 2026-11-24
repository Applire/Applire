# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The inline stale check of a CV's and a letter's status never calls a render
failed while it may still be running (adv/plans re-probe 3, R3-1).

The check used to count ten minutes from ``created_at``. A render may wait up
to ``render_slots.MAX_WAIT_SECONDS`` (8 min) for its slots and then runs for
minutes (a measured letter on the staging model: 178 s): one that started late
read ``failed`` while it ran, and turned ``ready`` afterwards. Now:

* a render this process is running, or that waits here for its slot, is never
  stale — it ends its own record (settle / give-up);
* a render whose failure mark could not be written is stale at once;
* any other unfinished record (its render may run in another process, e.g. an
  agent's) is stale only after the longest slot wait plus the longest measured
  render (the stale-job reaper's bound).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from applire.services import render_slots


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    render_slots.reset_for_tests()
    calls: list = []

    async def settle(kind, record_id, user_id):  # the record's own end is tested elsewhere
        calls.append((kind, record_id))

    monkeypatch.setattr(render_slots, "_settle", settle)
    yield calls
    render_slots.reset_for_tests()


async def _record(async_db, kind, status, minutes_ago):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job = JobAnalysis(
        raw_text_hash=f"hash-{uuid.uuid4()}", raw_text="x", role_title="Engineer",
        seniority_level="mid", language_requirement="English",
    )
    profile = make_master_profile(profile_json={"personal_info": {}, "work_experience": []})
    async_db.add_all([job, profile])
    await async_db.flush()
    at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    if kind == "cv":
        row = GeneratedCV(job_analysis_id=job.id, profile_id=profile.id, tailored_data={}, status=status, created_at=at)
    else:
        row = GeneratedCoverLetter(job_analysis_id=job.id, profile_id=profile.id, status=status, created_at=at)
    async_db.add(row)
    await async_db.commit()
    return row


async def _status(async_db, kind, record_id):
    from tests.support.owners import HARNESS_USER_ID

    if kind == "cv":
        from applire.services.cv import get_cv_status

        out = await get_cv_status(record_id, async_db, "http://x", user_id=HARNESS_USER_ID)
    else:
        from applire.services.cover_letter import get_cover_letter_status

        out = await get_cover_letter_status(record_id, async_db, "http://x", user_id=HARNESS_USER_ID)
    return out


def _value(status):
    return getattr(status, "value", status)


class _Running:
    """A render of ``record_id`` that holds its slot until released."""

    def __init__(self, kind):
        self.kind = kind
        self.started = asyncio.Event()
        self.release = asyncio.Event()

        @render_slots.bounded_render(kind)
        async def render(record_id, *, user_id=None):
            self.started.set()
            await self.release.wait()

        self.render = render

    async def __call__(self, record_id, user_id):
        return await self.render(record_id, user_id=user_id)


KINDS = ["cv", "cover_letter"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_render_that_waited_eight_minutes_and_runs_for_three_reads_generating(async_db, kind):
    """Created 11 minutes ago: 8 waiting for a slot, 3 rendering."""
    from tests.support.owners import HARNESS_USER_ID

    record = await _record(async_db, kind, "generating", minutes_ago=11)
    running = _Running(kind)
    task = asyncio.ensure_future(running(record.id, HARNESS_USER_ID))
    await asyncio.wait_for(running.started.wait(), 2)
    try:
        assert _value((await _status(async_db, kind, record.id)).status) == "generating"
    finally:
        running.release.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_render_still_waiting_here_for_its_slot_reads_pending(async_db, kind):
    from tests.support.owners import HARNESS_USER_ID

    # Past the time bound: only the process's knowledge keeps it unfinished.
    record = await _record(async_db, kind, "pending", minutes_ago=render_slots.stale_after_seconds() // 60 + 1)
    holder = _Running(kind)  # the person's one slot is taken by their other render
    other = asyncio.ensure_future(holder(uuid.uuid4(), HARNESS_USER_ID))
    await asyncio.wait_for(holder.started.wait(), 2)
    waiting = asyncio.ensure_future(_Running(kind)(record.id, HARNESS_USER_ID))
    await asyncio.sleep(0.01)
    try:
        assert _value((await _status(async_db, kind, record.id)).status) == "pending"
    finally:
        holder.release.set()
        await other
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_an_unfinished_record_no_render_here_knows_reads_unfinished_within_the_bound(async_db, kind):
    """Its render may run in another process (an agent's): eleven minutes is
    well within slot wait plus the longest measured render."""
    record = await _record(async_db, kind, "generating", minutes_ago=11)
    assert _value((await _status(async_db, kind, record.id)).status) == "generating"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_truly_abandoned_record_reads_failed_with_the_retry(async_db, kind):
    minutes = render_slots.stale_after_seconds() // 60 + 1
    record = await _record(async_db, kind, "generating", minutes_ago=minutes)
    out = await _status(async_db, kind, record.id)
    assert _value(out.status) == "failed"
    assert out.html_url is None and out.pdf_url is None
    if kind == "cv":
        assert out.error_code == "generation_failed"  # the view's localized retry
    else:
        assert out.error_message  # "Generate it again."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_render_whose_failure_mark_could_not_be_written_reads_failed_at_once(async_db, kind, monkeypatch):
    """The settle gave up (the database stayed away longer than its retries):
    the process knows the render is over, so the view need not wait."""
    from tests.support.owners import HARNESS_USER_ID

    record = await _record(async_db, kind, "generating", minutes_ago=2)

    def down():
        raise RuntimeError("database gone for good")

    monkeypatch.setattr("applire.db.session.AsyncSessionLocal", down)
    await render_slots._mark_failed(kind, record.id, HARNESS_USER_ID, ("generating",), "stopped")
    monkeypatch.undo()
    assert _value((await _status(async_db, kind, record.id)).status) == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_once_its_render_has_ended_here_an_old_unfinished_record_is_stale_again(async_db, kind):
    """The process forgets a render when it ends (the settle is stubbed here,
    so the record stays unfinished): the time bound applies again."""
    from tests.support.owners import HARNESS_USER_ID

    record = await _record(async_db, kind, "generating", minutes_ago=render_slots.stale_after_seconds() // 60 + 1)
    running = _Running(kind)
    task = asyncio.ensure_future(running(record.id, HARNESS_USER_ID))
    await asyncio.wait_for(running.started.wait(), 2)
    assert _value((await _status(async_db, kind, record.id)).status) == "generating"
    running.release.set()
    await task
    assert _value((await _status(async_db, kind, record.id)).status) == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_finished_record_is_never_turned(async_db, kind):
    record = await _record(async_db, kind, "ready", minutes_ago=600)
    assert _value((await _status(async_db, kind, record.id)).status) == "ready"


def test_the_bound_is_the_longest_slot_wait_plus_the_longest_measured_render():
    from applire.retention import worker

    assert render_slots.stale_after_seconds() == render_slots.MAX_WAIT_SECONDS + worker._STALE_CV_JOB_MINUTES * 60
