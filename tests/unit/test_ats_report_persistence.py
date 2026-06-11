# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""ADR-039 Task 3: ATS report persistence in the CV pipeline (TDD).

Tests:
1. test_background_job_persists_ats_report
2. test_audit_engine_error_leaves_report_null_and_status_ready
3. test_section_patch_recomputes_report
4. test_get_cv_ats_report_returns_persisted_report
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Helpers — shared stubs
# ---------------------------------------------------------------------------

def _stub_profile_json() -> dict:
    return {
        "professional_summary": {"de": "Erfahrener Entwickler", "en": ""},
        "work_experience": [
            {
                "company": "Acme GmbH",
                "title": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "responsibilities": ["Backend-Entwicklung"],
                "location": None,
            }
        ],
        "education": [
            {
                "institution": "TU Berlin",
                "degree": "B.Sc.",
                "field_of_study": "Informatik",
                "start_date": "2014",
                "end_date": "2018",
                "grade": None,
            }
        ],
        "skills": [{"name": "Python", "level": None, "category": "technical"}],
        "languages": [{"language": "Deutsch", "level": "Muttersprache", "is_native": True}],
        "certifications": [],
        "contact": {
            "first_name": "Max",
            "last_name": "Mustermann",
            "email": "max@example.com",
            "phone": None,
            "location": "Berlin",
            "linkedin": None,
            "xing": None,
            "portfolio": None,
        },
    }


def _stub_tailored_data() -> dict:
    return {
        "contact": {"name": "Max Mustermann", "email": "max@example.com"},
        "summary": "Erfahrener Python-Entwickler",
        "work_history": [
            {
                "company": "Acme GmbH",
                "role": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "bullets": ["Backend-Entwicklung mit Python", "REST APIs"],
            }
        ],
        "skills": ["Python", "FastAPI"],
        "education": [
            {
                "institution": "TU Berlin",
                "degree": "B.Sc.",
                "field": "Informatik",
                "start_date": "2014",
                "end_date": "2018",
            }
        ],
        "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
    }


def _make_ats_report(document: str = "cv") -> "ATSReport":  # type: ignore[name-defined]
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    return ATSReport(
        document=document,
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
    )


# ---------------------------------------------------------------------------
# SQLite DB fixture (mirrors test_micro_session.py db_with_cv)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def db_with_cv(db):
    """Insert a full chain: User → Job → Profile → GeneratedCV (ready, no ats_report)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.cv import GeneratedCV

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    job_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    profile_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    cv_id = uuid.UUID("00000000-0000-0000-0000-000000000005")

    content_snapshot = {
        "introduction": "Erfahrener Python-Entwickler",
        "positions": [
            {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "index": 0,
                "title": "Software Engineer",
                "company": "Acme GmbH",
                "period": "2020-01",
                "bullets": ["Backend-Entwicklung mit Python", "REST APIs"],
            }
        ],
        "skills": ["Python", "FastAPI"],
    }

    user = User(
        id=user_id,
        email="test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="abc123",
        raw_text="Python developer job",
        role_title="Python Developer",
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="de",
    )
    profile = MasterProfile(
        id=profile_id,
        profile_json=_stub_profile_json(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cv = GeneratedCV(
        id=cv_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        tailored_data=_stub_tailored_data(),
        template="classic_german",
        status="ready",
        content_snapshot=content_snapshot,
        section_overrides=None,
        ats_report=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([user, job, profile, cv])
    await db.commit()

    return {
        "db": db,
        "cv_id": cv_id,
        "job_id": job_id,
        "profile_id": profile_id,
    }


# ---------------------------------------------------------------------------
# Test 1: _render_cv_background persists ats_report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_background_job_persists_ats_report(db_with_cv):
    """After _render_cv_background succeeds, record.ats_report is populated and status='ready'."""
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]
    job_id = ctx["job_id"]
    profile_id = ctx["profile_id"]

    known_report = _make_ats_report("cv")

    # Inline profile_json (needed for LLM mock return)
    tailored_raw = _stub_tailored_data()

    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = tailored_raw

    async def fake_review(**kwargs):
        return kwargs["draft"]

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=mock_provider), \
         patch("applire.services.cv.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 0), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cv", return_value=known_report):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    # Re-fetch from db
    record = await session.get(GeneratedCV, cv_id)
    assert record.status == "ready", f"expected status 'ready', got {record.status!r}"
    assert record.ats_report is not None, "ats_report should be populated after successful generation"
    assert record.ats_report["document"] == "cv"


# ---------------------------------------------------------------------------
# Test 2: audit engine error leaves ats_report NULL and status still ready
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_engine_error_leaves_report_null_and_status_ready(db_with_cv):
    """If audit_cv raises, ats_report stays NULL but status must still be 'ready'."""
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]
    job_id = ctx["job_id"]
    profile_id = ctx["profile_id"]

    tailored_raw = _stub_tailored_data()
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = tailored_raw

    async def fake_review(**kwargs):
        return kwargs["draft"]

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=mock_provider), \
         patch("applire.services.cv.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 0), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cv", side_effect=RuntimeError("boom")):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    record = await session.get(GeneratedCV, cv_id)
    assert record.status == "ready", f"status must remain 'ready' even when audit fails, got {record.status!r}"
    assert record.ats_report is None, "ats_report must be NULL when audit engine errors"


# ---------------------------------------------------------------------------
# Test 3: patch_cv_section recomputes ats_report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_section_patch_recomputes_report(db_with_cv):
    """After patch_cv_section, ats_report should be updated (recomputed by _update_ats_report)."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv_section_editor import patch_cv_section

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    # Set an initial ats_report on the record
    record = await session.get(GeneratedCV, cv_id)
    record.ats_report = {"document": "cv", "version": 1, "checks": [], "keywords": {"present": [], "missing": []}, "passed": 0, "failed": 0}
    await session.commit()

    # New report that is clearly distinct
    new_report = _make_ats_report("cv")
    new_report_dict = new_report.model_dump()
    new_report_dict["passed"] = 99  # sentinel to tell old from new

    from applire.schemas.ats import ATSReport
    distinguishable_report = ATSReport.model_validate({**new_report.model_dump(), "passed": 99})

    with patch("applire.services.ats_audit.audit_cv", return_value=distinguishable_report), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-patched")):
        await patch_cv_section(cv_id, "introduction", "Neues Profil", False, session)

    record = await session.get(GeneratedCV, cv_id)
    assert record.ats_report is not None, "ats_report should be recomputed after section patch"
    assert record.ats_report.get("passed") == 99, (
        f"ats_report should reflect the new audit (passed=99), got {record.ats_report}"
    )


# ---------------------------------------------------------------------------
# Test 4: get_cv_ats_report returns persisted report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cv_ats_report_returns_persisted_report(db_with_cv):
    """get_cv_ats_report returns ATSReportResponse with report when ats_report is persisted;
    raises LookupError for unknown cv_id."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv import get_cv_ats_report

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    # Store a known report
    stored = _make_ats_report("cv")
    record = await session.get(GeneratedCV, cv_id)
    record.ats_report = stored.model_dump()
    await session.commit()

    response = await get_cv_ats_report(cv_id, session)
    assert response.document_id == cv_id
    assert response.report is not None
    assert response.report.document == "cv"

    # Unknown uuid must raise LookupError (→ 404 in router)
    with pytest.raises(LookupError):
        await get_cv_ats_report(uuid.uuid4(), session)
