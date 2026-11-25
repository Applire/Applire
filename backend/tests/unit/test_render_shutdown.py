# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stop ends every render as failed, with the retry (adv/plans re-probe 4, R4-1).

On SIGTERM uvicorn waits for background tasks only up to its
``--timeout-graceful-shutdown``, then cancels them once and goes straight on
to the lifespan shutdown, without awaiting them. So:

* a render cancelled while it runs, or while it still waits for its slot, has
  its record marked ``failed`` (the settle);
* that mark runs in its own task, shielded: a second cancel (the lifespan's,
  or ``asyncio.run``'s at exit) does not stop it;
* the lifespan's ``render_slots.end_renders`` cancels a render body that is
  still running and waits, at most ``SHUTDOWN_WAIT_SECONDS``, for every mark;
  only then does the usage drain run.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest

from applire.services import render_slots

REAL_SETTLE = render_slots._settle


@pytest.fixture(autouse=True)
def marks(monkeypatch):
    render_slots.reset_for_tests()
    calls: list = []

    async def settle(kind, record_id, user_id):
        calls.append((kind, record_id))

    monkeypatch.setattr(render_slots, "_settle", settle)
    yield calls
    render_slots.reset_for_tests()


def _render(kind="cv"):
    started = asyncio.Event()

    @render_slots.bounded_render(kind)
    async def render(record_id, *, user_id=None):
        started.set()
        await asyncio.sleep(3600)

    return render, started


def _registry_empty():
    return not render_slots._live and not render_slots._bodies and not render_slots._marks


@pytest.mark.asyncio
async def test_a_render_cancelled_while_it_runs_is_marked_failed(marks):
    render, started = _render()
    rid = uuid.uuid4()
    task = asyncio.ensure_future(render(rid, user_id=uuid.uuid4()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert marks == [("cv", rid)]
    assert _registry_empty()


@pytest.mark.asyncio
async def test_a_render_cancelled_while_it_waits_for_its_slot_is_marked_failed(marks):
    person = uuid.uuid4()
    holder, started = _render()
    first = asyncio.ensure_future(holder(uuid.uuid4(), user_id=person))
    await asyncio.wait_for(started.wait(), 2)
    waiter, _ = _render()
    rid = uuid.uuid4()
    second = asyncio.ensure_future(waiter(rid, user_id=person))  # waits for the person's one slot
    await asyncio.sleep(0.01)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert ("cv", rid) in marks
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert _registry_empty()


@pytest.mark.asyncio
async def test_the_mark_survives_a_second_cancel(monkeypatch):
    gate, done = asyncio.Event(), []

    async def slow_settle(kind, record_id, user_id):
        await gate.wait()
        done.append(record_id)

    monkeypatch.setattr(render_slots, "_settle", slow_settle)
    render, started = _render()
    rid = uuid.uuid4()
    task = asyncio.ensure_future(render(rid, user_id=uuid.uuid4()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()  # uvicorn's cancel: the wrapper is now in its mark
    await asyncio.sleep(0.01)
    task.cancel()  # a second cancel (asyncio.run at exit)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert render_slots._marks  # still running on its own
    gate.set()
    assert await render_slots.end_renders(timeout=2) == 0
    assert done == [rid]
    assert _registry_empty()


@pytest.mark.asyncio
async def test_end_renders_cancels_a_running_body_and_waits_for_its_mark(marks):
    render, started = _render("cover_letter")
    rid = uuid.uuid4()
    task = asyncio.ensure_future(render(rid, user_id=uuid.uuid4()))
    await asyncio.wait_for(started.wait(), 2)
    assert await render_slots.end_renders(timeout=2) == 0
    assert marks == [("cover_letter", rid)]
    assert task.done() and _registry_empty()


@pytest.mark.asyncio
async def test_end_renders_says_how_many_renders_it_ended(marks, caplog):
    render, started = _render()
    asyncio.ensure_future(render(uuid.uuid4(), user_id=uuid.uuid4()))
    await asyncio.wait_for(started.wait(), 2)
    with caplog.at_level("INFO", logger="applire.services.render_slots"):
        assert await render_slots.end_renders(timeout=2) == 0
    assert "shutdown: 1 render(s) stopped; 1 marked failed, 0 could not be marked" in caplog.text


@pytest.mark.asyncio
async def test_end_renders_names_the_renders_it_could_not_mark(monkeypatch, caplog):
    """The database is down: the marks give up, and the line says so."""

    async def unwritable(kind, record_id, user_id):
        render_slots._note_abandoned(kind, record_id)

    monkeypatch.setattr(render_slots, "_settle", unwritable)
    render, started = _render()
    asyncio.ensure_future(render(uuid.uuid4(), user_id=uuid.uuid4()))
    await asyncio.wait_for(started.wait(), 2)
    with caplog.at_level("INFO", logger="applire.services.render_slots"):
        assert await render_slots.end_renders(timeout=2) == 0
    assert "shutdown: 1 render(s) stopped; 0 marked failed, 1 could not be marked" in caplog.text


@pytest.mark.asyncio
async def test_end_renders_cancels_a_waiting_render_too(marks):
    person = uuid.uuid4()
    holder, started = _render()
    asyncio.ensure_future(holder(uuid.uuid4(), user_id=person))
    await asyncio.wait_for(started.wait(), 2)
    waiter, _ = _render()
    rid = uuid.uuid4()
    asyncio.ensure_future(waiter(rid, user_id=person))
    await asyncio.sleep(0.01)
    assert await render_slots.end_renders(timeout=2) == 0
    assert len(marks) == 2 and ("cv", rid) in marks
    assert _registry_empty()


@pytest.mark.asyncio
async def test_end_renders_gives_up_after_its_bound(monkeypatch, caplog):
    async def stuck(kind, record_id, user_id):
        await asyncio.sleep(3600)

    monkeypatch.setattr(render_slots, "_settle", stuck)
    render, started = _render()
    rid = uuid.uuid4()
    asyncio.ensure_future(render(rid, user_id=uuid.uuid4()))
    await asyncio.wait_for(started.wait(), 2)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with caplog.at_level("WARNING", logger="applire.services.render_slots"):
        assert await render_slots.end_renders(timeout=0.3) == 1
    assert loop.time() - t0 < 1.5
    assert f"render mark cv {rid}" in caplog.text  # names the record left unfinished
    for t in list(render_slots._marks):
        t.cancel()


@pytest.mark.asyncio
async def test_end_renders_with_nothing_running_returns_at_once():
    assert await render_slots.end_renders(timeout=5) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
async def test_a_cancelled_render_leaves_its_record_failed_with_the_retry(async_db, monkeypatch, kind):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from tests.support.owners import HARNESS_USER_ID
    from tests.support.profile_factory import make_master_profile

    job = JobAnalysis(
        raw_text_hash=f"hash-{uuid.uuid4()}", raw_text="x", role_title="Engineer",
        seniority_level="mid", language_requirement="English",
    )
    profile = make_master_profile(profile_json={"personal_info": {}, "work_experience": []})
    async_db.add_all([job, profile])
    await async_db.flush()
    Model = GeneratedCV if kind == "cv" else GeneratedCoverLetter
    extra = {"tailored_data": {}} if kind == "cv" else {}
    running = Model(job_analysis_id=job.id, profile_id=profile.id, status="generating", **extra)
    waiting = Model(job_analysis_id=job.id, profile_id=profile.id, status="pending", **extra)
    async_db.add_all([running, waiting])
    await async_db.commit()
    monkeypatch.setattr(
        "applire.db.session.AsyncSessionLocal",
        async_sessionmaker(async_db.bind, class_=AsyncSession, expire_on_commit=False),
    )
    monkeypatch.setattr(render_slots, "_settle", REAL_SETTLE)
    render, started = _render(kind)
    asyncio.ensure_future(render(running.id, user_id=HARNESS_USER_ID))
    await asyncio.wait_for(started.wait(), 2)
    waiter, _ = _render(kind)
    asyncio.ensure_future(waiter(waiting.id, user_id=HARNESS_USER_ID))
    await asyncio.sleep(0.01)
    assert await render_slots.end_renders(timeout=5) == 0
    for row in (running, waiting):
        await async_db.refresh(row)
        assert row.status == "failed"
        if kind == "cv":
            assert row.error_code == "generation_failed"
    assert _registry_empty()


def test_the_lifespan_ends_renders_before_it_drains_usage():
    from applire import main

    src = inspect.getsource(main.lifespan)
    assert "await end_renders(timeout=SHUTDOWN_WAIT_SECONDS)" in src
    assert src.index("await end_renders(") < src.index("await drain_usage(")


def test_the_shutdown_wait_covers_one_full_settle():
    assert render_slots.SHUTDOWN_WAIT_SECONDS >= render_slots.SETTLE_SECONDS + render_slots.SETTLE_RETRY_SECONDS
