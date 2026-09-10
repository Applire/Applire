# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The retention report finally gets a consumer (ADR-086 clause 5, US312).

`SF-RET.1` and `SF-RET.2` both carry "(D, unread)" in their current-control
cells, and the founder's 2026-07-12 ruling settled that *an unread signal is not
a detection mechanism*. These tests pin the three properties that make the row a
control rather than another unread artefact:

1. a successful run leaves a row carrying the report verbatim;
2. **a crashed run also leaves a row**, marked `ok=False`, so the layer can say
   "the last run FAILED" and not only "no run for N hours";
3. the row's write can fail without touching the sweep — a monitoring insert may
   never be the reason a GDPR deletion pass aborts.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.db.session import Base
from applire.models.llm_usage import LlmUsage
from applire.models.retention_run import RetentionRun
from applire.retention import worker
from applire.services.ops import config as ops_config

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    """One shared in-memory database, injected where the worker looks for it."""
    engine = create_async_engine(_SQLITE_URL, poolclass=None)
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[RetentionRun.__table__, LlmUsage.__table__],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "AsyncSessionLocal", maker)
    yield maker
    await engine.dispose()


async def _rows(maker) -> list[RetentionRun]:
    async with maker() as db:
        return list(
            (
                await db.execute(select(RetentionRun).order_by(RetentionRun.run_at))
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_a_successful_run_persists_the_report_verbatim(session_factory, capsys):
    report = {"run_at": "2026-09-08T10:00:00+00:00", "uploads_deleted": 4}

    async def _sweep():
        return report

    import applire.retention.worker as w

    original, w._sweep = w._sweep, _sweep
    try:
        await w.run()
    finally:
        w._sweep = original

    rows = await _rows(session_factory)
    assert len(rows) == 1
    assert rows[0].report == report
    assert rows[0].ok is True
    assert rows[0].error is None
    # The stdout line is unchanged and stays — every log-reading habit still works.
    assert "uploads_deleted" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_crashed_sweep_still_leaves_a_failed_record(session_factory):
    async def _sweep():
        raise RuntimeError("the database went away mid-sweep")

    import applire.retention.worker as w

    original, w._sweep = w._sweep, _sweep
    try:
        with pytest.raises(RuntimeError):
            await w.run()
    finally:
        w._sweep = original

    rows = await _rows(session_factory)
    assert len(rows) == 1
    assert rows[0].ok is False
    assert "RuntimeError" in (rows[0].error or "")


@pytest.mark.asyncio
async def test_a_failed_record_write_never_aborts_the_sweep(monkeypatch, capsys):
    """The worker's job is deleting data on a legal clock. Monitoring yields."""

    class _Broken:
        def __call__(self):
            raise RuntimeError("no such table: retention_runs")

    monkeypatch.setattr(worker, "AsyncSessionLocal", _Broken())

    async def _sweep():
        return {"uploads_deleted": 1}

    import applire.retention.worker as w

    original, w._sweep = w._sweep, _sweep
    try:
        await w.run()  # must not raise
    finally:
        w._sweep = original
    assert "uploads_deleted" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_old_run_records_are_trimmed(session_factory, monkeypatch):
    monkeypatch.setattr(ops_config, "OPS_RETENTION_RUNS_KEEP", 3)
    async with session_factory() as db:
        for day in range(6):
            db.add(
                RetentionRun(
                    run_at=datetime.now(timezone.utc) - timedelta(days=day),
                    report={"uploads_deleted": day},
                    duration_ms=1,
                    ok=True,
                )
            )
        await db.commit()
        deleted = await worker._trim_retention_runs(db)
    assert deleted == 3
    assert len(await _rows(session_factory)) == 3


@pytest.mark.asyncio
async def test_usage_rows_past_their_window_are_purged(session_factory, monkeypatch):
    """ADR-086 clause 10 — a growth bound, not a PII clock."""
    from applire.services.ops.usage_report import purge_old_usage

    async with session_factory() as db:
        for days in (1, 100, 400, 900):
            db.add(
                LlmUsage(
                    created_at=datetime.now(timezone.utc) - timedelta(days=days),
                    provider="mistral",
                    model="m",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                )
            )
        await db.commit()
        deleted = await purge_old_usage(db, 365)
        remaining = (await db.execute(select(LlmUsage))).scalars().all()
    assert deleted == 2
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_a_retention_of_zero_keeps_everything(session_factory):
    from applire.services.ops.usage_report import purge_old_usage

    async with session_factory() as db:
        db.add(
            LlmUsage(
                created_at=datetime.now(timezone.utc) - timedelta(days=5000),
                provider="mistral",
                model="m",
            )
        )
        await db.commit()
        assert await purge_old_usage(db, 0) == 0
