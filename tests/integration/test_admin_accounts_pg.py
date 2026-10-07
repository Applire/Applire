# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PostgreSQL-only proofs for package 1b (ADR-091 cl. 7, 22, 26).

* migration 0073 on the real chain: the ``BEFORE UPDATE`` trigger refuses a raw
  UPDATE of ``audit_events``; DELETE (retention) still works; downgrade is clean;
* the last-admin predicate under a real two-connection race (READ COMMITTED):
  two admins demoting each other at once leave exactly one admin;
* link redemption under a real two-connection race: exactly one winner.

Needs a disposable PostgreSQL database: ``APPLIRE_TEST_PG_URL=postgresql+asyncpg://…``
(rows of users/auth_links/audit_events are deleted; 0073 is downgraded and re-applied). Skipped
without it — SQLite cannot show row locks or the plpgsql trigger.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

PG_URL = os.environ.get("APPLIRE_TEST_PG_URL", "")
pytestmark = pytest.mark.skipif(not PG_URL.startswith("postgresql"),
                                reason="needs APPLIRE_TEST_PG_URL (disposable PostgreSQL)")

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _alembic(*args: str) -> None:
    env = dict(os.environ, DATABASE_URL=PG_URL, PYTHONPATH=str(BACKEND))
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, check=True,
                   capture_output=True, text=True)


@pytest.fixture(scope="module")
def migrated():
    _alembic("upgrade", "head")
    yield
    # Proves 0073's downgrade on PostgreSQL, then restores head for the next run.
    # (``downgrade base`` is not used: an older revision's downgrade fails on a
    # fresh database — not this package's chain.)
    _alembic("downgrade", "0072")
    _alembic("upgrade", "head")


@pytest_asyncio.fixture
async def engine(migrated):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(PG_URL)
    async with eng.begin() as conn:
        await conn.execute(text("DELETE FROM audit_events"))
        await conn.execute(text("DELETE FROM auth_links"))
        await conn.execute(text("DELETE FROM users"))
    yield eng
    await eng.dispose()


async def _insert_user(conn, *, role="admin", password_hash="scrypt$x"):
    from sqlalchemy import text

    uid = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO users (id, email, created_at, photo_consent, role, password_hash, link_epoch) "
        "VALUES (:id, :e, now(), false, :r, :p, 0)"),
        {"id": uid, "e": f"{uid.hex[:8]}@example.org", "r": role, "p": password_hash})
    return uid


@pytest.mark.asyncio
async def test_trigger_refuses_update_and_allows_delete(engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO audit_events (id, at, action, detail) VALUES (:id, now(), 'user.disabled', '{}')"),
            {"id": uuid.uuid4()})
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE audit_events SET action = 'user.enabled'"))
    async with engine.begin() as conn:
        assert (await conn.execute(text("SELECT action FROM audit_events"))).scalar_one() == "user.disabled"
        await conn.execute(text("DELETE FROM audit_events"))
        assert (await conn.execute(text("SELECT count(*) FROM audit_events"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_two_admins_demoting_each_other_concurrently_leave_one(engine):
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession

    from applire.models.user import User
    from applire.services.admin import users as accounts

    async with engine.begin() as conn:
        a = await _insert_user(conn)
        b = await _insert_user(conn)

    t1_locked = asyncio.Event()
    release_t1 = asyncio.Event()

    async def demote(target, *, first):
        async with AsyncSession(engine) as db:
            try:
                await accounts.assert_not_last_admin(db, target)
            except accounts.AccountError:
                await db.rollback()
                return "refused"
            await db.execute(update(User).where(User.id == target).values(role="user"))
            if first:
                t1_locked.set()
                await release_t1.wait()
            await db.commit()
            return "demoted"

    t1 = asyncio.create_task(demote(a, first=True))
    await asyncio.wait_for(t1_locked.wait(), 10)
    t2 = asyncio.create_task(demote(b, first=False))
    try:
        await asyncio.sleep(0.5)  # t2 should now wait on t1's row lock
        t2_blocked = not t2.done()
    finally:
        release_t1.set()
    results = sorted(await asyncio.wait_for(asyncio.gather(t1, t2), 30))
    assert t2_blocked, "the second demotion did not wait for the first one's lock"
    assert results == ["demoted", "refused"]

    from sqlalchemy import text
    async with engine.connect() as conn:
        admins = (await conn.execute(text("SELECT count(*) FROM users WHERE role='admin'"))).scalar_one()
    assert admins == 1


@pytest.mark.asyncio
async def test_concurrent_redeem_has_exactly_one_winner(engine):
    from sqlalchemy.ext.asyncio import AsyncSession

    from applire.models.user import User
    from applire.services.admin import links

    async with engine.begin() as conn:
        uid = await _insert_user(conn, role="user", password_hash=None)
    async with AsyncSession(engine) as db:
        user = await db.get(User, uid)
        _, raw = await links.issue_link(db, user, "invite")
        await db.commit()

    async def redeem():
        async with AsyncSession(engine) as db:
            got = await links.consume_link(db, raw)
            await asyncio.sleep(0.2)
            await db.commit()
            return got

    results = await asyncio.gather(*(redeem() for _ in range(5)))
    assert sum(r is not None for r in results) == 1
