"""
Unit tests for async gap-analysis jobs (E037 N2 — async gap analysis).

The first gap analysis of a fresh job runs heavy real-LLM work (classification +
clustering) that blocks the gaps screen ~2 min and 504s fragilely. The gap-job service
runs that work in a background task, polled via a status endpoint, delegating to the
existing analyze_gaps (so migration-0040 input_fingerprint idempotency is preserved).
These tests cover the model, schemas and service: create (+ concurrent dedup), run
(success + clean failure), and the IDOR-scoped lookup.

No Docker, no real LLM.
Run: pytest tests/unit/test_gap_jobs.py -v
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.db.session import Base
from applire.exceptions import LLMTimeoutError
from applire.models.gap_job import GapAnalysisJob, GapJobStatus

UID = uuid.uuid4()


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[GapAnalysisJob.__table__])
        )
    f = async_sessionmaker(engine, expire_on_commit=False)
    yield f
    await engine.dispose()


# --- Task 1: model ---------------------------------------------------------


def test_gap_job_status_enum_values():
    assert GapJobStatus.pending.value == "pending"
    assert {s.value for s in GapJobStatus} == {
        "pending", "processing", "ready", "failed", "expired"
    }


@pytest.mark.asyncio
async def test_gap_job_defaults_to_pending(factory):
    jid = uuid.uuid4()
    async with factory() as db:
        job = GapAnalysisJob(job_analysis_id=jid, user_id=UID)
        db.add(job)
        await db.commit()
        await db.refresh(job)
    assert job.status == GapJobStatus.pending.value
    assert job.job_analysis_id == jid
    assert job.user_id == UID
    assert job.result_gap_analysis_id is None


# --- Task 2: schemas -------------------------------------------------------


def test_gap_job_schemas_shape():
    from applire.schemas.gap import GapJobResponse, GapJobStatusResponse

    gid = uuid.uuid4()
    resp = GapJobResponse(gap_job_id=gid, status="pending")
    assert resp.gap_job_id == gid and resp.status == "pending"

    poll = GapJobStatusResponse(gap_job_id=gid, status="failed", error_code="llm_timeout")
    assert poll.result is None and poll.error_code == "llm_timeout"


# --- Task 3: service -------------------------------------------------------


def test_classify_gap_error_maps_stable_codes():
    from applire.services.gap_jobs import classify_gap_error

    assert classify_gap_error(LLMTimeoutError("slow")) == "llm_timeout"
    assert classify_gap_error(LookupError("no profile")) == "gap_not_found"
    assert classify_gap_error(RuntimeError("boom")) == "gap_failed"


@pytest.mark.asyncio
async def test_create_gap_job_is_pending(factory):
    from applire.services.gap_jobs import create_gap_job

    jid = uuid.uuid4()
    async with factory() as db:
        job = await create_gap_job(db, job_analysis_id=jid, user_id=None)
    assert job.status == GapJobStatus.pending.value
    assert job.job_analysis_id == jid


@pytest.mark.asyncio
async def test_create_gap_job_dedups_inflight(factory):
    from applire.services.gap_jobs import create_gap_job

    jid = uuid.uuid4()
    async with factory() as db:
        first = await create_gap_job(db, job_analysis_id=jid, user_id=None)
        second = await create_gap_job(db, job_analysis_id=jid, user_id=None)
    assert second.id == first.id  # reused the in-flight pending job, no duplicate LLM run


@pytest.mark.asyncio
async def test_create_gap_job_no_dedup_across_jobs(factory):
    from applire.services.gap_jobs import create_gap_job

    async with factory() as db:
        a = await create_gap_job(db, job_analysis_id=uuid.uuid4(), user_id=None)
        b = await create_gap_job(db, job_analysis_id=uuid.uuid4(), user_id=None)
    assert a.id != b.id  # different jobs → separate gap jobs


@pytest.mark.asyncio
async def test_get_gap_job_idor_scoped(factory):
    from applire.services.gap_jobs import create_gap_job, get_gap_job

    async with factory() as db:
        job = await create_gap_job(db, job_analysis_id=uuid.uuid4(), user_id=UID)
        jid = job.id
    other = uuid.uuid4()
    async with factory() as db:
        assert await get_gap_job(db, jid, user_id=other) is None  # foreign → hidden
        assert await get_gap_job(db, jid, user_id=UID) is not None
        assert await get_gap_job(db, uuid.uuid4(), user_id=UID) is None  # unknown id


@pytest.mark.asyncio
async def test_run_background_success_points_at_row_and_marks_ready(factory):
    from types import SimpleNamespace

    from applire.services.gap_jobs import create_gap_job, get_gap_job, run_gap_job_background

    job_analysis_id = uuid.uuid4()
    async with factory() as db:
        job = await create_gap_job(db, job_analysis_id=job_analysis_id, user_id=UID)
        gid = job.id

    analysis_id = uuid.uuid4()
    fake = AsyncMock(return_value=SimpleNamespace(id=analysis_id))
    with patch("applire.services.gap.analyze_gaps", new=fake), patch(
        "applire.services.gap_jobs.get_provider"
    ):
        await run_gap_job_background(gid, job_analysis_id, UID, session_factory=factory)

    async with factory() as db:
        job = await get_gap_job(db, gid, user_id=UID)
    assert job.status == GapJobStatus.ready.value
    assert job.error_code is None
    assert job.result_gap_analysis_id == analysis_id
    # analyze_gaps was called with the job_analysis_id (idempotency lives inside it).
    assert fake.call_args.args[0] == job_analysis_id


@pytest.mark.asyncio
async def test_run_background_failure_is_clean(factory):
    from applire.services.gap_jobs import create_gap_job, get_gap_job, run_gap_job_background

    job_analysis_id = uuid.uuid4()
    async with factory() as db:
        job = await create_gap_job(db, job_analysis_id=job_analysis_id, user_id=UID)
        gid = job.id

    fake = AsyncMock(side_effect=LLMTimeoutError("slow"))
    with patch("applire.services.gap.analyze_gaps", new=fake), patch(
        "applire.services.gap_jobs.get_provider"
    ):
        # must not raise
        await run_gap_job_background(gid, job_analysis_id, UID, session_factory=factory)

    async with factory() as db:
        job = await get_gap_job(db, gid, user_id=UID)
    assert job.status == GapJobStatus.failed.value
    assert job.error_code == "llm_timeout"
    assert job.result_gap_analysis_id is None
    assert job.error_message  # raw text kept internally
