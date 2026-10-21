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
from applire.ownership import owner_context
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


@pytest.fixture(autouse=True)
def _act_as_uid():
    """Every test here acts for UID, as a request does for its user (ADR-092 cl. 8);
    a read for another user runs in that user's own context below."""
    from applire.ownership import owner_context

    with owner_context(UID):
        yield


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
        with owner_context(other):
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
        assert (await get_import_job(db, j1.id, user_id=UID)).status == CVImportStatus.ready.value
        assert (await get_import_job(db, j2.id, user_id=UID)).status == CVImportStatus.ready.value


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
        assert (await get_import_job(db, j1.id, user_id=UID)).status == CVImportStatus.failed.value
        assert (await get_import_job(db, j2.id, user_id=UID)).status == CVImportStatus.ready.value


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw, code",
    [
        ("Model google/gemini-3.5-flash hit the token budget (stop_reason='length')", "llm_truncated"),
        ("OpenRouter call timed out after 120s", "llm_timeout"),
    ],
)
async def test_the_poll_names_a_failure_by_code_never_by_raw_provider_text(factory, client, raw, code):
    """Moved from the retired sync doors' router test (ruling V-4 = A): a reconcile
    that hits the token budget or times out surfaces as a stable ``error_code``;
    the raw provider sentence stays internal (ADR-047 §4)."""
    async with factory() as db:
        job = await create_import_job(db, filename="cv.pdf", user_id=UID)
    exc = LLMTruncatedError(raw) if code == "llm_truncated" else LLMTimeoutError(raw)
    with patch("applire.services.profile.import_jobs.get_provider"), patch(
        "applire.services.profile.import_jobs.get_storage"
    ), patch("applire.services.profile.import_jobs.get_ocr_extractor"), patch(
        "applire.services.profile.upload_cv", new=AsyncMock(side_effect=exc)
    ):
        await run_import_job_background(
            job.id, b"%PDF-1.4", "cv.pdf", "application/pdf", user_id=UID, session_factory=factory
        )
    r = await client.get(f"/api/profile/import-jobs/{job.id}")
    assert r.status_code == 200
    assert r.json()["status"] == "failed" and r.json()["error_code"] == code
    assert raw not in r.text and "stop_reason" not in r.text and "120s" not in r.text


# ---------------------------------------------------------------------------
# #789 L31 — the LinkedIn ZIP / PDF export is an async import job too
# ---------------------------------------------------------------------------


def _linkedin_merged_response(completeness: float = 0.62):
    """What ``import_from_linkedin_zip`` returns on a clean merge: a
    ``ProfileImportResponse`` (NOT a ``CVUploadResponse`` — no status, no expires_at)."""
    from applire.schemas.profile import MasterProfileData, ProfileImportResponse, ProfileStats

    now = datetime.now(timezone.utc)
    return ProfileImportResponse(
        id=uuid.uuid4(),
        profile=MasterProfileData.model_validate({"personal_info": {"name": "Anna Beispiel"}}),
        completeness=completeness,
        stats=ProfileStats(),
        merge_conflicts=[],
        created_at=now,
        updated_at=now,
        merge_status="applied",
        not_applied=[],
        completeness_score=completeness,
    )


def _linkedin_patches(zip_fn=None, pdf_fn=None, upload=None):
    return (
        patch("applire.services.profile.import_from_linkedin_zip", new=zip_fn or AsyncMock()),
        patch("applire.services.profile.import_from_linkedin_pdf", new=pdf_fn or AsyncMock()),
        patch("applire.services.profile.upload_cv", new=upload or AsyncMock()),
        patch("applire.services.profile.import_jobs.get_provider"),
        patch("applire.services.profile.import_jobs.get_storage"),
        patch("applire.services.profile.import_jobs.get_ocr_extractor"),
    )


@pytest.mark.asyncio
async def test_zip_job_runs_the_linkedin_adapter_and_records_a_cv_upload_shaped_result(factory):
    """L31: a ZIP is unambiguous — the job dispatches to ``import_from_linkedin_zip``
    (not ``upload_cv``) and stores a CVUploadResponse-shaped result the poll endpoint
    can serve, adapted from the ProfileImportResponse the sync door returns."""
    async with factory() as db:
        job = await create_import_job(db, filename="export.zip", user_id=UID)
        jid = job.id
    merged = _linkedin_merged_response(0.62)
    zip_fn = AsyncMock(return_value=merged)
    upload = AsyncMock()
    ps = _linkedin_patches(zip_fn=zip_fn, upload=upload)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
        await run_import_job_background(
            jid, b"PK zip bytes", "export.zip", "application/zip",
            user_id=UID, session_factory=factory,
        )
    upload.assert_not_awaited()
    kwargs = zip_fn.call_args.kwargs
    assert zip_fn.call_args.args[0] == b"PK zip bytes"
    assert kwargs["filename"] == "export.zip"
    assert kwargs["user_id"] == UID

    async with factory() as db:
        job = await get_import_job(db, jid, user_id=UID)
    assert job.status == CVImportStatus.ready.value
    # The stored result validates as the poll endpoint's CVUploadResponse.
    stored = CVUploadResponse.model_validate(job.result)
    assert stored.profile_id == merged.id
    assert stored.status == "COMPLETE"
    assert stored.completeness_score == pytest.approx(0.62)
    assert stored.gate == "none"
    assert stored.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_zip_job_low_completeness_is_draft(factory):
    async with factory() as db:
        job = await create_import_job(db, filename="export.zip", user_id=UID)
        jid = job.id
    ps = _linkedin_patches(zip_fn=AsyncMock(return_value=_linkedin_merged_response(0.2)))
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
        await run_import_job_background(
            jid, b"z", "export.zip", "application/zip", user_id=UID, session_factory=factory,
        )
    async with factory() as db:
        job = await get_import_job(db, jid, user_id=UID)
    assert job.result["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_zip_job_keeps_a_gated_result_as_is(factory):
    """A held merge is already a GATED CVUploadResponse — passed through untouched."""
    async with factory() as db:
        job = await create_import_job(db, filename="export.zip", user_id=UID)
        jid = job.id
    staged = uuid.uuid4()
    gated = CVUploadResponse(
        status="GATED", completeness_score=0.0, gate="name_divergence", staged_id=staged,
        expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc), account_name="A", cv_name="B",
    )
    ps = _linkedin_patches(zip_fn=AsyncMock(return_value=gated))
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
        await run_import_job_background(
            jid, b"z", "export.zip", "application/zip", user_id=UID, session_factory=factory,
        )
    async with factory() as db:
        job = await get_import_job(db, jid, user_id=UID)
    assert job.status == CVImportStatus.ready.value
    assert job.result["status"] == "GATED"
    assert job.result["staged_id"] == str(staged)


@pytest.mark.asyncio
async def test_pdf_job_needs_the_explicit_linkedin_source(factory):
    """A PDF is ambiguous (a CV or a LinkedIn profile print): without ``source`` it is
    a CV (``upload_cv``); with ``source="linkedin"`` it is the LinkedIn PDF adapter."""
    async with factory() as db:
        j1 = await create_import_job(db, filename="cv.pdf", user_id=UID)
        j2 = await create_import_job(db, filename="Profile.pdf", user_id=UID)
    pdf_fn = AsyncMock(return_value=_linkedin_merged_response())
    upload = AsyncMock(return_value=_fake_response())
    ps = _linkedin_patches(pdf_fn=pdf_fn, upload=upload)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
        await run_import_job_background(
            j1.id, b"%PDF", "cv.pdf", "application/pdf", user_id=UID, session_factory=factory,
        )
        pdf_fn.assert_not_awaited()
        upload.assert_awaited_once()
        await run_import_job_background(
            j2.id, b"%PDF", "Profile.pdf", "application/pdf",
            user_id=UID, source="linkedin", session_factory=factory,
        )
    pdf_fn.assert_awaited_once()
    assert upload.await_count == 1
    async with factory() as db:
        assert (await get_import_job(db, j2.id, user_id=UID)).status == CVImportStatus.ready.value


@pytest.mark.asyncio
async def test_json_job_with_the_linkedin_source_runs_the_json_import(factory):
    """Ruling V-4 = A: the retired ``POST /import`` took a LinkedIn JSON export as a
    ``linkedin_json`` form field; the job takes it as a ``.json`` file with
    ``source="linkedin"`` and hands the parsed object to ``import_from_linkedin``."""
    async with factory() as db:
        job = await create_import_job(db, filename="export.json", user_id=UID)
        bad = await create_import_job(db, filename="broken.json", user_id=UID)
    json_fn = AsyncMock(return_value=_linkedin_merged_response())
    upload = AsyncMock(return_value=_fake_response())
    ps = _linkedin_patches(upload=upload)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], patch(
        "applire.services.profile.import_from_linkedin", new=json_fn
    ):
        await run_import_job_background(
            job.id, b'{"profile": {"firstName": "Anna"}}', "export.json", "application/json",
            user_id=UID, source="linkedin", session_factory=factory,
        )
        await run_import_job_background(
            bad.id, b"{not json", "broken.json", "application/json",
            user_id=UID, source="linkedin", session_factory=factory,
        )
    json_fn.assert_awaited_once()
    assert json_fn.await_args.args[0] == {"profile": {"firstName": "Anna"}}
    upload.assert_not_awaited()
    async with factory() as db:
        assert (await get_import_job(db, job.id, user_id=UID)).status == CVImportStatus.ready.value
        failed = await get_import_job(db, bad.id, user_id=UID)
    assert failed.status == CVImportStatus.failed.value
    assert failed.error_code == "invalid_document"


@pytest.mark.asyncio
async def test_zip_job_takes_the_same_per_user_lock_as_a_cv_job(factory):
    """One lock, one queue: a LinkedIn ZIP job and a CV job for the same user never
    overlap (the merge is read-latest → write)."""
    async with factory() as db:
        j1 = await create_import_job(db, filename="export.zip", user_id=UID)
        j2 = await create_import_job(db, filename="cv.pdf", user_id=UID)
    events: list = []

    async def slow_zip(zip_bytes, *a, **kw):
        events.append(("start", "zip"))
        await asyncio.sleep(0.05)
        events.append(("end", "zip"))
        return _linkedin_merged_response()

    async def slow_cv(**kw):
        events.append(("start", "cv"))
        await asyncio.sleep(0.05)
        events.append(("end", "cv"))
        return _fake_response()

    ps = _linkedin_patches(zip_fn=slow_zip, upload=slow_cv)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
        await asyncio.gather(
            run_import_job_background(
                j1.id, b"z", "export.zip", "application/zip", user_id=UID, session_factory=factory
            ),
            run_import_job_background(
                j2.id, b"c", "cv.pdf", "application/pdf", user_id=UID, session_factory=factory
            ),
        )
    assert events == [("start", "zip"), ("end", "zip"), ("start", "cv"), ("end", "cv")]


@pytest.mark.asyncio
async def test_zip_job_failure_is_a_clean_failed_job(factory):
    async with factory() as db:
        job = await create_import_job(db, filename="export.zip", user_id=UID)
        jid = job.id
    ps = _linkedin_patches(zip_fn=AsyncMock(side_effect=ValueError("no profile data")))
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
        await run_import_job_background(
            jid, b"z", "export.zip", "application/zip", user_id=UID, session_factory=factory,
        )
    async with factory() as db:
        job = await get_import_job(db, jid, user_id=UID)
    assert job.status == CVImportStatus.failed.value
    assert job.error_code == "invalid_document"


@pytest.mark.asyncio
async def test_start_endpoint_forwards_the_linkedin_source(factory, client):
    """POST /import-jobs?source=linkedin hands ``source`` to the background task; a bare
    POST hands ``None`` (CV behaviour unchanged)."""
    calls: list = []

    async def fake_bg(*args, **kwargs):
        calls.append((args, kwargs))

    with patch("applire.routers.profile.run_import_job_background", new=fake_bg):
        r1 = await client.post(
            "/api/profile/import-jobs?source=linkedin",
            files={"file": ("Profile.pdf", b"%PDF", "application/pdf")},
        )
        r2 = await client.post(
            "/api/profile/import-jobs",
            files={"file": ("export.zip", b"PK", "application/zip")},
        )
        r3 = await client.post(
            "/api/profile/import-jobs?source=bogus",
            files={"file": ("cv.pdf", b"%PDF", "application/pdf")},
        )
    assert r1.status_code == 202 and r2.status_code == 202
    assert r3.status_code == 422  # unknown source is refused, not silently a CV
    assert calls[0][1].get("source") == "linkedin"
    assert calls[1][1].get("source") is None
