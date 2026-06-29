"""
Unit tests for async CV-import jobs (E036 follow-up — async import).

A CV upload runs heavy segmented LLM work that, on a slow/output-capped model, exceeds
the request/proxy timeout → 504 → CV dropped. The import-job service runs that work in a
background task, polled via a status endpoint. These tests cover the service: create the
job, run it (success + clean failure), and the IDOR-scoped lookup.

No Docker, no real LLM.
Run: pytest tests/unit/test_import_jobs.py -v
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.db.session import Base
from applire.models.import_job import CVImportJob, CVImportStatus
from applire.schemas.profile import CVUploadResponse
from applire.exceptions import LLMTruncatedError, LLMTimeoutError
from applire.services.profile.import_jobs import (
    create_import_job,
    get_import_job,
    run_import_job_background,
)

UID = uuid.uuid4()


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[CVImportJob.__table__])
        )
    f = async_sessionmaker(engine, expire_on_commit=False)
    yield f
    await engine.dispose()


def _fake_response() -> CVUploadResponse:
    return CVUploadResponse(
        profile_id=uuid.uuid4(),
        status="DRAFT",
        completeness_score=0.62,
        expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_create_import_job_is_pending(factory):
    async with factory() as db:
        job = await create_import_job(db, filename="markus_cv.pdf", user_id=UID)
    assert job.status == CVImportStatus.pending.value
    assert job.filename == "markus_cv.pdf"
    assert job.user_id == UID
    assert job.result is None


@pytest.mark.asyncio
async def test_run_background_success_stores_result_and_marks_ready(factory):
    async with factory() as db:
        job = await create_import_job(db, filename="cv.pdf", user_id=UID)
        jid = job.id

    resp = _fake_response()
    with patch(
        "applire.services.profile.upload_cv",
        new=AsyncMock(return_value=resp),
    ), patch("applire.services.profile.import_jobs.get_provider"), patch(
        "applire.services.profile.import_jobs.get_storage"
    ), patch(
        "applire.services.profile.import_jobs.get_ocr_extractor"
    ):
        await run_import_job_background(
            jid, b"%PDF bytes", "cv.pdf", "application/pdf",
            session_factory=factory,
        )

    async with factory() as db:
        job = await get_import_job(db, jid, user_id=UID)
    assert job.status == CVImportStatus.ready.value
    assert job.error_code is None
    assert job.result["profile_id"] == str(resp.profile_id)
    assert job.result["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_run_background_passes_job_id_and_user_through(factory):
    async with factory() as db:
        job = await create_import_job(db, filename="cv.pdf", user_id=UID)
        jid = job.id
    job_id = uuid.uuid4()
    mock_upload = AsyncMock(return_value=_fake_response())
    with patch("applire.services.profile.upload_cv", new=mock_upload), patch(
        "applire.services.profile.import_jobs.get_provider"
    ), patch("applire.services.profile.import_jobs.get_storage"), patch(
        "applire.services.profile.import_jobs.get_ocr_extractor"
    ):
        await run_import_job_background(
            jid, b"x", "cv.pdf", "application/pdf",
            job_id=job_id, user_id=UID, session_factory=factory,
        )
    kwargs = mock_upload.call_args.kwargs
    assert kwargs["job_id"] == job_id
    assert kwargs["user_id"] == UID
    assert kwargs["file_bytes"] == b"x"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc,code",
    [
        (LLMTruncatedError("cap"), "llm_truncated"),
        (LLMTimeoutError("slow"), "llm_timeout"),
        (ValueError("not a cv"), "invalid_document"),
    ],
)
async def test_run_background_failure_is_clean(factory, exc, code):
    """A failed import never raises; it marks the job failed with a stable error_code and
    keeps the raw text internal (never surfaced)."""
    async with factory() as db:
        job = await create_import_job(db, filename="cv.pdf", user_id=UID)
        jid = job.id

    with patch(
        "applire.services.profile.upload_cv",
        new=AsyncMock(side_effect=exc),
    ), patch("applire.services.profile.import_jobs.get_provider"), patch(
        "applire.services.profile.import_jobs.get_storage"
    ), patch(
        "applire.services.profile.import_jobs.get_ocr_extractor"
    ):
        # must not raise
        await run_import_job_background(
            jid, b"x", "cv.pdf", "application/pdf", session_factory=factory,
        )

    async with factory() as db:
        job = await get_import_job(db, jid, user_id=UID)
    assert job.status == CVImportStatus.failed.value
    assert job.error_code == code
    assert job.result is None
    assert job.error_message  # raw text kept internally


@pytest.mark.asyncio
async def test_get_import_job_idor_scoped(factory):
    async with factory() as db:
        job = await create_import_job(db, filename="cv.pdf", user_id=UID)
        jid = job.id
    other = uuid.uuid4()
    async with factory() as db:
        assert await get_import_job(db, jid, user_id=other) is None  # foreign → hidden
        assert await get_import_job(db, jid, user_id=UID) is not None
        assert await get_import_job(db, uuid.uuid4(), user_id=UID) is None  # unknown id
