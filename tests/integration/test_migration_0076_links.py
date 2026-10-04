# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migration 0076 on a real PostgreSQL — the full chain 0073 → head with a
v0.42-shaped fixture (W2-1; ADR-092 cl. 5c; Strawberry package 4a).

Runs only when ``APPLIRE_PG_TEST_URL`` names a **throwaway** ``…_test`` database
(pgvector available) — the test DROPS its ``public`` schema. Example::

    docker run -d --rm --name strawberry-4a-pg -e POSTGRES_PASSWORD=proof \\
        -e POSTGRES_DB=links_test -p 127.0.0.1:55494:5432 pgvector/pgvector:pg16
    APPLIRE_PG_TEST_URL=postgresql+asyncpg://postgres:proof@127.0.0.1:55494/links_test \\
        LLM_PROVIDER=mistral PYTHONPATH=backend \\
        python3 -m pytest tests/integration/test_migration_0076_links.py --noconftest -q

``--noconftest``: the root ``tests/conftest.py`` would bring up the compose stack.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
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


def _alembic(*args: str) -> str:
    env = {**os.environ, "DATABASE_URL": PG_URL, "PYTHONPATH": str(BACKEND)}
    env.setdefault("LLM_PROVIDER", "mistral")
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stderr


async def _run(fn):
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(PG_URL)
    try:
        async with eng.begin() as conn:
            return await conn.run_sync(fn)
    finally:
        await eng.dispose()


async def _reset() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(PG_URL)
    async with eng.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
    await eng.dispose()


def test_chain_to_head_creates_the_links_on_postgres():
    from tests.support.nougat_fixture import STUB, FixtureDB, seed_duplicate_settings, seed_nougat
    from tests.support.posting_links import seed_0076

    asyncio.run(_reset())
    _alembic("upgrade", "0073")

    def seed(conn):
        db = FixtureDB(conn)
        ids = seed_nougat(db, with_stub=True)
        seed_duplicate_settings(db, ids)
        return ids, seed_0076(db, ids)

    ids, more = asyncio.run(_run(seed))
    log = _alembic("upgrade", "head")
    assert "0076: created 3 application link(s)" in log, log[-2000:]

    def check(conn):
        rows = FixtureDB(conn).rows(
            "applications", "user_id", "job_analysis_id", "user_status", "workflow_status", "deleted_at"
        )
        by_job = {r[1]: r for r in rows}
        assert set(by_job) == {
            ids["j_scraped"], ids["j_pasted"], more["j_flow"], more["j_carded"], more["j_removed"]
        }
        for job in (ids["j_scraped"], ids["j_pasted"], more["j_flow"]):
            assert by_job[job][0] == STUB and by_job[job][2:] == ("tracking", "none", None)
        assert by_job[more["j_removed"]][4] is not None, "a removed card stays removed"
        # FK + unique enforced by PostgreSQL held for every inserted row
        n = conn.execute(sa.text(
            "SELECT count(*) FROM applications a JOIN users u ON u.id = a.user_id "
            "JOIN job_analyses j ON j.id = a.job_analysis_id"
        )).scalar_one()
        assert n == 5

    asyncio.run(_run(check))

    # head → 0075 → head again: 0076 finds nothing new (idempotent), chain stays green
    _alembic("downgrade", "0075")
    log2 = _alembic("upgrade", "head")
    assert "nothing to create" in log2
    asyncio.run(_run(check))
