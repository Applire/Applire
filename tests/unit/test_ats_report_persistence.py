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

from tests.support.profile_factory import make_master_profile

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
    profile = make_master_profile(
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
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit._audit_cv_text", return_value=known_report):
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

    def fake_audit(text, tailored, keywords, ledger=None, **kwargs):
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
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit._audit_cv_text", side_effect=fake_audit):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    assert captured.get("ledger") == ledger, "the audit did not receive the Keyword Ledger"


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

    def fake_audit(pdf, letter_data, keywords, ledger=None, **kwargs):
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

    # US203's pin: the audit receives THE ledger of this job, row for row.
    # #592 (ADR-048 amended): what it receives is the persisted row re-derived
    # against the CURRENT vault, so the assertion is on identity of the rows,
    # not on their persisted statuses. This fixture is itself a miniature of the
    # bug — the row says `Python` is an honest gap while the vault's skills list
    # names Python, so the refresh legitimately lifts it and the letter's
    # DO-NOT-CLAIM block stops contradicting the profile beside it.
    received = captured.get("ledger")
    assert received is not None, "audit_cover_letter did not receive the Keyword Ledger"
    assert [e["concept"] for e in received] == [e["concept"] for e in ledger]
    assert received[0]["status"] == "direct" and received[0]["claimable"] is True


@pytest.mark.asyncio
async def test_cv_audit_receives_vault_text_norm(db_with_cv):
    """#249 run-4: _update_ats_report must thread the vault's literal corpus into the
    audit so the shared-predicate guard on present_unsupported is active in production
    (a keyword the Oracle grounds literally can never be labeled unsupported)."""
    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]
    job_id = ctx["job_id"]
    profile_id = ctx["profile_id"]

    captured: dict = {}

    def fake_audit(text, tailored, keywords, ledger=None, **kwargs):
        captured.update(kwargs)
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
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit._audit_cv_text", side_effect=fake_audit):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    vault = captured.get("vault_text_norm")
    assert isinstance(vault, str) and vault, "audit did not receive the vault corpus"
    assert "acme" in vault, "vault corpus does not carry the profile's literal text"


@pytest.mark.asyncio
async def test_cv_audit_receives_vault_skill_forms(db_with_cv):
    """#391 interim (ADR-076 amendment 4 point 6): _update_ats_report must thread
    the vault's claimable skill names into the audit so the skills-weak-vault-tie
    advisory is live in production, not just reachable via a direct unit call."""
    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]
    job_id = ctx["job_id"]
    profile_id = ctx["profile_id"]

    captured: dict = {}

    def fake_audit(text, tailored, keywords, ledger=None, **kwargs):
        captured.update(kwargs)
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
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit._audit_cv_text", side_effect=fake_audit):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    forms = captured.get("vault_skill_forms")
    assert isinstance(forms, list) and forms, "audit did not receive vault skill forms"
    assert "Python" in forms, "vault skill forms does not carry the profile's claimable skill"


@pytest.mark.asyncio
async def test_letter_audit_receives_vault_text_norm(db_with_cover_letter):
    """#249 run-4 letter twin: _update_ats_report_letter threads the vault corpus."""
    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    captured: dict = {}

    def fake_audit(pdf, letter_data, keywords, ledger=None, **kwargs):
        captured.update(kwargs)
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

    vault = captured.get("vault_text_norm")
    assert isinstance(vault, str) and vault, "letter audit did not receive the vault corpus"
    assert "acme" in vault, "vault corpus does not carry the profile's literal text"


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
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit._audit_cv_text", side_effect=RuntimeError("boom")):
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

    with patch("applire.services.ats_audit._audit_cv_text", return_value=distinguishable_report), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-patched")):
        await patch_cv_section(cv_id, "introduction", "Neues Profil", False, session, bg)

    # One task must have been enqueued
    assert len(bg.tasks) == 1, f"expected 1 background task, got {len(bg.tasks)}"

    # Execute it — it opens its own AsyncSessionLocal session; patch that to use our test DB
    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.ats_audit._audit_cv_text", return_value=distinguishable_report), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
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


@pytest.mark.asyncio
async def test_get_cv_ats_report_malformed_degrades_to_null(db_with_cv):
    """E037 PQ #2 hardening: a non-conforming stored ats_report must degrade to
    report=null rather than raise (which would be an HTTP 500 the frontend can't recover)."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv import get_cv_ats_report

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    # Store a structurally invalid report (missing required fields / wrong types)
    record = await session.get(GeneratedCV, cv_id)
    record.ats_report = {"document": 123, "not_a_real_field": True}
    await session.commit()

    response = await get_cv_ats_report(cv_id, session)
    assert response.document_id == cv_id
    assert response.report is None, "malformed stored report must degrade to report=None"
    assert response.status == "ready"


# ---------------------------------------------------------------------------
# E057/ADR-079 clause 4 groundwork (#629, story #637): the not_applicable
# bucket, API-response layer. No producer constructs one yet — these round-
# trip a hand-built report through the persisted-JSONB → get_cv_ats_report
# path the same way test_get_cv_ats_report_returns_persisted_report does.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cv_ats_report_not_applicable_excluded_from_totals(db_with_cv):
    """A stored report carrying a not_applicable check round-trips through
    get_cv_ats_report with the count in its own bucket — never folded into
    passed/failed."""
    from applire.models.cv import GeneratedCV
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    from applire.services.cv import get_cv_ats_report

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    stored = ATSReport(
        document="cv",
        checks=[
            ATSCheck(id="contact-name", status="pass"),
            ATSCheck(id="page-length", status="not_applicable"),
        ],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
        not_applicable=1,
    )
    record = await session.get(GeneratedCV, cv_id)
    record.ats_report = stored.model_dump(mode="json")
    await session.commit()

    response = await get_cv_ats_report(cv_id, session)
    assert response.report is not None
    assert response.report.passed == 1
    assert response.report.failed == 0
    assert response.report.not_applicable == 1


@pytest.mark.asyncio
async def test_get_cv_ats_report_legacy_payload_reads_not_applicable_as_none(db_with_cv):
    """A report persisted before this field existed has no `not_applicable`
    key in its stored JSONB at all — the API response must read that as
    None, never as a silent 0 (schemas/ats.py's back-compat comment: a
    legacy report was never given the chance to say "confirmed zero")."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv import get_cv_ats_report

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    record = await session.get(GeneratedCV, cv_id)
    # The exact shape of a report persisted before this migration shipped —
    # no "not_applicable" key at all.
    record.ats_report = {
        "version": 1,
        "document": "cv",
        "checks": [{"id": "contact-name", "status": "pass"}],
        "keywords": {"present": ["Python"], "missing": []},
        "passed": 1,
        "failed": 0,
    }
    await session.commit()

    response = await get_cv_ats_report(cv_id, session)
    assert response.report is not None
    assert response.report.not_applicable is None


@pytest.mark.asyncio
async def test_mcp_get_cv_ats_report_not_applicable_excluded_from_totals(db_with_cv):
    """The MCP agent door's ATS report summary (applire.mcp.server's
    get_cv_ats_report tool) is a bare `.model_dump(mode="json")` of the same
    ATSReportResponse the REST endpoint returns — proven directly against
    the actual tool function, not just by analogy to another tool."""
    from applire.mcp.server import get_cv_ats_report as mcp_get_cv_ats_report
    from applire.models.cv import GeneratedCV
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    stored = ATSReport(
        document="cv",
        checks=[
            ATSCheck(id="contact-name", status="pass"),
            ATSCheck(id="page-length", status="not_applicable"),
        ],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
        not_applicable=1,
    )
    record = await session.get(GeneratedCV, cv_id)
    record.ats_report = stored.model_dump(mode="json")
    await session.commit()

    def _db_cm(sess):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    with patch("applire.mcp.server.get_db", return_value=_db_cm(session)):
        result = await mcp_get_cv_ats_report(str(cv_id))

    report = result["report"]
    assert report is not None
    assert report["passed"] == 1
    assert report["failed"] == 0
    assert report["not_applicable"] == 1


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
    profile = make_master_profile(
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

    async def fake_review(**kwargs):
        return kwargs["draft"]

    mock_render_pdf = AsyncMock(return_value=b"%PDF-fake")
    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter_pdf.render_pdf", mock_render_pdf), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=known_report):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    # render_pdf must be called exactly once on the no-change path — the
    # measure render's bytes are reused by _update_ats_report_letter; the
    # audit itself must NOT launch a second Playwright render (#539: reviews
    # are stubbed to settle unchanged, so no terminal re-composition renders).
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
# Test CL-2b: condense re-render failure must not audit a STALE PDF (Finding 1,
# review of #177 / ADR-051 §6 amended condense-regenerate — ADR-039)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_condense_rerender_failure_audits_with_null_pdf_not_stale(db_with_cover_letter):
    """Review Finding 1 / ADR-039: when the post-condense render_pdf call raises,
    pdf_bytes must NOT keep the STALE pre-condense PDF — _update_ats_report_letter
    must be called with pdf=None so the persisted audit is never computed against
    content that no longer matches the (condensed) letter_data. ADR-039: a NULL
    report / a fresh internal re-render beats a report computed from stale content."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    letter_raw = _stub_letter_data()
    condensed_raw = _stub_letter_data()
    condensed_raw["body"]["paragraphs"] = ["Condensed body."]

    mock_provider = AsyncMock()
    # 1st call = initial generation, 2nd call = condense generation.
    mock_provider.aparse_json.side_effect = [letter_raw, condensed_raw]

    async def fake_review(**kwargs):
        return kwargs["draft"]

    # 1st render_pdf = pre-condense smoke render (succeeds); 2nd = post-condense
    # re-render (fails) — this is the exact failure the finding targets.
    mock_render_pdf = AsyncMock(side_effect=[b"%PDF-stale", RuntimeError("render boom")])
    mock_update_report = AsyncMock()

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 0), \
         patch("applire.services.cover_letter_pdf.render_pdf", mock_render_pdf), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.cover_letter._update_ats_report_letter", mock_update_report):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    mock_update_report.assert_awaited_once()
    _call_args, call_kwargs = mock_update_report.call_args
    assert call_kwargs.get("pdf") is None, (
        "Finding 1 (ADR-039): condense re-render failed but pdf=None was not passed "
        f"to _update_ats_report_letter (got {call_kwargs.get('pdf')!r}) — the audit "
        "must never be computed against the STALE pre-condense PDF."
    )

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready"


# ---------------------------------------------------------------------------
# Test CL-2c: condense LLM/review failure must fail open, not fail the letter
# (Finding 2, review of #177 / ADR-051 §6 amended condense-regenerate)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_condense_llm_failure_keeps_original_letter_ready(db_with_cover_letter):
    """Review Finding 2: a transient LLM error inside the condense pass (generation
    or review) must NOT propagate to the outer handler and mark the WHOLE letter
    'failed' — that would discard an already-valid rendered letter over a bounded
    best-effort optimization pass. On condense failure the ORIGINAL letter_data and
    PDF/audit must stand untouched and status must still flip to 'ready' — the ATS
    audit stays the honest backstop for any residual page overrun."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    letter_raw = _stub_letter_data()

    mock_provider = AsyncMock()
    # 1st call = initial generation (succeeds); 2nd call = condense pass (fails).
    mock_provider.aparse_json.side_effect = [letter_raw, RuntimeError("LLM transient error")]

    async def fake_review(**kwargs):
        return kwargs["draft"]

    mock_render_pdf = AsyncMock(return_value=b"%PDF-original")
    known_report = _make_ats_report("cover_letter")

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 0), \
         patch("applire.services.cover_letter_pdf.render_pdf", mock_render_pdf), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=known_report):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    # The condense pass failed before any re-render was attempted, so render_pdf
    # must have been called exactly once (the original pre-condense smoke render).
    mock_render_pdf.assert_called_once()

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready", (
        f"Finding 2: a transient condense-pass LLM error must not fail the whole "
        f"letter — got status {cl.status!r}"
    )
    # #564: _stub_letter_data()'s body.paragraphs carries no recognisable
    # Anrede (its salutation lives in a legacy top-level "salutation" key
    # this schema no longer uses), so the FIRST successful compose — before
    # the condense pass ever runs — now injects the generic floor as
    # paragraphs[0]. "The ORIGINAL letter_data" is that composed state, not
    # the raw pre-compose draft; letter_raw itself is untouched (a deep copy
    # is made inside _compose_letter before any guard mutates it).
    from applire.templates.labels import cover_letter_labels

    assert cl.letter_data["body"]["paragraphs"] == [
        cover_letter_labels("de")["salutation"],
        *letter_raw["body"]["paragraphs"],
    ], "condense failure must leave the ORIGINAL (composed) letter_data untouched"
    assert cl.ats_report is not None and cl.ats_report["document"] == "cover_letter", (
        "the original (pre-condense) PDF audit must still be persisted, not discarded"
    )


@pytest.mark.asyncio
async def test_letter_condense_commit_failure_rolls_back_to_original_not_half_state(
    db_with_cover_letter,
):
    """#181 (review item 4): if the condense pass's own db.commit() fails AFTER
    cl.letter_data was reassigned to the condensed value, the fail-open handler must
    roll back and restore the original — otherwise the function's final commit would
    persist the half-condensed state. Status must still reach 'ready'."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    letter_raw = _stub_letter_data()
    condensed_raw = _stub_letter_data()
    condensed_raw["body"]["paragraphs"] = ["Condensed body that must NOT be persisted."]

    mock_provider = AsyncMock()
    # 1st call = initial generation (succeeds); 2nd = condense generation (succeeds).
    mock_provider.aparse_json.side_effect = [letter_raw, condensed_raw]

    async def fake_review(**kwargs):
        return kwargs["draft"]

    mock_render_pdf = AsyncMock(return_value=b"%PDF-original")
    known_report = _make_ats_report("cover_letter")

    # Fail ONLY the condense commit (the 3rd db.commit in the flow: generating →
    # initial letter_data → condense → final ready). The others delegate to the
    # real session so setup and the final ready-flip persist normally.
    real_commit = session.commit
    commit_calls = {"n": 0}

    async def flaky_commit():
        commit_calls["n"] += 1
        if commit_calls["n"] == 3:
            raise RuntimeError("condense commit boom")
        return await real_commit()

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 0), \
         patch("applire.services.cover_letter_pdf.render_pdf", mock_render_pdf), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=known_report), \
         patch.object(session, "commit", flaky_commit):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    session.expire_all()
    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready", (
        f"a condense-commit failure must still flip the letter to ready, got {cl.status!r}"
    )
    # #564: see test_letter_condense_llm_failure_keeps_original_letter_ready's
    # comment — the FIRST successful compose (before the condense pass ever
    # runs) injects the generic Anrede floor, since _stub_letter_data()'s
    # body.paragraphs carries none. That composed state, not the raw
    # pre-compose draft, is "the ORIGINAL letter_data" this rollback restores.
    from applire.templates.labels import cover_letter_labels

    assert cl.letter_data["body"]["paragraphs"] == [
        cover_letter_labels("de")["salutation"],
        *letter_raw["body"]["paragraphs"],
    ], (
        "the condensed half-state must be rolled back — the ORIGINAL (composed) "
        f"letter_data must be persisted, got {cl.letter_data['body']['paragraphs']!r}"
    )


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


@pytest.mark.asyncio
async def test_get_cover_letter_ats_report_malformed_degrades_to_null(db_with_cover_letter):
    """E037 PQ #2 hardening (letter twin): a non-conforming stored ats_report degrades to
    report=null rather than raising an HTTP 500."""
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import get_cover_letter_ats_report

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]

    cl = await session.get(GeneratedCoverLetter, cl_id)
    cl.ats_report = {"document": 99, "checks": "not-a-list"}
    await session.commit()

    response = await get_cover_letter_ats_report(cl_id, session)
    assert response.document_id == cl_id
    assert response.report is None, "malformed stored report must degrade to report=None"
    assert response.status == "ready"


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
    profile = make_master_profile(
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
    profile = make_master_profile(
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


# ---------------------------------------------------------------------------
# #177 / ADR-051 §6 amended — letter measure-and-condense guarantee
#
# Letters get the CV's guarantee shape: measure the real render, ONE bounded
# condense-regenerate routed back through the grounding review, audit as the
# honest backstop. Unlike the CV there is no deterministic bullet-cut model
# for prose, so the condense pass is a scoped LLM rewrite (ADR-approved
# deviation) — bounded to exactly one iteration, never a loop.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_overrun_triggers_one_condense_regenerate(db_with_cover_letter):
    """2-page smoke render -> exactly ONE condense generation -> re-render; the
    condense rewrite RE-ENTERS the terminal review (#539: chain
    ``letter_terminal_review``, shared budget — the ``cover_letter_condense``
    chain with its own loop and its own guard-tail copy is retired), and the
    guards/date are re-applied via the single composition site."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    initial_raw = _stub_letter_data()
    condensed_raw = _stub_letter_data()
    known_report = _make_ats_report("cover_letter")

    mock_provider = AsyncMock()
    mock_provider.aparse_json = AsyncMock(side_effect=[initial_raw, condensed_raw])

    review_calls: list = []

    async def fake_review(**kwargs):
        review_calls.append(kwargs.get("chain_id"))
        return kwargs["draft"]

    mock_render_pdf = AsyncMock(side_effect=[b"%PDF-2page", b"%PDF-1page"])
    mock_extract = MagicMock(side_effect=[("text", 2), ("text", 1)])

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter_pdf.render_pdf", mock_render_pdf), \
         patch("applire.services.ats_audit.extract_text_and_pages", mock_extract), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=known_report):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    assert mock_render_pdf.call_count == 2, (
        f"expected 2 renders (smoke + condense re-render), got {mock_render_pdf.call_count}"
    )
    # Exactly one condense GENERATION (initial writer + condense rewrite).
    # The Oracle's pre-grading sentence-triage self-audit call (ADR-068) is
    # not a condense call — count only the others.
    non_triage_calls = [
        c
        for c in mock_provider.aparse_json.call_args_list
        if "sentence triage" not in (c.kwargs.get("system") or "").lower()
    ]
    assert len(non_triage_calls) == 2, (
        f"expected exactly one condense generation, got {len(non_triage_calls) - 1}"
    )
    # ... and its output re-enters the SAME terminal review — never the retired
    # cover_letter_condense chain with its own retry budget.
    assert review_calls.count("cover_letter_condense") == 0, (
        f"the cover_letter_condense chain is retired (#539), got {review_calls}"
    )
    assert review_calls.count("letter_terminal_review") == 1, (
        f"the condense rewrite must re-enter the terminal review, got {review_calls}"
    )

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready"
    assert cl.letter_data["recipient"]["date"], "date must be re-injected post-condense"


@pytest.mark.asyncio
async def test_letter_overrun_with_section_overrides_skips_condense(db_with_cover_letter):
    """A CL with user section overrides must never be auto-condensed — the user's
    own edits win (ADR-051 seam); the audit still reports honestly."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    cl = await session.get(GeneratedCoverLetter, cl_id)
    cl.section_overrides = {"body": "User-edited closing paragraph."}
    await session.commit()

    initial_raw = _stub_letter_data()
    known_report = _make_ats_report("cover_letter")

    mock_provider = AsyncMock()
    mock_provider.aparse_json = AsyncMock(return_value=initial_raw)

    review_calls: list = []

    async def fake_review(**kwargs):
        review_calls.append(kwargs.get("chain_id"))
        return kwargs["draft"]

    mock_render_pdf = AsyncMock(return_value=b"%PDF-2page")
    mock_extract = MagicMock(return_value=("text", 2))

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter_pdf.render_pdf", mock_render_pdf), \
         patch("applire.services.ats_audit.extract_text_and_pages", mock_extract), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=known_report):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    assert mock_render_pdf.call_count == 1, (
        f"expected exactly 1 render (no condense re-render), got {mock_render_pdf.call_count}"
    )
    assert review_calls.count("cover_letter_condense") == 0, (
        f"expected zero condense-chain reviews, got {review_calls}"
    )
    # The Oracle's pre-grading sentence-triage self-audit call (ADR-068
    # amended 2026-08-08) is not a condense call — count only the others.
    non_triage_calls = [
        c
        for c in mock_provider.aparse_json.call_args_list
        if "sentence triage" not in (c.kwargs.get("system") or "").lower()
    ]
    assert len(non_triage_calls) == 1, "the LLM must not be called again for condense"
    # #539: the measure itself now always runs (the terminal review carries the
    # real render measure as context) — what the override seam forbids is the
    # CONDENSE, i.e. the second LLM call and the second render, both asserted
    # above. The pre-#539 `mock_extract.assert_not_called()` pinned the old
    # gate's position, not the guarantee.
    assert mock_extract.call_count == 1

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready"


# ---------------------------------------------------------------------------
# #563 part D — the terminal review's outcome reaches the PERSISTED report
# (the seam test for `_update_ats_report`'s call site, not just the audit fn)
# ---------------------------------------------------------------------------


def _exhausted_outcome():
    from applire.services.review_issues import ReviewSettle
    from applire.services.terminal_review_outcome import settle_to_outcome

    return settle_to_outcome(
        ReviewSettle(
            path="exhausted",
            approved=False,
            blocking_issues=("the LucaNet project bullet omits the ownership limitation",),
            minor_issues=(),
            rounds=1,
            settled={},
        ),
        chain_id="cv_terminal_review",
    )


@pytest.mark.asyncio
async def test_terminal_review_outcome_reaches_the_persisted_cv_report(db_with_cv):
    """`_update_ats_report` is the single audit-and-persist seam all three CV doors
    share. #563(D) is inert unless the outcome survives THAT call, not merely
    `_audit_cv_text` — so this drives the service function, not the audit helper."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _update_ats_report

    ctx = db_with_cv
    session = ctx["db"]
    record = await session.get(GeneratedCV, ctx["cv_id"])

    with patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)):
        await _update_ats_report(record, session, terminal_review=_exhausted_outcome())

    checks = {c["id"]: c for c in record.ats_report["checks"]}
    assert "terminal-review" in checks, "the check must never be absent (#634 class)"
    assert checks["terminal-review"]["status"] == "fail"
    assert "LucaNet" in checks["terminal-review"]["details"]


@pytest.mark.asyncio
async def test_a_re_audit_door_cannot_launder_an_exhausted_terminal_review(db_with_cv):
    """The section-editor and agent-authored doors reach the same seam with NO terminal
    review of their own. Recomputing `not_applicable` there would turn a document that
    shipped on an exhausted review into one that reads as cleanly audited after any
    later edit — so the previously persisted check is re-emitted verbatim."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _update_ats_report

    ctx = db_with_cv
    session = ctx["db"]
    record = await session.get(GeneratedCV, ctx["cv_id"])

    with patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)):
        await _update_ats_report(record, session, terminal_review=_exhausted_outcome())
        first = {c["id"]: c for c in record.ats_report["checks"]}["terminal-review"]
        # The re-audit door: same seam, no fresh outcome.
        await _update_ats_report(record, session)

    carried = {c["id"]: c for c in record.ats_report["checks"]}["terminal-review"]
    assert carried["status"] == "fail"
    assert carried["details"] == first["details"]
