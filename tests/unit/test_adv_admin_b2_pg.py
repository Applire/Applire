# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial review adv-admin (Strawberry build 2): Postgres-only probes of the
admin override store (ADR-093). SQLite serialises writers, so these run only
when ``APPLIRE_TEST_PG_URL`` points at a migrated, disposable database::

    APPLIRE_TEST_PG_URL=postgresql+asyncpg://test:test@127.0.0.1:1935/adv \\
      PYTHONPATH=backend python3 -m pytest tests/unit/test_adv_admin_b2_pg.py --noconftest

Skipped otherwise (``-rs`` names the reason).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

PG_URL = os.environ.get("APPLIRE_TEST_PG_URL", "")

pytestmark = pytest.mark.skipif(not PG_URL, reason="APPLIRE_TEST_PG_URL not set (needs two Postgres writers)")


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(PG_URL)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_adv_admin_7_two_first_writes_of_one_key_end_in_an_unhandled_integrity_error(engine):
    """``apply_changes`` reads the rows, then INSERTs a missing key. Two admins
    (or one double-click on the confirmation dialog) writing the same key for the
    first time race: the loser's commit raises ``IntegrityError`` (a bare 500; the
    traceback's ``[parameters: …]`` carries the row — for a secret, its
    ciphertext) instead of a last-write-wins upsert or a contract error."""
    from applire.models.instance_settings import InstanceSetting
    from applire.models.user import User
    from applire.services import instance_settings as svc

    admin_id = uuid.uuid4()
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(delete(InstanceSetting).where(InstanceSetting.key == "MISTRAL_MODEL"))
        db.add(User(id=admin_id, email=f"race-{admin_id.hex[:6]}@race.example", role="admin"))
        await db.commit()

    gate = asyncio.Event()

    async def writer(value: str) -> str:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await svc.load_rows(db)
            await gate.wait()
            try:
                await svc.apply_changes(db, actor_id=admin_id, changes={"MISTRAL_MODEL": value})
                await db.commit()
                return "ok"
            except svc.SettingsError as exc:
                await db.rollback()
                return exc.code
            except Exception as exc:  # what the router does NOT catch
                await db.rollback()
                return type(exc).__name__

    tasks = [asyncio.create_task(writer(f"mistral-race-{i}")) for i in range(2)]
    await asyncio.sleep(0.2)
    gate.set()
    results = await asyncio.gather(*tasks)
    try:
        assert "IntegrityError" not in results, f"concurrent first writes: {results}"
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(delete(InstanceSetting).where(InstanceSetting.key == "MISTRAL_MODEL"))
            await db.commit()
