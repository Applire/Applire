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

"""ADR-039 Tasks 3-5: ATS report persistence and REST exposure (TDD).

Tests:
1. test_background_job_persists_ats_report
2. test_audit_engine_error_leaves_report_null_and_status_ready
3. test_section_patch_recomputes_report
4. test_get_cv_ats_report_returns_persisted_report
5. Router tests: GET /api/cv/{id}/ats-report and GET /api/cover-letter/{id}/ats-report
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
# Test 1b: the Keyword Ledger reaches audit_cv so the missing buckets populate (US203)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cv_audit_receives_keyword_ledger(db_with_cv):
    """US203: _update_ats_report must pass the job's latest Keyword Ledger to audit_cv
    so it can split missing keywords into claimable vs honest-gap."""
    from applire.models.gap import GapAnalysis

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]
    job_id = ctx["job_id"]
    profile_id = ctx["profile_id"]

    ledger = [
        {"concept": "Python", "surface_forms": ["Python"], "claimable": True,
         "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "5y"},
    ]
    session.add(GapAnalysis(
        id=uuid.uuid4(), job_analysis_id=job_id, profile_id=profile_id,
        match_score=50, keyword_ledger=ledger,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    ))
    await session.commit()

    captured: dict = {}

    def fake_audit(pdf, tailored, keywords, ledger=None):
        captured["keywords"] = keywords
        captured["ledger"] = ledger
        return _make_ats_report("cv")

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
         patch("applire.services.ats_audit.audit_cv", side_effect=fake_audit):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    assert captured.get("ledger") == ledger, "audit_cv did not receive the Keyword Ledger"


@pytest.mark.asyncio
async def test_letter_audit_receives_keyword_ledger(db_with_cover_letter):
    """US203 letter twin: _update_ats_report_letter passes the ledger to audit_cover_letter."""
    from applire.models.gap import GapAnalysis

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]
    profile_id = ctx["profile_id"]

    ledger = [
        {"concept": "Python", "surface_forms": ["Python"], "claimable": False,
         "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": ""},
    ]
    session.add(GapAnalysis(
        id=uuid.uuid4(), job_analysis_id=job_id, profile_id=profile_id,
        match_score=50, keyword_ledger=ledger,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    ))
    await session.commit()

    captured: dict = {}

    def fake_audit(pdf, letter_data, keywords, ledger=None):
        captured["ledger"] = ledger
        return _make_ats_report("cover_letter")

    letter_raw = _stub_letter_data()
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = letter_raw

    async def fake_review(**kwargs):
        return kwargs["draft"]

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 0), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", side_effect=fake_audit):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    assert captured.get("ledger") == ledger, "audit_cover_letter did not receive the Keyword Ledger"


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
# Test 3: patch_cv_section enqueues ATS re-audit via BackgroundTasks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_section_patch_recomputes_report(db_with_cv):
    """patch_cv_section enqueues one background task; executing it updates ats_report."""
    from fastapi import BackgroundTasks
    from applire.models.cv import GeneratedCV
    from applire.services.cv_section_editor import patch_cv_section

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    # Set an initial ats_report on the record so we can verify it changes
    record = await session.get(GeneratedCV, cv_id)
    record.ats_report = {"document": "cv", "version": 1, "checks": [], "keywords": {"present": [], "missing": []}, "passed": 0, "failed": 0}
    await session.commit()

    from applire.schemas.ats import ATSReport
    distinguishable_report = ATSReport.model_validate({**_make_ats_report("cv").model_dump(), "passed": 99})

    bg = BackgroundTasks()

    with patch("applire.services.ats_audit.audit_cv", return_value=distinguishable_report), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-patched")):
        await patch_cv_section(cv_id, "introduction", "Neues Profil", False, session, bg)

    # One task must have been enqueued
    assert len(bg.tasks) == 1, f"expected 1 background task, got {len(bg.tasks)}"

    # Execute it — it opens its own AsyncSessionLocal session; patch that to use our test DB
    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.ats_audit.audit_cv", return_value=distinguishable_report), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-patched")):
        mock_session_local.return_value.__aenter__.return_value = session
        await bg.tasks[0]()

    record = await session.get(GeneratedCV, cv_id)
    assert record.ats_report is not None, "ats_report should be recomputed after background task runs"
    assert record.ats_report.get("passed") == 99, (
        f"ats_report should reflect the new audit (passed=99), got {record.ats_report}"
    )


# ---------------------------------------------------------------------------
# Test 4: get_cv_ats_report returns persisted report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cv_ats_report_null_report(db_with_cv):
    """A ready CV whose ats_report is NULL → get_cv_ats_report returns report=None with status passed through."""
    from applire.services.cv import get_cv_ats_report

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    # db_with_cv seeds the record with ats_report=None already
    response = await get_cv_ats_report(cv_id, session)
    assert response.document_id == cv_id
    assert response.report is None, "report should be None when ats_report is NULL"
    assert response.status == "ready", f"status should be 'ready', got {response.status!r}"


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


# ===========================================================================
# ADR-039 Task 4: Cover-letter pipeline persistence hooks (TDD)
# ===========================================================================

# ---------------------------------------------------------------------------
# Helpers — cover-letter stubs
# ---------------------------------------------------------------------------

def _stub_letter_data() -> dict:
    return {
        "header": {
            "name": "Max Mustermann",
            "email": "max@example.com",
            "phone": "+49 30 12345678",
            "location": "Berlin",
        },
        "recipient": {
            "name": "Dr. Anna Schmidt",
            "company": "Acme GmbH",
            "date": "11. Juni 2026",
        },
        "subject": "Bewerbung als Python Developer",
        "salutation": "Sehr geehrte Frau Dr. Schmidt,",
        "body": {
            "paragraphs": [
                "ich bewerbe mich hiermit um die Stelle als Python Developer.",
                "Mit fünf Jahren Erfahrung in der Backend-Entwicklung bringe ich alle geforderten Kenntnisse mit.",
            ]
        },
        "closing": "Mit freundlichen Grüßen",
        "signature": "Max Mustermann",
    }


# ---------------------------------------------------------------------------
# Fixture — db_with_cover_letter
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_with_cover_letter(db):
    """User → Job → Profile → GeneratedCoverLetter (ready, no ats_report)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    job_id  = uuid.UUID("00000000-0000-0000-0000-000000000012")
    profile_id = uuid.UUID("00000000-0000-0000-0000-000000000013")
    cl_id  = uuid.UUID("00000000-0000-0000-0000-000000000015")

    user = User(
        id=user_id,
        email="cl-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="cl_abc123",
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
    cl = GeneratedCoverLetter(
        id=cl_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        template="classic_german",
        letter_data=_stub_letter_data(),
        pre_gen_inputs={"tone": "formal"},
        status=CoverLetterStatus.ready.value,
        section_overrides=None,
        ats_report=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([user, job, profile, cl])
    await db.commit()

    return {
        "db": db,
        "cl_id": cl_id,
        "job_id": job_id,
        "profile_id": profile_id,
    }


# ---------------------------------------------------------------------------
# Test CL-1: _render_cover_letter_background persists ats_report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_background_persists_report(db_with_cover_letter):
    """After _render_cover_letter_background succeeds, cl.ats_report is populated and status='ready'."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    known_report = _make_ats_report("cover_letter")
    letter_raw = _stub_letter_data()

    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = letter_raw

    mock_render_pdf = AsyncMock(return_value=b"%PDF-fake")
    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter_pdf.render_pdf", mock_render_pdf), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=known_report):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    # render_pdf must be called exactly once — smoke render bytes are reused by
    # _update_ats_report_letter; a second Playwright launch must NOT occur.
    mock_render_pdf.assert_called_once()

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready", f"expected status 'ready', got {cl.status!r}"
    assert cl.ats_report is not None, "ats_report should be populated after successful generation"
    assert cl.ats_report["document"] == "cover_letter"


# ---------------------------------------------------------------------------
# Test CL-2: audit error leaves ats_report NULL, status still ready
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_audit_error_leaves_null_and_status_ready(db_with_cover_letter):
    """If audit_cover_letter raises, ats_report stays NULL but status must still be 'ready'."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    letter_raw = _stub_letter_data()
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = letter_raw

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", side_effect=RuntimeError("boom")):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready", f"status must remain 'ready' even when audit fails, got {cl.status!r}"
    assert cl.ats_report is None, "ats_report must be NULL when audit engine errors"


# ---------------------------------------------------------------------------
# Test CL-3: patch_cover_letter_section enqueues ATS re-audit via BackgroundTasks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_section_patch_enqueues_reaudit(db_with_cover_letter):
    """patch_cover_letter_section enqueues one background task; executing it updates ats_report."""
    from fastapi import BackgroundTasks
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import patch_cover_letter_section

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]

    # Seed an initial report so we can detect the change
    cl = await session.get(GeneratedCoverLetter, cl_id)
    cl.ats_report = {
        "version": 1,
        "document": "cover_letter",
        "checks": [],
        "keywords": {"present": [], "missing": []},
        "passed": 0,
        "failed": 0,
    }
    await session.commit()

    from applire.schemas.ats import ATSReport
    sentinel_report = ATSReport.model_validate({
        **_make_ats_report("cover_letter").model_dump(),
        "passed": 77,
    })

    bg = BackgroundTasks()

    with patch("applire.services.ats_audit.audit_cover_letter", return_value=sentinel_report), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-patched")):
        await patch_cover_letter_section(cl_id, "body", "Neuer Absatz", session, bg)

    # One task must have been enqueued
    assert len(bg.tasks) == 1, f"expected 1 background task, got {len(bg.tasks)}"

    # Execute it — it opens its own AsyncSessionLocal; patch that to use our test DB
    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=sentinel_report), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-patched")):
        mock_session_local.return_value.__aenter__.return_value = session
        await bg.tasks[0]()

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.ats_report is not None, "ats_report should be recomputed after background task runs"
    assert cl.ats_report.get("passed") == 77, (
        f"ats_report should reflect the sentinel audit (passed=77), got {cl.ats_report}"
    )


# ---------------------------------------------------------------------------
# Test CL-4: get_cover_letter_ats_report returns persisted report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cover_letter_ats_report(db_with_cover_letter):
    """get_cover_letter_ats_report returns ATSReportResponse; NULL column → report None; unknown id → LookupError."""
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import get_cover_letter_ats_report

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]

    # Case 1: ready letter, ats_report is NULL → report should be None
    response = await get_cover_letter_ats_report(cl_id, session)
    assert response.document_id == cl_id
    assert response.report is None, "report should be None when ats_report column is NULL"
    assert response.status == "ready"

    # Case 2: store a known report → round-trip should return it
    stored = _make_ats_report("cover_letter")
    cl = await session.get(GeneratedCoverLetter, cl_id)
    cl.ats_report = stored.model_dump()
    await session.commit()

    response = await get_cover_letter_ats_report(cl_id, session)
    assert response.document_id == cl_id
    assert response.report is not None
    assert response.report.document == "cover_letter"

    # Case 3: unknown id → LookupError (→ 404 in router)
    with pytest.raises(LookupError):
        await get_cover_letter_ats_report(uuid.uuid4(), session)


# ===========================================================================
# ADR-039 Task 5: REST exposure of ATS reports (TDD)
# ===========================================================================

# ---------------------------------------------------------------------------
# Router fixtures (ASGI in-process clients, mirroring test_cover_letter.py)
# ---------------------------------------------------------------------------

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from applire.db.session import get_db
from applire.auth import get_auth_provider
from applire.auth.no_auth import NoAuthProvider


@pytest_asyncio.fixture
async def cv_router_db():
    """Separate in-memory SQLite for CV router tests."""
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.cover_letter
    import applire.models.session
    import applire.models.application
    import applire.models.flow
    import applire.models.uploads
    import applire.models.color_profile
    import applire.models.company
    import applire.models.user_settings

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def cv_ats_client(cv_router_db):
    """FastAPI ASGI client for CV router (auth + db overridden)."""
    from applire.routers.cv import router as cv_router
    from applire.routers.cv import _get_provider

    _app = FastAPI()
    _app.include_router(cv_router)
    _app.dependency_overrides[get_db] = lambda: cv_router_db
    _app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    _app.dependency_overrides[_get_provider] = lambda: AsyncMock()

    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, cv_router_db


@pytest_asyncio.fixture
async def cl_router_db():
    """Separate in-memory SQLite for cover-letter router tests."""
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.cover_letter
    import applire.models.session
    import applire.models.application
    import applire.models.flow
    import applire.models.uploads
    import applire.models.color_profile
    import applire.models.company
    import applire.models.user_settings

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def cl_ats_client(cl_router_db):
    """FastAPI ASGI client for cover-letter router (auth + db overridden)."""
    from applire.routers.cover_letter import router as cl_router
    from applire.routers.cover_letter import _get_provider

    _app = FastAPI()
    _app.include_router(cl_router)
    _app.dependency_overrides[get_db] = lambda: cl_router_db
    _app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    _app.dependency_overrides[_get_provider] = lambda: AsyncMock()

    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, cl_router_db


# ---------------------------------------------------------------------------
# Helper — seed a ready CV with optional ats_report into the router DB
# ---------------------------------------------------------------------------

async def _seed_cv(db, *, ats_report=None):
    """Insert User → Job → Profile → GeneratedCV into db; return cv_id."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.cv import GeneratedCV

    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    profile_id = uuid.uuid4()

    user = User(id=user_id, email=f"{user_id}@test.example",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    job = JobAnalysis(
        id=job_id, raw_text_hash=str(user_id), raw_text="Dev job",
        role_title="Developer", required_skills=[], nice_to_have_skills=[],
        keywords=[], seniority_level="mid", company_culture_signals=[],
        language_requirement="de",
    )
    profile = MasterProfile(
        id=profile_id, profile_json=_stub_profile_json(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cv = GeneratedCV(
        job_analysis_id=job_id, profile_id=profile_id,
        tailored_data=_stub_tailored_data(), template="classic_german",
        status="ready",
        content_snapshot={"introduction": "x", "positions": [], "skills": []},
        section_overrides=None, ats_report=ats_report,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([user, job, profile, cv])
    await db.commit()
    await db.refresh(cv)
    return cv.id


async def _seed_cl(db, *, ats_report=None):
    """Insert User → Job → Profile → GeneratedCoverLetter into db; return cl_id."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    profile_id = uuid.uuid4()

    user = User(id=user_id, email=f"{user_id}@test.example",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    job = JobAnalysis(
        id=job_id, raw_text_hash=str(user_id), raw_text="Dev job",
        role_title="Developer", required_skills=[], nice_to_have_skills=[],
        keywords=[], seniority_level="mid", company_culture_signals=[],
        language_requirement="de",
    )
    profile = MasterProfile(
        id=profile_id, profile_json=_stub_profile_json(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cl = GeneratedCoverLetter(
        job_analysis_id=job_id, profile_id=profile_id,
        template="classic_german", letter_data=_stub_letter_data(),
        pre_gen_inputs={"tone": "formal"},
        status=CoverLetterStatus.ready.value,
        section_overrides=None, ats_report=ats_report,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([user, job, profile, cl])
    await db.commit()
    await db.refresh(cl)
    return cl.id


# ---------------------------------------------------------------------------
# CV router tests  — GET /api/cv/{cv_id}/ats-report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_cv_ats_report_200_with_report(cv_ats_client):
    """200 with report.document == 'cv' for a ready CV with a persisted ats_report."""
    client, db = cv_ats_client
    report_data = _make_ats_report("cv").model_dump()
    cv_id = await _seed_cv(db, ats_report=report_data)

    resp = await client.get(f"/api/cv/{cv_id}/ats-report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report"] is not None
    assert body["report"]["document"] == "cv"


@pytest.mark.asyncio
async def test_router_cv_ats_report_200_null_report(cv_ats_client):
    """200 with report: null for a ready CV whose ats_report column is NULL."""
    client, db = cv_ats_client
    cv_id = await _seed_cv(db, ats_report=None)

    resp = await client.get(f"/api/cv/{cv_id}/ats-report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report"] is None


@pytest.mark.asyncio
async def test_router_cv_ats_report_404_unknown(cv_ats_client):
    """404 for an unknown CV UUID."""
    client, _ = cv_ats_client
    resp = await client.get(f"/api/cv/{uuid.uuid4()}/ats-report")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cover-letter router tests  — GET /api/cover-letter/{cl_id}/ats-report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_cl_ats_report_200_with_report(cl_ats_client):
    """200 with report.document == 'cover_letter' for a ready CL with a persisted ats_report."""
    client, db = cl_ats_client
    report_data = _make_ats_report("cover_letter").model_dump()
    cl_id = await _seed_cl(db, ats_report=report_data)

    resp = await client.get(f"/api/cover-letter/{cl_id}/ats-report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report"] is not None
    assert body["report"]["document"] == "cover_letter"


@pytest.mark.asyncio
async def test_router_cl_ats_report_200_null_report(cl_ats_client):
    """200 with report: null for a ready CL whose ats_report column is NULL."""
    client, db = cl_ats_client
    cl_id = await _seed_cl(db, ats_report=None)

    resp = await client.get(f"/api/cover-letter/{cl_id}/ats-report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report"] is None


@pytest.mark.asyncio
async def test_router_cl_ats_report_404_unknown(cl_ats_client):
    """404 for an unknown cover-letter UUID."""
    client, _ = cl_ats_client
    resp = await client.get(f"/api/cover-letter/{uuid.uuid4()}/ats-report")
    assert resp.status_code == 404
