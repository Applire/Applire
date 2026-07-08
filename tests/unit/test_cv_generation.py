# tests/unit/test_iter20_cv_sprint6.py
"""
Sprint 6 — CV Generation UI (unit tests)

Covers:
  - filename_part: pure function, no DB required (E039/US219)
  - get_pdf_filename: <name>_<company>_<role>.pdf contract
  - list_cvs_for_job: SQLite in-memory
  - ensure_thumbnails: skips existing files

No Docker, no LLM, no external services.

Run:
    pytest tests/unit/test_cv_generation.py -v
"""
import sys
from pathlib import Path

import pytest

# Make the applire package importable
_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv import filename_part


def test_filename_part_preserves_case_and_hyphenates_spaces():
    assert filename_part("Senior Python Engineer") == "Senior-Python-Engineer"


def test_filename_part_transliterates_umlauts():
    assert filename_part("Jürgen Müßig-Öztürk") == "Juergen-Muessig-Oeztuerk"


def test_filename_part_transliterates_non_german_diacritics():
    """Non-German diacritics must fold to their base letter, never be dropped —
    'Milan Novák' → 'Milan-Novak', not 'Milan-Novk' (found on the live path)."""
    assert filename_part("Milan Novák") == "Milan-Novak"
    assert filename_part("José García") == "Jose-Garcia"
    assert filename_part("François Petříček") == "Francois-Petricek"


def test_filename_part_strips_special_chars():
    assert filename_part("C++ Developer (m/w/d)") == "C-Developer-mwd"


def test_filename_part_collapses_multiple_hyphens():
    assert filename_part("Role--Name") == "Role-Name"


def test_filename_part_strips_leading_trailing_whitespace():
    assert filename_part("  Role  ") == "Role"


def test_filename_part_empty_and_none():
    assert filename_part("") == ""
    assert filename_part(None) == ""


def test_filename_part_special_chars_only():
    assert filename_part("!@#$%") == ""


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
    import applire.models.cover_letter
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


async def _seed_ready_cv(
    db,
    *,
    role_title: str = "QA Manager 21 CFR",
    company_name: str | None = "DataCraft GmbH",
    contact_name: str | None = "Emma Weber",
):
    """JobAnalysis + ready GeneratedCV pair for filename tests. Returns cv_id."""
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV
    import uuid as _uuid
    from datetime import datetime, timezone

    job_id = _uuid.uuid4()
    cv_id = _uuid.uuid4()
    db.add(JobAnalysis(
        id=job_id,
        raw_text_hash=f"hash-{job_id}",
        raw_text="Sample job description",
        role_title=role_title,
        company_name=company_name,
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    ))
    db.add(GeneratedCV(
        id=cv_id,
        job_analysis_id=job_id,
        profile_id=_uuid.uuid4(),
        tailored_data={"contact": {"name": contact_name}} if contact_name else {},
        template="classic_german",
        status="ready",
        created_at=datetime.now(timezone.utc),
        deleted_at=None,
    ))
    await db.commit()
    return cv_id


@pytest.mark.asyncio
async def test_get_pdf_filename_is_name_company_role(db):
    """E039/US219 (FMEA JF-E-Q.1): downloads must be identifiable in a Downloads
    folder full of applications — <name>_<company>_<role>.pdf."""
    from applire.services.cv import get_pdf_filename

    cv_id = await _seed_ready_cv(db)
    filename = await get_pdf_filename(cv_id, db)
    assert filename == "Emma-Weber_DataCraft-GmbH_QA-Manager-21-CFR.pdf"


@pytest.mark.asyncio
async def test_get_pdf_filename_transliterates_umlauts(db):
    """Umlaut-safe: ä→ae ö→oe ü→ue ß→ss so the name survives every filesystem."""
    from applire.services.cv import get_pdf_filename

    cv_id = await _seed_ready_cv(
        db,
        contact_name="Jürgen Müßig",
        company_name="Bäckerei Höfer AG",
        role_title="Geschäftsführer",
    )
    filename = await get_pdf_filename(cv_id, db)
    assert filename == "Juergen-Muessig_Baeckerei-Hoefer-AG_Geschaeftsfuehrer.pdf"


@pytest.mark.asyncio
async def test_get_pdf_filename_skips_missing_company(db):
    from applire.services.cv import get_pdf_filename

    cv_id = await _seed_ready_cv(db, company_name=None)
    filename = await get_pdf_filename(cv_id, db)
    assert filename == "Emma-Weber_QA-Manager-21-CFR.pdf"


@pytest.mark.asyncio
async def test_get_pdf_filename_strips_unsafe_characters(db):
    """Slashes, quotes & friends never reach the Content-Disposition header."""
    from applire.services.cv import get_pdf_filename

    cv_id = await _seed_ready_cv(
        db,
        contact_name='Emma "Em" Weber',
        company_name="Data/Craft: GmbH & Co. KG",
        role_title="Senior Analyst (m/w/d)",
    )
    filename = await get_pdf_filename(cv_id, db)
    assert filename == "Emma-Em-Weber_DataCraft-GmbH-Co-KG_Senior-Analyst-mwd.pdf"


@pytest.mark.asyncio
async def test_get_pdf_filename_falls_back_when_all_parts_missing(db):
    from applire.services.cv import get_pdf_filename

    cv_id = await _seed_ready_cv(db, contact_name=None, company_name=None, role_title="")
    filename = await get_pdf_filename(cv_id, db)
    assert filename == f"lebenslauf-{str(cv_id)[:8]}.pdf"


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
