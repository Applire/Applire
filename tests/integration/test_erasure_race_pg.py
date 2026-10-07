# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Erasure vs. a concurrent posting link — two connections on a real PostgreSQL
(ADR-092 cl. 11 "lock first, check second"; adversarial-security §6; SF-OWN).

User A erases; user B links the SAME shared posting P (analyze → application,
RD-2) at the same moment. P must never be deleted out from under a committed
link, and a link must never point at a deleted posting. Both orders:

* **link first** — B's INSERT holds ``KEY SHARE`` on P (its FK check). A's erasure
  must WAIT at ``SELECT … FOR UPDATE``, then its fresh ``DELETE … NOT EXISTS``
  sees B's committed link and KEEPS P — in ONE attempt (no IntegrityError retry:
  the retry is a backstop, not the mechanism).
* **erasure first** — A's transaction holds P's row lock and has deleted P;
  B's INSERT waits on the lock and, when A commits, fails its FK check
  (``IntegrityError``) instead of committing a dangling link; 4a's analyze
  retries its dedup in a fresh session.

Runs only against a throwaway ``…_test`` database named by
``APPLIRE_PG_TEST_URL`` (DROPS the public schema), with ``--noconftest``::

    docker run -d --rm --name strawberry-3b-pg -e POSTGRES_PASSWORD=proof \\
        -e POSTGRES_DB=erasure_test -p 55493:5432 pgvector/pgvector:pg16
    APPLIRE_PG_TEST_URL=postgresql+asyncpg://postgres:proof@127.0.0.1:55493/erasure_test \\
        LLM_PROVIDER=mistral PYTHONPATH=backend \\
        python3 -m pytest tests/integration/test_erasure_race_pg.py --noconftest -q
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

PG_URL = os.environ.get("APPLIRE_PG_TEST_URL", "")
if PG_URL:
    os.environ.setdefault("DATABASE_URL", PG_URL)
BACKEND = Path(__file__).resolve().parents[2] / "backend"

pytestmark = pytest.mark.skipif(
    not PG_URL or not PG_URL.rsplit("/", 1)[-1].endswith("_test"),
    reason="APPLIRE_PG_TEST_URL (a throwaway …_test PostgreSQL DB) not set",
)

WAIT = 1.0  # seconds a blocked transaction is observed to stay blocked


class _Storage:
    async def delete(self, path: str) -> None:  # noqa: ARG002
        return None


def _alembic_head() -> None:
    env = {**os.environ, "DATABASE_URL": PG_URL, "PYTHONPATH": str(BACKEND)}
    env.setdefault("LLM_PROVIDER", "mistral")
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.fixture(scope="module")
def migrated():
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _reset():
        eng = create_async_engine(PG_URL)
        async with eng.begin() as conn:
            await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
            await conn.execute(sa.text("CREATE SCHEMA public"))
        await eng.dispose()

    asyncio.run(_reset())
    _alembic_head()


async def _seed(factory):
    """A (profile + application → P) and B (profile, no link yet); returns ids."""
    from applire import ownership
    from applire.models.application import Application
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile, authorized_profile_write
    from applire.models.user import User

    a = User(id=uuid.uuid4(), email=f"race-a-{uuid.uuid4().hex[:6]}@example.org")
    b = User(id=uuid.uuid4(), email=f"race-b-{uuid.uuid4().hex[:6]}@example.org")
    p = JobAnalysis(raw_text_hash=uuid.uuid4().hex, raw_text="Shared posting.",
                    role_title="Engineer", seniority_level="mid", language_requirement="English")
    with ownership.unscoped("tooling"):
        async with factory() as s:
            s.add_all([a, b, p])
            await s.flush()
            with authorized_profile_write():
                s.add_all([MasterProfile(user_id=a.id, profile_json={}),
                           MasterProfile(user_id=b.id, profile_json={})])
            s.add(Application(user_id=a.id, job_analysis_id=p.id, company_name="Acme", role_title="E"))
            await s.commit()
    return a.id, b.id, p.id


async def _posting_exists(factory, pid) -> bool:
    from applire import ownership
    from applire.models.job import JobAnalysis

    with ownership.unscoped("tooling"):
        async with factory() as s:
            return (await s.get(JobAnalysis, pid)) is not None


def test_link_first_erasure_waits_and_keeps_the_posting(migrated, caplog):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire import ownership
    from applire.models.application import Application
    from applire.services.erasure import erase

    async def main():
        eng = create_async_engine(PG_URL, pool_size=4)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        a, b, p = await _seed(factory)

        linker = factory()
        with ownership.owner_context(b):
            linker.add(Application(user_id=b, job_analysis_id=p, company_name="B", role_title="B"))
            await linker.flush()  # FK check: KEY SHARE on P, uncommitted

        async def _erase():
            async with factory() as s:
                return await erase(s, a, "vault", storage=_Storage())

        task = asyncio.create_task(_erase())
        await asyncio.sleep(WAIT)
        assert not task.done(), "erasure did not wait for the in-flight link (no FOR UPDATE?)"
        await linker.commit()
        await linker.close()
        counts = await asyncio.wait_for(task, timeout=30)
        assert counts["job_analyses"] == 0
        assert counts["applications"] == 1
        assert await _posting_exists(factory, p), "P was deleted under B's committed link"
        await eng.dispose()

    with caplog.at_level(logging.WARNING, logger="applire.services.erasure"):
        asyncio.run(main())
    retries = [r for r in caplog.records if "integrity error" in r.getMessage()]
    assert retries == [], "the lock, not the retry, must keep P (one attempt)"


def test_erasure_first_link_waits_and_fails_its_fk_check(migrated):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire import ownership
    from applire.models.application import Application
    from applire.services import erasure

    async def main():
        eng = create_async_engine(PG_URL, pool_size=4)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        a, b, p = await _seed(factory)

        eraser = factory()
        with ownership.owner_context(a):
            candidates = await erasure._candidate_postings(eraser, a)
            await erasure._delete_owned(eraser, a, "vault")
        assert candidates == {p}
        assert await erasure.purge_unreferenced_postings(eraser, candidates) == 1  # locked + deleted

        async def _link():
            async with factory() as s:
                with ownership.owner_context(b):
                    s.add(Application(user_id=b, job_analysis_id=p, company_name="B", role_title="B"))
                    await s.commit()

        task = asyncio.create_task(_link())
        await asyncio.sleep(WAIT)
        assert not task.done(), "the link did not wait for the erasure's row lock"
        await eraser.commit()
        await eraser.close()
        with pytest.raises(IntegrityError):
            await asyncio.wait_for(task, timeout=30)
        assert not await _posting_exists(factory, p)
        with ownership.unscoped("tooling"):
            async with factory() as s:
                dangling = (await s.execute(
                    sa.select(Application).where(Application.job_analysis_id == p)
                )).scalars().all()
        assert dangling == []
        await eng.dispose()

    asyncio.run(main())


def test_two_users_sharing_a_posting_erase_concurrently(migrated):
    """Both erase at once: each may keep the shared posting (cl. 11 — orphan for
    the retention purge), but neither fails and no row of either survives."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire import ownership
    from applire.models.application import Application
    from applire.services.erasure import erase, purge_unreferenced_postings

    async def main():
        eng = create_async_engine(PG_URL, pool_size=4)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        a, b, p = await _seed(factory)
        with ownership.unscoped("tooling"):
            async with factory() as s:
                s.add(Application(user_id=b, job_analysis_id=p, company_name="B", role_title="B"))
                await s.commit()

        async def _erase(uid):
            async with factory() as s:
                return await erase(s, uid, "account", storage=_Storage())

        ca, cb = await asyncio.gather(_erase(a), _erase(b))
        assert ca["applications"] == cb["applications"] == 1
        if await _posting_exists(factory, p):  # both skipped it → the orphan purge collects it
            with ownership.unscoped("retention"):
                async with factory() as s:
                    assert await purge_unreferenced_postings(s, [p]) == 1
                    await s.commit()
        assert not await _posting_exists(factory, p)
        await eng.dispose()

    asyncio.run(main())
