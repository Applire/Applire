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

"""E057/US296 (#637), ADR-079 clause 8 — persisting the .docx export's OWN ATS
report on ``GeneratedCV.docx_ats_report`` (TDD).

``_update_ats_report`` (``services/cv.py``) is the single audit-and-persist
seam reached by three call sites; every one of the three must independently
persist ``docx_ats_report`` — reverting the wiring at any ONE call site must
turn only that call site's own named test red:

1. ``test_generation_persists_docx_ats_report`` — the generation path
   (``_render_cv_background`` -> the subject-identity loop -> ``cv.py:~3087``).
2. ``test_section_editor_reaudit_persists_docx_ats_report`` — the
   section-editor re-audit (``_update_ats_report_by_id``, ``cv.py:~4014``).
3. ``test_agent_authored_cv_persists_docx_ats_report`` — the agent door
   (``render_agent_cv``, ``cv.py:~4196``).

Plus the property the whole design exists for (ADR-079 clause 8 / the
task's "audit the DELIVERED artefact"):

4. ``test_docx_audit_reflects_section_overrides`` — the audited TEXT must
   come from the OVERRIDE-APPLIED document (what ``get_cv_docx`` actually
   serves), never the raw pre-override ``tailored_data``.

And ``_update_ats_report``'s own docstring contract ("an audit failure must
NEVER fail or alter generation status" / "deliberately wipes any previous
report on error"), extended to the new column:

5. ``test_docx_audit_engine_error_leaves_report_null_and_status_ready`` — the
   direct-call demonstration: the docx block's own try/except swallows the
   error.
6. ``test_docx_audit_engine_error_via_generation_path_leaves_status_ready`` —
   the end-to-end demonstration: with NO try/except of its own around the
   new block, ``_render_cv_background``'s outer handler would catch the
   propagated exception and flip status to ``'failed'``, discarding an
   otherwise-successful generation over a measurement-only audit failure.
   This is the test that specifically catches "generation status changed".

Never mocks ``render_cv_docx`` itself — it is a pure, fast, no-I/O function
(ADR-079 clause 2), so it runs for real in every test here; only the audit
ENGINE is mocked. Note the mock TARGET: ``_audit_cv_text`` as imported (`from
applire.services.ats_audit import _audit_cv_text`) into
``applire.services.office_export.extract``'s OWN module namespace at that
module's import time — a *different* binding from
``applire.services.ats_audit._audit_cv_text`` itself, which is what
``tests/unit/test_ats_report_persistence.py`` patches for the PDF audit.
Patching the origin module does not reach a name another module already
imported by value; every test below patches
``applire.services.office_export.extract._audit_cv_text`` specifically for
this reason.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Helpers — shared stubs (kept LOCAL to this file, per this test suite's own
# convention: test_render_agent.py / test_ats_report_persistence.py each
# define their own rather than importing one another's).
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


def _make_pdf_ats_report() -> "ATSReport":  # type: ignore[name-defined]
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    return ATSReport(
        document="cv",
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
    )


def _make_docx_ats_report() -> "ATSReport":  # type: ignore[name-defined]
    """Distinguishable from `_make_pdf_ats_report` (passed=88, not 1) so a
    test can prove docx_ats_report reflects THIS report, not a stray read of
    the PDF's ats_report column."""
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    return ATSReport(
        document="cv",
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=88,
        failed=0,
    )


# ---------------------------------------------------------------------------
# SQLite DB fixtures (mirrors test_ats_report_persistence.py's db/db_with_cv)
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
    """Insert a full chain: User -> Job -> Profile -> GeneratedCV (ready, no reports)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.cv import GeneratedCV

    user_id = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
    job_id = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
    profile_id = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
    cv_id = uuid.UUID("00000000-0000-0000-0000-0000000000a5")

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
        email="docx-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="docx_abc123",
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
# Call site 1: generation path (_render_cv_background)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generation_persists_docx_ats_report(db_with_cv):
    """After _render_cv_background succeeds, docx_ats_report is populated
    AND distinct from ats_report — proving the generation path (cv.py:~3087)
    wires the new .docx audit block, not a re-read of the PDF one."""
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
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_cv_text", return_value=_make_docx_ats_report()):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    record = await session.get(GeneratedCV, cv_id)
    assert record.status == "ready", f"expected status 'ready', got {record.status!r}"
    assert record.ats_report is not None and record.ats_report["passed"] == 1, (
        "PDF ats_report must be unaffected by the new docx wiring"
    )
    assert record.docx_ats_report is not None, (
        "docx_ats_report should be populated after successful generation"
    )
    assert record.docx_ats_report["passed"] == 88, (
        f"docx_ats_report must reflect the DOCX audit engine, got {record.docx_ats_report}"
    )


# ---------------------------------------------------------------------------
# Call site 2: section-editor re-audit (_update_ats_report_by_id)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_section_editor_reaudit_persists_docx_ats_report(db_with_cv):
    """patch_cv_section enqueues one background task (_update_ats_report_by_id);
    executing it must recompute docx_ats_report too, not just ats_report."""
    from fastapi import BackgroundTasks
    from applire.models.cv import GeneratedCV
    from applire.services.cv_section_editor import patch_cv_section

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    bg = BackgroundTasks()

    with patch("applire.services.ats_audit._audit_cv_text", return_value=_make_pdf_ats_report()), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-patched")), \
         patch("applire.services.office_export.extract._audit_cv_text", return_value=_make_docx_ats_report()):
        await patch_cv_section(cv_id, "introduction", "Neues Profil", False, session, bg)

    assert len(bg.tasks) == 1, f"expected 1 background task, got {len(bg.tasks)}"

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_pdf_ats_report()), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-patched")), \
         patch("applire.services.office_export.extract._audit_cv_text", return_value=_make_docx_ats_report()):
        mock_session_local.return_value.__aenter__.return_value = session
        await bg.tasks[0]()

    record = await session.get(GeneratedCV, cv_id)
    assert record.docx_ats_report is not None, (
        "docx_ats_report should be recomputed after the section-editor re-audit"
    )
    assert record.docx_ats_report["passed"] == 88, (
        f"docx_ats_report should reflect the sentinel audit, got {record.docx_ats_report}"
    )


# ---------------------------------------------------------------------------
# Call site 3: agent door (render_agent_cv)
# ---------------------------------------------------------------------------

AGENT_PROFILE_JSON = {
    "personal_info": {"name": "Anna Bauer", "photo_url": "uploads/photos/anna.jpg"},
    "professional_summary": {"en": "Engineer focused on backend platforms."},
    "work_experience": [
        {
            "company": "Acme GmbH",
            "role": "Backend Engineer",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "achievements": ["Led a team of 12 engineers."],
        }
    ],
    "skills": [{"name": "Python"}],
}

AGENT_CV_CONTENT = {
    "contact": {
        "name": "Anna Bauer",
        "email": "anna@example.de",
        "location": "Berlin",
        "photo_url": "/etc/passwd",  # hostile — render_agent_cv must strip this
    },
    "summary": "Backend engineer with platform focus.",
    "work_history": [
        {
            "company": "Acme GmbH",
            "role": "Backend Engineer",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "bullets": ["Led a team of 12 engineers."],
        }
    ],
    "skills": ["Python"],
    "education": [],
    "languages": [],
    "projects": [],
    "certifications": [],
    "show_photo": True,
}


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.job import JobAnalysis

    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    db.add_all(
        [
            JobAnalysis(
                id=job_id,
                raw_text_hash="docx-render-agent",
                raw_text="Backend Engineer job",
                role_title="Backend Engineer",
                required_skills=["Python"],
                nice_to_have_skills=[],
                keywords=["Python"],
                seniority_level="senior",
                company_culture_signals=[],
                language_requirement="de",
            ),
            make_master_profile(
                id=profile_id,
                profile_json=AGENT_PROFILE_JSON,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db.commit()
    return {"db": db, "job_id": job_id, "profile_id": profile_id}


@pytest.mark.asyncio
async def test_agent_authored_cv_persists_docx_ats_report(seeded):
    """render_agent_cv's audit-only tail (cv.py:~4196) must persist
    docx_ats_report in the same commit as ats_report/truthfulness_report —
    "ready implies reports available" extended to the new column."""
    from applire.services.cv import render_agent_cv

    with patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("Anna Bauer Python", 3)), \
         patch("applire.services.office_export.extract._audit_cv_text", return_value=_make_docx_ats_report()):
        record = await render_agent_cv(
            dict(AGENT_CV_CONTENT), seeded["job_id"], seeded["db"], target_pages=1
        )

    assert record.origin == "agent"
    assert record.status == "ready"
    assert record.docx_ats_report is not None, (
        "docx_ats_report should be populated by the agent-authored re-audit"
    )
    assert record.docx_ats_report["passed"] == 88, (
        f"docx_ats_report must reflect the DOCX audit engine, got {record.docx_ats_report}"
    )


# ---------------------------------------------------------------------------
# The property the whole design exists for: audited bytes == delivered bytes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_docx_audit_reflects_section_overrides(db_with_cv):
    """ADR-079 clause 8 / #637: the persisted docx_ats_report must describe
    the OVERRIDE-APPLIED document get_cv_docx actually serves, never the raw
    pre-override tailored_data. Captures the TEXT the docx audit engine
    actually received (extracted from the REAL rendered .docx bytes, via the
    real render_cv_docx + extract_docx_text — nothing about the render or
    extraction is mocked) and asserts the override marker is present while
    the pre-override summary is gone.

    This is the property the task brief's mutation (i) must break: skipping
    apply_overrides_to_tailored before rendering the audited document would
    feed the audit engine the ORIGINAL summary instead."""
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    record = await session.get(GeneratedCV, cv_id)
    record.section_overrides = {"introduction": "MUTATION-MARKER-OVERRIDDEN-SUMMARY"}
    await session.commit()

    captured: dict = {}

    def fake_docx_audit(text, tailored, keywords, ledger=None, **kwargs):
        captured["text"] = text
        return _make_docx_ats_report()

    with patch("applire.services.cv.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_cv_text", side_effect=fake_docx_audit):
        from applire.services.cv import _update_ats_report
        await _update_ats_report(record, session)

    text = captured.get("text")
    assert text is not None, "the docx audit engine was never called"
    assert "MUTATION-MARKER-OVERRIDDEN-SUMMARY" in text, (
        "the audited .docx text does not contain the section override — the "
        "report was computed from a document that is not the one get_cv_docx "
        "actually serves"
    )
    assert "Erfahrener Python-Entwickler" not in text, (
        "the audited .docx text still contains the PRE-override summary — "
        "the override was not applied before rendering the audited document"
    )


# ---------------------------------------------------------------------------
# Contract: engine errors leave docx_ats_report NULL, never raise / alter status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_docx_audit_engine_error_leaves_report_null_and_status_ready(db_with_cv):
    """_update_ats_report's own docstring contract ("an audit failure must
    NEVER fail or alter generation status"; "deliberately wipes any previous
    report on error") extended to docx_ats_report: if the docx audit engine
    raises, docx_ats_report stays NULL, status is untouched, and the
    INDEPENDENT PDF-side ats_report is unaffected (the four report blocks
    are deliberately independent try blocks)."""
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]
    cv_id = ctx["cv_id"]

    record = await session.get(GeneratedCV, cv_id)

    with patch("applire.services.cv.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 2)), \
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_cv_text", side_effect=RuntimeError("docx audit boom")):
        from applire.services.cv import _update_ats_report
        await _update_ats_report(record, session)

    assert record.status == "ready", (
        f"a docx-audit engine error must never alter generation status, got {record.status!r}"
    )
    assert record.docx_ats_report is None, (
        "docx_ats_report must be NULL when the docx audit engine errors"
    )
    assert record.ats_report is not None and record.ats_report["passed"] == 1, (
        "the PDF-side ats_report must be unaffected by the docx audit's failure"
    )


@pytest.mark.asyncio
async def test_docx_audit_engine_error_via_generation_path_leaves_status_ready(db_with_cv):
    """The end-to-end demonstration of the same contract: _render_cv_background
    has NO handler of its own for a docx-audit error — the ONLY thing standing
    between a docx-audit exception and CVGenerationStatus.failed is
    _update_ats_report's own try/except around the new block. If that block's
    except clause stopped catching real errors, this exception would propagate
    out of the whole background task and _render_cv_background's outer
    `except Exception as exc: _record_generation_failure(record, exc)` would
    flip status to 'failed' and discard the (otherwise successful) generation
    over a measurement-only audit failure. This is the test that must catch
    "generation status changed", specifically — as opposed to the direct-call
    test above, which would only ever see an unhandled exception, never a
    caller with status-flipping behaviour to observe."""
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
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_cv_text", side_effect=RuntimeError("docx audit boom")):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    record = await session.get(GeneratedCV, cv_id)
    assert record.status == "ready", (
        "a docx-audit engine error reaching the generation path must NOT change "
        f"generation status to 'failed' — got {record.status!r}"
    )
    assert record.docx_ats_report is None
    assert record.ats_report is not None and record.ats_report["passed"] == 1
