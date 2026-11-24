# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""adv/plans F2 (b): a person's burst of generations cannot take the pool.

A render waits for its person's slot and an instance slot BEFORE it opens a
session, so a burst runs one render per person and kind at a time, at most
``INSTANCE`` in the process, and a waiting render holds no connection. The
Postgres half (a real pool) is ``tests/unit/test_adr096_debit_pg_races.py``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from applire.services import render_slots


@pytest.fixture(autouse=True)
def _fresh():
    render_slots.reset_for_tests()
    yield
    render_slots.reset_for_tests()


class Pool:
    """Counts the connections renders hold (a render 'opens' one on entry)."""

    def __init__(self) -> None:
        self.open = 0
        self.peak = 0
        self.by_person: dict = {}
        self.peak_by_person: dict = {}

    async def render(self, person, *, hold=0.02):
        self.open += 1
        self.peak = max(self.peak, self.open)
        self.by_person[person] = self.by_person.get(person, 0) + 1
        self.peak_by_person[person] = max(self.peak_by_person.get(person, 0), self.by_person[person])
        await asyncio.sleep(hold)
        self.by_person[person] -= 1
        self.open -= 1


def _bounded(pool, kind="cv"):
    @render_slots.bounded_render(kind)
    async def render(cv_id, *, user_id=None):
        await pool.render(user_id)
        return cv_id

    return render


@pytest.mark.asyncio
async def test_one_persons_burst_runs_one_render_at_a_time():
    pool = Pool()
    render = _bounded(pool)
    tester = uuid.uuid4()
    done = await asyncio.gather(*(render(i, user_id=tester) for i in range(12)))
    assert done == list(range(12))  # every render ran — none refused, none lost
    assert pool.peak == 1 == pool.peak_by_person[tester]


@pytest.mark.asyncio
async def test_the_process_runs_at_most_instance_renders_and_others_are_not_starved():
    pool = Pool()
    render = _bounded(pool)
    burster, others = uuid.uuid4(), [uuid.uuid4() for _ in range(8)]
    jobs = [render(i, user_id=burster) for i in range(12)] + [render(100 + i, user_id=p) for i, p in enumerate(others)]
    await asyncio.gather(*jobs)
    assert pool.peak == render_slots.INSTANCE
    assert pool.peak_by_person[burster] == 1
    assert all(pool.peak_by_person[p] == 1 for p in others)


@pytest.mark.asyncio
async def test_a_cv_and_a_letter_of_one_person_may_run_side_by_side():
    pool = Pool()
    cv, letter = _bounded(pool, "cv"), _bounded(pool, "cover_letter")
    tester = uuid.uuid4()
    await asyncio.gather(cv(1, user_id=tester), letter(2, user_id=tester))
    assert pool.peak == 2


@pytest.mark.asyncio
async def test_the_owner_context_names_the_person_when_the_keyword_is_absent():
    from applire.ownership import owner_context

    pool = Pool()

    @render_slots.bounded_render("cv")
    async def render(cv_id, *, user_id=None):
        await pool.render("ctx")
        return cv_id

    tester = uuid.uuid4()

    async def as_tester(i):
        with owner_context(tester):
            return await render(i)

    await asyncio.gather(*(as_tester(i) for i in range(5)))
    assert pool.peak == 1


def test_the_instance_bound_leaves_the_pool_room_for_requests():
    """Each render holds up to two connections (its session + a usage write);
    SQLAlchemy's default pool is 5 + 10 per process (one uvicorn worker)."""
    from sqlalchemy.pool import QueuePool

    import inspect

    defaults = inspect.signature(QueuePool.__init__).parameters
    capacity = defaults["pool_size"].default + defaults["max_overflow"].default
    assert render_slots.INSTANCE * 2 <= capacity - 5


def test_both_generation_renders_are_bounded():
    from applire.services import cover_letter, cv

    assert getattr(cv._render_cv_background, "__applire_render_slot__", None) == "cv"
    assert getattr(cover_letter._render_cover_letter_background, "__applire_render_slot__", None) == "cover_letter"


# ── the wait is bounded (coordinator, fix round: what a waiting person sees) ──


class _Gate:
    """A render body that opens a 'session' and holds it until released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.opened = 0
        self.ran: list = []

    def render(self, kind="cv"):
        @render_slots.bounded_render(kind)
        async def render(record_id, *, user_id=None):
            self.opened += 1  # the body is where a render opens its session
            self.ran.append(record_id)
            await self.release.wait()
            return record_id

        return render


@pytest.fixture
def gave_up(monkeypatch):
    calls: list = []

    async def record(kind, record_id, user_id):
        calls.append((kind, record_id, user_id))

    monkeypatch.setattr(render_slots, "_give_up", record)
    monkeypatch.setattr(render_slots, "MAX_WAIT_SECONDS", 0.05)
    return calls


@pytest.mark.asyncio
async def test_a_waiting_render_opens_no_session_and_gives_up_after_the_bound(gave_up):
    gate = _Gate()
    render = gate.render()
    tester = uuid.uuid4()
    first = asyncio.ensure_future(render("cv-1", user_id=tester))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(render("cv-2", user_id=tester))
    await asyncio.sleep(0.01)
    assert gate.opened == 1  # the waiting render holds no session
    try:
        assert await asyncio.wait_for(second, 2) is None  # no slot within the bound: gives up …
    finally:
        gate.release.set()
    assert gate.ran == ["cv-1"]  # … and never runs
    assert gave_up == [("cv", "cv-2", tester)]  # its record is marked failed
    gate.release.set()
    assert await first == "cv-1"
    assert await render("cv-3", user_id=tester) == "cv-3"  # the slot was not leaked


@pytest.mark.asyncio
async def test_a_render_waiting_for_an_instance_slot_gives_up_too(gave_up):
    gate = _Gate()
    render = gate.render()
    busy = [asyncio.ensure_future(render(f"o-{i}", user_id=uuid.uuid4())) for i in range(render_slots.INSTANCE)]
    await asyncio.sleep(0)
    late = uuid.uuid4()
    try:
        assert await asyncio.wait_for(render("late", user_id=late), 2) is None
    finally:
        gate.release.set()
    assert gave_up == [("cv", "late", late)]
    gate.release.set()
    assert sorted(await asyncio.gather(*busy)) == sorted(f"o-{i}" for i in range(render_slots.INSTANCE))
    assert await render("after", user_id=late) == "after"  # nothing leaked


@pytest.mark.asyncio
async def test_an_error_inside_the_render_is_not_taken_for_a_slot_timeout(gave_up):
    @render_slots.bounded_render("cv")
    async def render(record_id, *, user_id=None):
        raise render_slots.SlotWaitTimeout("inner")

    with pytest.raises(render_slots.SlotWaitTimeout):
        await render("x", user_id=uuid.uuid4())
    assert gave_up == []


@pytest.mark.asyncio
async def test_giving_up_marks_only_a_pending_record_failed(async_db, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.cv import CVGenerationStatus, GeneratedCV
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
    waiting = GeneratedCV(job_analysis_id=job.id, profile_id=profile.id, tailored_data={},
                          status=CVGenerationStatus.pending.value)
    done = GeneratedCV(job_analysis_id=job.id, profile_id=profile.id, tailored_data={},
                       status=CVGenerationStatus.ready.value)
    letter = GeneratedCoverLetter(job_analysis_id=job.id, profile_id=profile.id,
                                  status=CoverLetterStatus.pending.value)
    async_db.add_all([waiting, done, letter])
    await async_db.commit()
    monkeypatch.setattr(
        "applire.db.session.AsyncSessionLocal",
        async_sessionmaker(async_db.bind, class_=AsyncSession, expire_on_commit=False),
    )
    await render_slots._give_up("cv", waiting.id, HARNESS_USER_ID)
    await render_slots._give_up("cv", done.id, HARNESS_USER_ID)
    await render_slots._give_up("cover_letter", letter.id, HARNESS_USER_ID)
    for row in (waiting, done, letter):
        await async_db.refresh(row)
    assert waiting.status == "failed" and "render slot" in waiting.error_message
    assert done.status == "ready" and done.error_message is None
    assert letter.status == "failed"
