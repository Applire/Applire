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
