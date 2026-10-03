# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-connection races on real Postgres (ADR-091 cl. 7, 14; SF-IAM.1, SF-IAM.7).

SQLite has no row locks, so these run only when ``APPLIRE_TEST_PG_URL`` points
at a migrated, disposable Postgres database (``alembic upgrade head``), e.g.::

    APPLIRE_TEST_PG_URL=postgresql+asyncpg://test:test@127.0.0.1:55433/mig_test

They are skipped otherwise (``-rs`` names the reason). The real-auth E2E lane
(package 5b) is their CI home.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

PG_URL = os.environ.get("APPLIRE_TEST_PG_URL", "")

pytestmark = pytest.mark.skipif(not PG_URL, reason="APPLIRE_TEST_PG_URL not set (needs Postgres row locks)")


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(PG_URL)
    yield eng
    await eng.dispose()


async def _cleanup(engine):
    from applire.models.user import User

    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(delete(User).where(User.email.like("%@race.example")))
        await db.commit()


@pytest.mark.asyncio
async def test_two_concurrent_setup_claims_exactly_one_wins(engine):
    from applire.auth.harness import STUB_USER_ID
    from applire.auth.setup import claim_stub, ensure_stub_user
    from applire.models.user import User

    await _cleanup(engine)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await ensure_stub_user(db)
        await db.execute(
            update(User).where(User.id == STUB_USER_ID).values(
                password_hash=None, oidc_subject=None, oidc_issuer=None, role="user",
                email="local@applire.community",
            )
        )
        await db.commit()

    async def claim(email):
        async with AsyncSession(engine, expire_on_commit=False) as db:
            ok = await claim_stub(db, email=email, password_hash=f"scrypt${email}")
            await asyncio.sleep(0.2)  # hold the row lock so the other claim waits on it
            await db.commit()
            return ok

    results = await asyncio.gather(claim("a@race.example"), claim("b@race.example"))
    assert sorted(results) == [False, True]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        stub = await db.get(User, STUB_USER_ID)
        winner = "a@race.example" if results[0] else "b@race.example"
        assert stub.email == winner and stub.password_hash == f"scrypt${winner}"
        # restore the stub for the next run
        await db.execute(
            update(User).where(User.id == STUB_USER_ID).values(
                password_hash=None, role="user", email="local@applire.community",
                email_verified_at=None,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_two_admins_demoting_themselves_concurrently_leave_one(engine):
    from applire.auth.roles import assert_not_last_admin
    from applire.models.user import User

    await _cleanup(engine)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        # Any other active admin in this DB would make the race moot.
        await db.execute(
            update(User).where(User.role == "admin").values(role="user")
        )
        a = User(id=uuid.uuid4(), email="a@race.example", role="admin", password_hash="scrypt$a")
        b = User(id=uuid.uuid4(), email="b@race.example", role="admin", password_hash="scrypt$b")
        db.add_all([a, b])
        await db.commit()
        ids = (a.id, b.id)

    a_locked = asyncio.Event()

    async def demote(uid, *, first):
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if not first:
                await a_locked.wait()
            try:
                await assert_not_last_admin(db, uid)
            except HTTPException as exc:
                await db.rollback()
                return exc.detail["error_code"]
            if first:
                a_locked.set()
                await asyncio.sleep(0.3)  # B now blocks on FOR UPDATE
            await db.execute(update(User).where(User.id == uid).values(role="user"))
            await db.commit()
            return "demoted"

    results = await asyncio.gather(demote(ids[0], first=True), demote(ids[1], first=False))
    assert results == ["demoted", "last_admin"]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        roles = dict((await db.execute(select(User.id, User.role).where(User.id.in_(ids)))).all())
        assert sorted(roles.values()) == ["admin", "user"]
    await _cleanup(engine)
