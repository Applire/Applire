"""
Unit tests for async CV-import jobs (E036 follow-up — async import; PQ F1 queue safety).

A CV upload runs heavy segmented LLM work that, on a slow/output-capped model, exceeds
the request/proxy timeout → 504 → CV dropped. The import-job service runs that work in a
background task, polled via a status endpoint. These tests cover the service: create the
job, run it (success + clean failure), the IDOR-scoped lookup, per-user serialization of
concurrent jobs (PQ F1 — no interleaved _apply_merge), and the active-jobs listing that
backs the dashboard's "import still in progress" indicator.

No Docker, no real LLM.
Run: pytest tests/unit/test_import_jobs.py -v
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
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
    list_import_jobs,
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


# ---------------------------------------------------------------------------
# PQ F1 AC2 — per-user serialization of concurrent import jobs
# ---------------------------------------------------------------------------


def _upload_patches(upload):
    """Patch upload_cv + the provider/storage/ocr factories the background task calls."""
    return (
        patch("applire.services.profile.upload_cv", new=upload),
        patch("applire.services.profile.import_jobs.get_provider"),
        patch("applire.services.profile.import_jobs.get_storage"),
        patch("applire.services.profile.import_jobs.get_ocr_extractor"),
    )


def _slow_upload(events: list, marker_key: str = "file_bytes"):
    """An upload_cv stand-in that records start/end so overlap is observable."""

    async def upload(**kwargs):
        events.append(("start", kwargs[marker_key]))
        await asyncio.sleep(0.05)
        events.append(("end", kwargs[marker_key]))
        return _fake_response()

    return upload


@pytest.mark.asyncio
async def test_same_user_jobs_are_serialized_in_creation_order(factory):
    """PQ F1 (blocker): with all import jobs POSTed up-front, two background jobs for
    the same user must NOT interleave the profile merge (read-latest → write would lose
    one CV's data). Processing must be mutually exclusive and follow creation order."""
    async with factory() as db:
        j1 = await create_import_job(db, filename="a.pdf", user_id=UID)
        j2 = await create_import_job(db, filename="b.pdf", user_id=UID)

    events: list = []
    p1, p2, p3, p4 = _upload_patches(_slow_upload(events))
    with p1, p2, p3, p4:
        await asyncio.gather(
            run_import_job_background(
                j1.id, b"1", "a.pdf", "application/pdf", user_id=UID, session_factory=factory
            ),
            run_import_job_background(
                j2.id, b"2", "b.pdf", "application/pdf", user_id=UID, session_factory=factory
            ),
        )

    # Strictly serialized, in creation order — never start2 before end1.
    assert events == [("start", b"1"), ("end", b"1"), ("start", b"2"), ("end", b"2")]

    async with factory() as db:
        assert (await get_import_job(db, j1.id)).status == CVImportStatus.ready.value
        assert (await get_import_job(db, j2.id)).status == CVImportStatus.ready.value


@pytest.mark.asyncio
async def test_different_users_do_not_block_each_other(factory):
    """The serialization is PER USER — another user's import must not queue behind."""
    other = uuid.uuid4()
    async with factory() as db:
        j1 = await create_import_job(db, filename="a.pdf", user_id=UID)
        j2 = await create_import_job(db, filename="b.pdf", user_id=other)

    events: list = []
    p1, p2, p3, p4 = _upload_patches(_slow_upload(events))
    with p1, p2, p3, p4:
        await asyncio.gather(
            run_import_job_background(
                j1.id, b"1", "a.pdf", "application/pdf", user_id=UID, session_factory=factory
            ),
            run_import_job_background(
                j2.id, b"2", "b.pdf", "application/pdf", user_id=other, session_factory=factory
            ),
        )

    # Both start before either finishes → they ran concurrently.
    assert [e[0] for e in events[:2]] == ["start", "start"]


@pytest.mark.asyncio
async def test_failed_job_releases_the_queue(factory):
    """A failed earlier job must not wedge later jobs for the same user."""
    async with factory() as db:
        j1 = await create_import_job(db, filename="a.pdf", user_id=UID)
        j2 = await create_import_job(db, filename="b.pdf", user_id=UID)

    async def upload(**kwargs):
        if kwargs["file_bytes"] == b"1":
            raise ValueError("not a cv")
        return _fake_response()

    p1, p2, p3, p4 = _upload_patches(upload)
    with p1, p2, p3, p4:
        await asyncio.gather(
            run_import_job_background(
                j1.id, b"1", "a.pdf", "application/pdf", user_id=UID, session_factory=factory
            ),
            run_import_job_background(
                j2.id, b"2", "b.pdf", "application/pdf", user_id=UID, session_factory=factory
            ),
        )

    async with factory() as db:
        assert (await get_import_job(db, j1.id)).status == CVImportStatus.failed.value
        assert (await get_import_job(db, j2.id)).status == CVImportStatus.ready.value


# ---------------------------------------------------------------------------
# PQ F1 AC3 — active-jobs listing (dashboard "import still in progress")
# ---------------------------------------------------------------------------


async def _set_status(factory, job_id: uuid.UUID, status: str) -> None:
    async with factory() as db:
        job = await db.get(CVImportJob, job_id)
        job.status = status
        await db.commit()


@pytest.mark.asyncio
async def test_list_import_jobs_active_returns_pending_and_processing_in_order(factory):
    async with factory() as db:
        j1 = await create_import_job(db, filename="a.pdf", user_id=UID)   # pending
        j2 = await create_import_job(db, filename="b.pdf", user_id=UID)   # → processing
        j3 = await create_import_job(db, filename="c.pdf", user_id=UID)   # → ready
        j4 = await create_import_job(db, filename="d.pdf", user_id=UID)   # → failed
    await _set_status(factory, j2.id, CVImportStatus.processing.value)
    await _set_status(factory, j3.id, CVImportStatus.ready.value)
    await _set_status(factory, j4.id, CVImportStatus.failed.value)

    async with factory() as db:
        jobs = await list_import_jobs(db, user_id=UID, active=True)
    assert [j.id for j in jobs] == [j1.id, j2.id]  # creation order, finished excluded


@pytest.mark.asyncio
async def test_list_import_jobs_is_user_scoped(factory):
    """IDOR guard parity with get_import_job: never another user's jobs."""
    other = uuid.uuid4()
    async with factory() as db:
        mine = await create_import_job(db, filename="mine.pdf", user_id=UID)
        await create_import_job(db, filename="foreign.pdf", user_id=other)

    async with factory() as db:
        jobs = await list_import_jobs(db, user_id=UID, active=True)
    assert [j.id for j in jobs] == [mine.id]


@pytest.mark.asyncio
async def test_list_import_jobs_excludes_expired_pending(factory):
    """An orphaned pending job past its TTL must not pin the indicator forever."""
    async with factory() as db:
        stale = await create_import_job(db, filename="stale.pdf", user_id=UID)
        job = await db.get(CVImportJob, stale.id)
        job.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()

    async with factory() as db:
        jobs = await list_import_jobs(db, user_id=UID, active=True)
    assert jobs == []


# ---------------------------------------------------------------------------
# PQ F1 AC3 — GET /api/profile/import-jobs?active=true endpoint
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(factory):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import router

    app = FastAPI()
    app.include_router(router)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=UID))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_get_import_jobs_active_endpoint(factory, client):
    async with factory() as db:
        j1 = await create_import_job(db, filename="a.pdf", user_id=UID)
        j2 = await create_import_job(db, filename="done.pdf", user_id=UID)
        await create_import_job(db, filename="foreign.pdf", user_id=uuid.uuid4())
    await _set_status(factory, j2.id, CVImportStatus.ready.value)

    resp = await client.get("/api/profile/import-jobs?active=true")
    assert resp.status_code == 200
    items = resp.json()
    assert [i["import_id"] for i in items] == [str(j1.id)]
    assert items[0]["status"] == "pending"
    assert items[0]["filename"] == "a.pdf"
