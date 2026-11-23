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
from sqlalchemy import delete, func, select
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
