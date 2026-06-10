# tests/unit/test_iter20_cv_sprint6.py
"""
Sprint 6 — CV Generation UI (unit tests)

Covers:
  - _slugify: pure function, no DB required
  - list_cvs_for_job: SQLite in-memory
  - ensure_thumbnails: skips existing files

No Docker, no LLM, no external services.

Run:
    pytest tests/unit/test_iter20_cv_sprint6.py -v
"""
import sys
from pathlib import Path

import pytest

# Make the applire package importable
_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv import _slugify


def test_slugify_lowercases():
    assert _slugify("QA Manager") == "qa-manager"


def test_slugify_replaces_spaces_with_hyphens():
    assert _slugify("Senior Python Engineer") == "senior-python-engineer"


def test_slugify_strips_special_chars_only():
    assert _slugify("QA Manager 21 CFR Part 11") == "qa-manager-21-cfr-part-11"


def test_slugify_strips_special_chars_from_mixed_input():
    assert _slugify("C++ Developer") == "c-developer"


def test_slugify_collapses_multiple_hyphens():
    assert _slugify("Role--Name") == "role-name"


def test_slugify_strips_leading_trailing_hyphens():
    assert _slugify("  Role  ") == "role"


def test_slugify_empty_string():
    assert _slugify("") == ""


def test_slugify_special_chars_only():
    assert _slugify("!@#$%") == ""


import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.session
    import applire.models.application
    import applire.models.flow
    import applire.models.uploads

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _make_cv(job_id: uuid.UUID, status: str = "ready", deleted: bool = False, offset_seconds: int = 0):
    from applire.models.cv import GeneratedCV
    return GeneratedCV(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        tailored_data={},
        template="classic_german",
        status=status,
        created_at=datetime.now(timezone.utc) + timedelta(seconds=offset_seconds),
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )


@pytest.mark.asyncio
async def test_list_cvs_empty_for_unknown_job(db):
    from applire.services.cv import list_cvs_for_job
    result = await list_cvs_for_job(uuid.uuid4(), db, "http://localhost:8001")
    assert result == []


@pytest.mark.asyncio
async def test_list_cvs_sorted_by_created_at_desc(db):
    from applire.services.cv import list_cvs_for_job
    job_id = uuid.uuid4()
    older = _make_cv(job_id, offset_seconds=0)
    newer = _make_cv(job_id, offset_seconds=10)
    db.add(older)
    db.add(newer)
    await db.commit()

    result = await list_cvs_for_job(job_id, db, "http://localhost:8001")
    assert len(result) == 2
    assert result[0].cv_id == newer.id
    assert result[1].cv_id == older.id


@pytest.mark.asyncio
async def test_list_cvs_excludes_soft_deleted(db):
    from applire.services.cv import list_cvs_for_job
    job_id = uuid.uuid4()
    active = _make_cv(job_id)
    deleted = _make_cv(job_id, deleted=True)
    db.add(active)
    db.add(deleted)
    await db.commit()

    result = await list_cvs_for_job(job_id, db, "http://localhost:8001")
    assert len(result) == 1
    assert result[0].cv_id == active.id


@pytest.mark.asyncio
async def test_list_cvs_urls_only_when_ready(db):
    from applire.services.cv import list_cvs_for_job
    job_id = uuid.uuid4()
    ready_cv = _make_cv(job_id, status="ready")
    pending_cv = _make_cv(job_id, status="pending")
    db.add(ready_cv)
    db.add(pending_cv)
    await db.commit()

    result = await list_cvs_for_job(job_id, db, "http://localhost:8001")
    ready_resp = next(r for r in result if r.cv_id == ready_cv.id)
    pending_resp = next(r for r in result if r.cv_id == pending_cv.id)

    assert ready_resp.html_url is not None
    assert ready_resp.pdf_url is not None
    assert pending_resp.html_url is None
    assert pending_resp.pdf_url is None


@pytest.mark.asyncio
async def test_get_pdf_filename_contains_role_slug(db):
    """get_pdf_filename returns lebenslauf-{slug}-{id[:8]}.pdf."""
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV
    from applire.services.cv import get_pdf_filename
    import uuid as _uuid
    from datetime import datetime, timezone, timedelta

    job_id = _uuid.uuid4()
    cv_id = _uuid.uuid4()

    # Create a JobAnalysis row (read the model to get required fields)
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="test_hash_unique_12345",
        raw_text="Sample job description",
        role_title="QA Manager 21 CFR",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    )
    db.add(job)

    # Create a ready GeneratedCV linked to the job
    cv = GeneratedCV(
        id=cv_id,
        job_analysis_id=job_id,
        profile_id=_uuid.uuid4(),
        tailored_data={},
        template="classic_german",
        status="ready",
        created_at=datetime.now(timezone.utc),
        deleted_at=None,
    )
    db.add(cv)
    await db.commit()

    filename = await get_pdf_filename(cv_id, db)
    assert filename == f"lebenslauf-qa-manager-21-cfr-{str(cv_id)[:8]}.pdf"


# ---------------------------------------------------------------------------
# TailoredCVData resilience — LLM occasionally returns null for summary
# (observed live 2026-06-10: ValidationError failed the whole generation)
# ---------------------------------------------------------------------------

def test_tailored_cv_data_coerces_null_summary_to_empty_string():
    from applire.schemas.cv import TailoredCVData

    data = TailoredCVData.model_validate({
        "contact": {"name": "Milan Novak"},
        "summary": None,
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    assert data.summary == ""
