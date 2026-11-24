# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 phase 2 on real Postgres: the per-subject lock of the debit
(FMEA SF-ALLOW.4 — the limit passed under concurrency).

SQLite serialises writers, so these run only when ``APPLIRE_TEST_PG_URL`` points
at a migrated, disposable Postgres database (``alembic upgrade head``), e.g.::

    APPLIRE_TEST_PG_URL=postgresql+asyncpg://postgres:proof@127.0.0.1:5551/races_test

They are skipped otherwise (``-rs`` names the reason). CI home: the
``postgres-only-tests`` job (the ``races_test`` database, beside
``test_auth_pg_races.py``), where a skip fails the lane.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

PG_URL = os.environ.get("APPLIRE_TEST_PG_URL", "")

pytestmark = pytest.mark.skipif(not PG_URL, reason="APPLIRE_TEST_PG_URL not set (needs Postgres advisory locks)")

#: How long the first transaction holds the lock before it commits.
HOLD = 0.4


@pytest.fixture(autouse=True)
def _metered(monkeypatch):
    from applire.auth import links
    from applire.config import settings
    from applire.services import allowance, allowance_seam

    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    monkeypatch.setattr(settings, "allowance_applications_per_month", 1)
    monkeypatch.setattr(settings, "allowance_imports_per_month", 1)
    previous = links._instance_secret
    links.set_instance_secret("pg-race-instance-secret-0123456789")
    allowance.reset_context_for_tests()
    allowance_seam.reset_for_tests()
    yield
    allowance.reset_context_for_tests()
    links._instance_secret = previous


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(PG_URL)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def person(engine):
    from applire.models.allowance import AllowanceLedger
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:10]}@debit-race.example", role="user")
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add(user)
        await db.commit()
    yield user
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(delete(AllowanceLedger).where(AllowanceLedger.user_id == user.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()


async def _debits(engine, user_id, action):
    from applire.models.allowance import AllowanceLedger

    async with AsyncSession(engine) as db:
        return (
            await db.execute(
                select(func.count()).select_from(AllowanceLedger).where(
                    AllowanceLedger.user_id == user_id,
                    AllowanceLedger.kind == "debit",
                    AllowanceLedger.action == action,
                )
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_two_concurrent_debits_for_the_last_application_admit_exactly_one(engine, person, monkeypatch):
    """Two jobs started at once with one application left: one is admitted, the
    other refused ``allowance_exhausted`` — never two debits for one.

    The month's grant exists already (one of two applications spent): on the
    month's first debit the grant's own unique index would serialise the two
    starts and hide a missing lock."""
    from applire.config import settings
    from applire.services import allowance

    monkeypatch.setattr(settings, "allowance_applications_per_month", 2)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await allowance.debit_for_door(db, person.id, "application", ref="job-0")
        await db.commit()
    first_holds = asyncio.Event()

    async def start(ref, *, first):
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if not first:
                await first_holds.wait()
            try:
                await allowance.debit_for_door(db, person.id, "application", ref=ref)
            except allowance.AdmissionRefused as exc:
                await db.rollback()
                return exc.code
            if first:
                first_holds.set()
                await asyncio.sleep(HOLD)  # the debit is flushed, not committed: the lock is held
            await db.commit()
            return "admitted"

    results = await asyncio.gather(start("job-a", first=True), start("job-b", first=False))
    assert sorted(results) == ["admitted", "allowance_exhausted"]
    assert await _debits(engine, person.id, "application") == 2


@pytest.mark.asyncio
async def test_two_concurrent_starts_of_one_job_debit_once(engine, person):
    """A double start of the same job (two tabs, a retried request): both are
    admitted, one debit."""
    from applire.services import allowance

    first_holds = asyncio.Event()

    async def start(*, first):
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if not first:
                await first_holds.wait()
            await allowance.debit_for_door(db, person.id, "application", ref="job-same")
            if first:
                first_holds.set()
                await asyncio.sleep(HOLD)
            await db.commit()
            return "admitted"

    assert await asyncio.gather(start(first=True), start(first=False)) == ["admitted", "admitted"]
    assert await _debits(engine, person.id, "application") == 1


@pytest.mark.asyncio
async def test_the_agent_import_does_not_hold_the_lock_across_its_extraction(engine, person, monkeypatch):
    """The agent's ``import_cv`` counts and commits before the extraction, so the
    person's next debited door is not stalled by minutes of model work."""
    from applire.config import settings
    from applire.mcp import server
    from applire.services import allowance

    monkeypatch.setattr(settings, "allowance_imports_per_month", 3)
    async with AsyncSession(engine, expire_on_commit=False) as agent_db:
        await server._count_agent_import(agent_db, person.id)
        # The extraction would run now, in this still-open session.
        await agent_db.execute(select(1))
        async with AsyncSession(engine, expire_on_commit=False) as web_db:
            await asyncio.wait_for(
                allowance.debit_for_door(web_db, person.id, "application", ref="job-web"), timeout=5
            )
            await web_db.commit()
        await agent_db.commit()
    assert await _debits(engine, person.id, "profile_import") == 1
    assert await _debits(engine, person.id, "application") == 1


# ── adv/plans F2: one person's burst cannot take the pool or the counter ───────


def _tiny_engine(size=2, timeout=2):
    """A pool far smaller than production's, so a missing bound shows at once."""
    return create_async_engine(PG_URL, pool_size=size, max_overflow=0, pool_timeout=timeout)


@pytest.mark.asyncio
async def test_a_persons_burst_of_renders_leaves_the_pool_to_others(engine, person):
    """12 renders of one person, each holding a connection for its run (as a
    real render does across its model work), on a pool of two: with the render
    slots they run one after another, none times out, and another person's
    debited door gets a connection meanwhile."""
    from applire.models.user import User
    from applire.services import allowance, render_slots

    render_slots.reset_for_tests()
    tiny = _tiny_engine()
    burster = uuid.uuid4()
    finished = []

    @render_slots.bounded_render("cv")
    async def render(n, *, user_id=None):
        async with AsyncSession(tiny, expire_on_commit=False) as db:
            await db.execute(select(1))  # the session's transaction holds the connection
            await asyncio.sleep(0.15)  # the model work
            await db.commit()
        finished.append(n)

    async def other_person():
        await asyncio.sleep(0.2)  # while the burst is running
        async with AsyncSession(tiny, expire_on_commit=False) as db:
            await allowance.debit_for_door(db, person.id, "application", ref="job-during-burst")
            await db.commit()
        return "admitted"

    try:
        results = await asyncio.gather(
            *(render(n, user_id=burster) for n in range(12)), other_person(), return_exceptions=True
        )
    finally:
        await tiny.dispose()
        render_slots.reset_for_tests()
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == [], errors
    assert sorted(finished) == list(range(12)) and results[-1] == "admitted"
    assert await _debits(engine, person.id, "application") == 1


@pytest.mark.asyncio
async def test_a_waiting_render_holds_no_connection_and_no_advisory_lock(engine, person, monkeypatch):
    """The generation door's shape: the request debits under the subject's
    advisory lock, commits, and only then schedules the render. Three renders
    of the person: while the first runs, the two waiting ones hold no pool
    connection and nobody holds an advisory lock."""
    from applire.config import settings
    from applire.services import allowance, render_slots

    monkeypatch.setattr(settings, "allowance_applications_per_month", 3)
    render_slots.reset_for_tests()
    tiny = _tiny_engine(size=3)
    running = asyncio.Event()
    release = asyncio.Event()

    @render_slots.bounded_render("cv")
    async def render(n, *, user_id=None):
        async with AsyncSession(tiny, expire_on_commit=False) as db:
            await db.execute(select(1))
            running.set()
            await release.wait()
            await db.commit()
        return n

    async def door(n):
        async with AsyncSession(tiny, expire_on_commit=False) as db:
            await allowance.debit_for_door(db, person.id, "application", ref=f"job-wait-{n}")
            await db.commit()  # the lock ends here, before the render is scheduled
        return asyncio.ensure_future(render(n, user_id=person.id))

    try:
        tasks = [await door(n) for n in range(3)]
        await running.wait()
        await asyncio.sleep(0.05)  # the other two are queued at their slot
        assert tiny.pool.checkedout() == 1  # only the running render's
        async with AsyncSession(engine) as db:
            advisory = (
                await db.execute(text("select count(*) from pg_locks where locktype = 'advisory'"))
            ).scalar_one()
        assert advisory == 0
        release.set()
        assert sorted(await asyncio.gather(*tasks)) == [0, 1, 2]
    finally:
        release.set()
        await tiny.dispose()
        render_slots.reset_for_tests()


@pytest.mark.asyncio
async def test_the_usage_counter_recovers_after_a_pool_outage(engine, monkeypatch):
    """The real sink against real Postgres: while the pool is exhausted the
    counter pauses (instance-paid admission fails closed); when the pool is free
    again and the cool-down has run out, the next write succeeds and admission
    is open again — no restart."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from applire.config import settings
    from applire.models.llm_usage import LlmUsage
    from applire.providers.llm import usage
    from applire.services import allowance

    tiny = _tiny_engine(size=1, timeout=0.3)
    monkeypatch.setattr("applire.db.session.AsyncSessionLocal", async_sessionmaker(tiny, expire_on_commit=False))
    now = [5000.0]
    monkeypatch.setattr(usage, "_sink_clock", lambda: now[0])
    usage.reset_sink_state()
    stage = f"pg-race-{uuid.uuid4().hex[:8]}"
    row = {"provider": "mock", "payer": "instance", "total_tokens": 7, "stage": stage}
    assert settings.allowance_policy == "allowance"
    try:
        hog = AsyncSession(tiny)
        await hog.execute(select(1))  # the burst: the only connection is taken
        for _ in range(3):
            await usage._persist(dict(row))
        assert allowance.usage_counting_ok() is False  # fail closed during the outage
        await hog.rollback()
        await hog.close()  # the burst is over
        assert allowance.usage_counting_ok() is False  # still inside the cool-down
        # Security review: the cool-down alone must not re-open admission. Take
        # the pool again; after the cool-down the probe fails and it stays shut.
        hog = AsyncSession(tiny)
        await hog.execute(select(1))
        now[0] += usage._SINK_COOLDOWN_START + 1
        assert allowance.usage_counting_ok() is False
        await usage._probe_task
        assert allowance.usage_counting_ok() is False and usage._sink_cooldown == 2 * usage._SINK_COOLDOWN_START
        await hog.rollback()
        await hog.close()
        now[0] += usage._sink_cooldown + 1
        assert allowance.usage_counting_ok() is False  # asks for one probe …
        await usage._probe_task
        assert allowance.usage_counting_ok() is True and usage._sink_disabled is False  # … which opens it
        async with AsyncSession(engine) as db:
            probed = (
                await db.execute(select(func.count()).select_from(LlmUsage).where(LlmUsage.stage == "sink_probe"))
            ).scalar_one()
        assert probed == 0  # the probe is rolled back: it counts nothing
        await usage._persist(dict(row))
        async with AsyncSession(engine) as db:
            written = (
                await db.execute(select(func.count()).select_from(LlmUsage).where(LlmUsage.stage == stage))
            ).scalar_one()
        assert written == 1
    finally:
        usage.reset_sink_state()
        async with AsyncSession(engine) as db:
            await db.execute(delete(LlmUsage).where(LlmUsage.stage == stage))
            await db.commit()
        await tiny.dispose()
