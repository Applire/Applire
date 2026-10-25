# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""789:14 — a dead CV / letter generation turns ``failed`` (Hazelnut build 4, WP-O).

The retention worker's two stale-job reapers mark ``pending``/``generating`` rows
older than a MEASURED bound as ``failed``; the retention container runs them
every 10 min between the daily sweeps (``python -m applire.retention
--stale-jobs``), so "Generating…" no longer stands for a day.

Bound: captured LLM debug logs, 195 CV / 159 letter generations — CV p99 616 s,
max 819 s; letter p99 464 s → CV p99 × 3 = 30.8 → 31 min. The old 10 min sat
below the CV p99 and below the measured max.
"""

from __future__ import annotations

import re
import runpy
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire import ownership
from applire.retention import worker

_DDL = [
    """CREATE TABLE generated_cvs (
        id TEXT PRIMARY KEY, job_analysis_id TEXT NOT NULL, profile_id TEXT NOT NULL,
        status TEXT NOT NULL, error_message TEXT, error_code TEXT,
        created_at TEXT NOT NULL, expires_at TEXT, deleted_at TEXT)""",
    """CREATE TABLE generated_cover_letters (
        id TEXT PRIMARY KEY, job_analysis_id TEXT NOT NULL, profile_id TEXT NOT NULL,
        status TEXT NOT NULL, error_message TEXT,
        created_at TEXT NOT NULL, expires_at TEXT, deleted_at TEXT)""",
]

#: Measured on the captured logs (module docstring). A live generation this old
#: is still running on a slow provider and must not be failed.
_MEASURED_CV_MAX_S = 819
_MEASURED_CV_P99_S = 616


def _sqlite_ts(minutes_ago: float) -> str:
    # SQLAlchemy's SQLite DateTime storage format (naive UTC), so the reaper's
    # `created_at < cutoff` compares like with like.
    dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for stmt in _DDL:
            await conn.execute(text(stmt))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(db, table: str, *, status: str, minutes_ago: float, deleted: bool = False) -> str:
    rid = str(uuid.uuid4())
    await db.execute(
        text(
            f"INSERT INTO {table} (id, job_analysis_id, profile_id, status, created_at, deleted_at) "
            "VALUES (:id, :j, :p, :s, :c, :d)"
        ),
        {"id": rid, "j": str(uuid.uuid4()), "p": str(uuid.uuid4()), "s": status,
         "c": _sqlite_ts(minutes_ago), "d": _sqlite_ts(0) if deleted else None},
    )
    await db.commit()
    return rid


async def _status(db, table: str, rid: str) -> tuple[str, str | None]:
    row = (await db.execute(text(f"SELECT status, error_message FROM {table} WHERE id = :id"), {"id": rid})).one()
    return row[0], row[1]


def test_the_bound_is_the_measured_p99_times_three_rounded_up():
    import math

    assert worker._STALE_CV_JOB_MINUTES == math.ceil(_MEASURED_CV_P99_S * 3 / 60) == 31
    assert worker._STALE_CV_JOB_MINUTES * 60 > _MEASURED_CV_MAX_S


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table, reap",
    [("generated_cvs", worker._reap_stale_cv_jobs), ("generated_cover_letters", worker._reap_stale_cl_jobs)],
    ids=["cv", "letter"],
)
async def test_a_dead_generation_past_the_bound_turns_failed(session_factory, table, reap):
    async with session_factory() as db:
        dead = await _seed(db, table, status="generating", minutes_ago=32)
        dead_pending = await _seed(db, table, status="pending", minutes_ago=45)
        # the measured slowest live generation (819 s ≈ 13.7 min) is NOT failed
        slow_live = await _seed(db, table, status="generating", minutes_ago=_MEASURED_CV_MAX_S / 60 + 1)
        just_inside = await _seed(db, table, status="generating", minutes_ago=30)
        ready_old = await _seed(db, table, status="ready", minutes_ago=600)
        deleted_dead = await _seed(db, table, status="generating", minutes_ago=600, deleted=True)

        # production runs the reapers inside the sweep's declared cross-user
        # context (ADR-092 cl. 8); outside it the ORM adds an owner filter
        with ownership.unscoped("retention"):
            assert await reap(db) == 2

        assert await _status(db, table, dead) == ("failed", "Generation timed out (stale job reaper)")
        assert (await _status(db, table, dead_pending))[0] == "failed"
        assert (await _status(db, table, slow_live))[0] == "generating"
        assert (await _status(db, table, just_inside))[0] == "generating"
        assert (await _status(db, table, ready_old))[0] == "ready"
        assert (await _status(db, table, deleted_dead))[0] == "generating"


@pytest.mark.asyncio
async def test_run_stale_jobs_reaps_both_tables_and_writes_no_retention_run(session_factory, monkeypatch, capsys):
    monkeypatch.setattr(worker, "AsyncSessionLocal", session_factory)
    recorded = []

    async def _no_record(*a, **k):  # the ops layer's "last run" stays the daily sweep
        recorded.append(a)

    monkeypatch.setattr(worker, "record_run", _no_record)
    async with session_factory() as db:
        cv = await _seed(db, "generated_cvs", status="generating", minutes_ago=40)
        cl = await _seed(db, "generated_cover_letters", status="generating", minutes_ago=40)

    report = await worker.run_stale_jobs()

    assert report == {"stale_cv_jobs_failed": 1, "stale_cl_jobs_failed": 1}
    assert recorded == []
    assert '"stale_jobs"' in capsys.readouterr().out
    async with session_factory() as db:
        assert (await _status(db, "generated_cvs", cv))[0] == "failed"
        assert (await _status(db, "generated_cover_letters", cl))[0] == "failed"


@pytest.mark.parametrize("argv, expected", [(["--stale-jobs"], "stale"), ([], "full")])
def test_the_entry_point_dispatches_on_the_stale_jobs_flag(monkeypatch, argv, expected):
    called = []

    async def _full():
        called.append("full")

    async def _stale():
        called.append("stale")

    monkeypatch.setattr(worker, "run", _full)
    monkeypatch.setattr(worker, "run_stale_jobs", _stale)
    monkeypatch.setattr(sys, "argv", ["applire.retention", *argv])
    sys.modules.pop("applire.retention.__main__", None)
    runpy.run_module("applire.retention.__main__", run_name="__main__")
    assert called == [expected]


def test_the_retention_container_runs_the_reapers_every_ten_minutes_between_daily_sweeps():
    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    command = yaml.safe_load(compose.read_text())["services"]["retention"]["command"]
    assert "python -m applire.retention;" in command  # the daily full sweep
    assert "python -m applire.retention --stale-jobs" in command
    inner = re.search(r"-lt (\d+) \]; do sleep (\d+);", command)
    assert inner, command
    assert int(inner.group(1)) * int(inner.group(2)) == 86400  # still one full sweep a day
    assert int(inner.group(2)) == 600
