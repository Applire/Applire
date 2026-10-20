# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""785:L126 / founder ruling R-2 (A) — a fresh install does not read unhealthy on
day one. While the installation is younger than the warn window (keyed on
`instance.first_started_at`, written once by the fresh-install branch), a backup
that was never recorded and a retention run that never happened are `ok` with
their due date; an upgraded instance (no key) gets no grace, and the provider
probe has none at all (a refused key is down on day one because it IS down)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.db.session import Base
from applire.models.llm_usage import LlmUsage
from applire.models.retention_run import RetentionRun
from applire.services.ops import config as ops_config
from applire.services.ops import probes


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[RetentionRun.__table__, LlmUsage.__table__])
        await conn.run_sync(probes._ops_metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _first_started(db, *, days_ago: float):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    await db.execute(
        probes._INSTANCE_STATE.insert().values(
            key=probes.FIRST_STARTED_KEY, value=stamp, updated_at=datetime.now(timezone.utc)
        )
    )
    await db.commit()


def test_the_probe_reads_the_key_the_model_declares():
    from applire.models.instance_state import KEY_INSTANCE_FIRST_STARTED_AT, KNOWN_KEYS

    assert probes.FIRST_STARTED_KEY == KEY_INSTANCE_FIRST_STARTED_AT
    assert KEY_INSTANCE_FIRST_STARTED_AT in KNOWN_KEYS


@pytest.mark.asyncio
async def test_a_fresh_install_without_a_backup_is_ok_with_a_due_date(db):
    await _first_started(db, days_ago=0.01)
    result = await probes.probe_backup(db)
    assert result.status == probes.OK
    assert "first one due by" in result.message and result.detail["first_due_by"]
    assert result.detail["last_backup_at"] is None


@pytest.mark.asyncio
async def test_the_backup_grace_ends_with_the_warn_window(db):
    await _first_started(db, days_ago=ops_config.OPS_BACKUP_WARN_DAYS + 1)
    result = await probes.probe_backup(db)
    assert result.status == probes.DEGRADED and "has ever been recorded" in result.message


@pytest.mark.asyncio
async def test_an_upgraded_instance_gets_no_backup_grace(db):
    result = await probes.probe_backup(db)  # no first_started_at key
    assert result.status == probes.DEGRADED


@pytest.mark.asyncio
async def test_a_fresh_install_before_its_first_retention_run_is_ok(db):
    await _first_started(db, days_ago=0.5)
    result = await probes.probe_retention(db)
    assert result.status == probes.OK and result.message == "first run pending"
    assert result.detail["first_due_by"]


@pytest.mark.asyncio
async def test_the_retention_grace_ends_after_two_intervals(db):
    days = 2 * ops_config.OPS_RETENTION_INTERVAL_SECONDS / 86400 + 0.1
    await _first_started(db, days_ago=days)
    result = await probes.probe_retention(db)
    assert result.status == probes.UNKNOWN


@pytest.mark.asyncio
async def test_an_upgraded_instance_gets_no_retention_grace(db):
    assert (await probes.probe_retention(db)).status == probes.UNKNOWN


@pytest.mark.asyncio
async def test_the_fresh_install_branch_writes_first_started_at_and_an_upgrade_does_not(
    async_db, seed_profile, monkeypatch
):
    import applire.main as main_mod
    from applire.models.instance_state import KEY_INSTANCE_FIRST_STARTED_AT
    from applire.schemas.profile import MasterProfileData
    from applire.services.instance_state import read_state

    @asynccontextmanager
    async def session_factory():
        yield async_db

    monkeypatch.setattr(main_mod, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr("applire.routers.health.set_upgrade_notice", lambda n: None)

    await main_mod._publish_upgrade_notice()  # empty database = fresh install
    stamp = await read_state(async_db, KEY_INSTANCE_FIRST_STARTED_AT)
    assert isinstance(stamp, str) and datetime.fromisoformat(stamp) <= datetime.now(timezone.utc)

    # a second boot (key present) and an upgrade (data, no key) never rewrite it
    await main_mod._publish_upgrade_notice()
    assert await read_state(async_db, KEY_INSTANCE_FIRST_STARTED_AT) == stamp
