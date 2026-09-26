# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-090 amendment 2026-09-26 (WP-R) — seam tests for the FOUR call sites
that must pass ``non_claim=non_claim_names_for_job(job)`` into the ATS audit
engine:

1. ``services/cv.py::_update_ats_report`` -> ``_audit_cv_text`` (PDF report)
2. ``services/cv.py::_update_ats_report`` -> ``audit_cv_docx`` (.docx report)
3. ``services/cover_letter.py::_update_ats_report_letter`` -> ``audit_cover_letter`` (PDF)
4. ``services/cover_letter.py::_update_ats_report_letter`` -> ``audit_cover_letter_docx`` (.docx)

Each is a NAMED test that drives the real ``_update_ats_report`` /
``_update_ats_report_letter`` seam over an in-memory SQLite DB with a real
``JobAnalysis`` row (``role_title="Leiter Operations (m/w/d)"``,
``company_name="Rheinwerk Verpackungen GmbH"``), and asserts the audit
ENGINE at that one site received a non-``None`` ``non_claim`` whose
``titles``/``employers`` carry the posting's own names — reverting the
wiring at any ONE call site must turn only that call site's own named test
red.

Patch targets mirror ``tests/unit/test_review_grounding_call_sites.py``
(itself mirroring ``tests/unit/test_docx_ats_report_persistence.py`` and its
letter twin):

* CV PDF site: ``applire.services.ats_audit._audit_cv_text`` — imported
  LOCALLY inside ``_update_ats_report``'s own try block, so patching the
  origin module's attribute is picked up on every call.
* CV .docx site: ``applire.services.office_export.extract._audit_cv_text`` —
  bound into ``office_export.extract``'s OWN module namespace at THAT
  module's import time (a *different* binding from the one above); patching
  the origin does not reach it.
* Letter PDF site: ``applire.services.ats_audit.audit_cover_letter`` —
  also a local import inside ``_update_ats_report_letter``'s try block. Note
  this spy sees the names BEFORE ``_audit_letter_text`` adds the letter's
  own ``recipient.company`` — so it is asserted on title/employer only.
* Letter .docx site: ``applire.services.office_export.extract._audit_letter_text``
  — same module-namespace-binding reason as the CV .docx site.

The site NOT under test in each pairing is stubbed (not spied) with a valid
``ATSReport`` so its real render/audit machinery never runs and can never
fail the test for an unrelated reason.

A fifth test drives the real (unpatched) ``_audit_letter_text`` directly to
prove the letter's OWN addition — the recipient's company joining the
posting's names — actually changes the unsupported-claim verdict.
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
# Helpers — LOCAL to this file (this test suite's own convention).
# ---------------------------------------------------------------------------

def _profile_json() -> dict:
    """A minimal, valid MasterProfileData-shaped dict."""
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
        "education": [],
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
            "company": "Rheinwerk Verpackungen GmbH",
            "date": "11. Juni 2026",
        },
        "body": {
            "paragraphs": [
                "ich bewerbe mich hiermit um die Stelle als Leiter Operations.",
                "Mit fünf Jahren Erfahrung in der Betriebsleitung bringe ich alle geforderten Kenntnisse mit.",
            ]
        },
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Max Mustermann"},
    }


def _make_report(document: str) -> "ATSReport":  # type: ignore[name-defined]
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    return ATSReport(
        document=document,
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
    )


# ---------------------------------------------------------------------------
# SQLite DB fixtures (mirrors test_review_grounding_call_sites.py)
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
    """User -> Job (role "Leiter Operations (m/w/d)" at "Rheinwerk
    Verpackungen GmbH") -> Profile -> GeneratedCV (ready)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV

    user_id = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
    job_id = uuid.UUID("00000000-0000-0000-0000-0000000000e2")
    profile_id = uuid.UUID("00000000-0000-0000-0000-0000000000e3")
    cv_id = uuid.UUID("00000000-0000-0000-0000-0000000000e5")

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
        email="non-claim-cv-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="non_claim_cv_abc123",
        raw_text="Leiter Operations role",
        role_title="Leiter Operations (m/w/d)",
        company_name="Rheinwerk Verpackungen GmbH",
        required_skills=["Operations"],
        nice_to_have_skills=[],
        keywords=["Operations"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    profile = make_master_profile(
        id=profile_id,
        profile_json=_profile_json(),
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

    return {"db": db, "cv_id": cv_id, "job_id": job_id, "profile_id": profile_id}


@pytest_asyncio.fixture
async def db_with_cover_letter(db):
    """User -> Job (role "Leiter Operations (m/w/d)" at "Rheinwerk
    Verpackungen GmbH") -> Profile -> GeneratedCoverLetter (ready)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    user_id = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
    job_id = uuid.UUID("00000000-0000-0000-0000-0000000000f2")
    profile_id = uuid.UUID("00000000-0000-0000-0000-0000000000f3")
    cl_id = uuid.UUID("00000000-0000-0000-0000-0000000000f5")

    user = User(
        id=user_id,
        email="non-claim-letter-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="non_claim_letter_abc123",
        raw_text="Leiter Operations role",
        role_title="Leiter Operations (m/w/d)",
        company_name="Rheinwerk Verpackungen GmbH",
        required_skills=["Operations"],
        nice_to_have_skills=[],
        keywords=["Operations"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    profile = make_master_profile(
        id=profile_id,
        profile_json=_profile_json(),
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
        docx_ats_report=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([user, job, profile, cl])
    await db.commit()

    return {"db": db, "cl_id": cl_id, "job_id": job_id, "profile_id": profile_id}


# ---------------------------------------------------------------------------
# Call site 1: services/cv.py::_update_ats_report -> _audit_cv_text (PDF)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cv_pdf_audit_receives_non_claim_names(db_with_cv):
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]
    record = await session.get(GeneratedCV, ctx["cv_id"])

    captured: dict = {}

    def _spy(text, tailored, keywords, ledger=None, **kwargs):
        captured["non_claim"] = kwargs.get("non_claim")
        return _make_report("cv")

    with patch("applire.services.cv.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 1)), \
         patch("applire.services.ats_audit._audit_cv_text", side_effect=_spy), \
         patch("applire.services.office_export.extract._audit_cv_text", return_value=_make_report("cv")):
        from applire.services.cv import _update_ats_report
        await _update_ats_report(record, session)

    non_claim = captured.get("non_claim")
    assert non_claim is not None, (
        "the CV PDF audit call site (_audit_cv_text via _update_ats_report) "
        "did not receive a non_claim"
    )
    assert "leiter operations" in non_claim.titles, non_claim.titles
    assert "rheinwerk verpackungen" in non_claim.employers, non_claim.employers


# ---------------------------------------------------------------------------
# Call site 2: services/cv.py::_update_ats_report -> audit_cv_docx (.docx)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cv_docx_audit_receives_non_claim_names(db_with_cv):
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]
    record = await session.get(GeneratedCV, ctx["cv_id"])

    captured: dict = {}

    def _spy(text, tailored, keywords, ledger=None, **kwargs):
        captured["non_claim"] = kwargs.get("non_claim")
        return _make_report("cv")

    with patch("applire.services.cv.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 1)), \
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_report("cv")), \
         patch("applire.services.office_export.extract._audit_cv_text", side_effect=_spy):
        from applire.services.cv import _update_ats_report
        await _update_ats_report(record, session)

    non_claim = captured.get("non_claim")
    assert non_claim is not None, (
        "the CV .docx audit call site (audit_cv_docx via _update_ats_report) "
        "did not receive a non_claim"
    )
    assert "leiter operations" in non_claim.titles, non_claim.titles
    assert "rheinwerk verpackungen" in non_claim.employers, non_claim.employers


# ---------------------------------------------------------------------------
# Call site 3: services/cover_letter.py::_update_ats_report_letter
#              -> audit_cover_letter (PDF)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_pdf_audit_receives_non_claim_names(db_with_cover_letter):
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])

    captured: dict = {}

    def _spy(pdf_bytes, letter_data, keywords, ledger=None, **kwargs):
        captured["non_claim"] = kwargs.get("non_claim")
        return _make_report("cover_letter")

    with patch("applire.services.cover_letter.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", side_effect=_spy), \
         patch("applire.services.office_export.extract._audit_letter_text", return_value=_make_report("cover_letter")):
        from applire.services.cover_letter import _update_ats_report_letter
        await _update_ats_report_letter(cl, session)

    non_claim = captured.get("non_claim")
    # This spy patches applire.services.ats_audit.audit_cover_letter itself,
    # so it sees the names BEFORE _audit_letter_text adds recipient.company —
    # assert title/employer only (see module docstring).
    assert non_claim is not None, (
        "the letter PDF audit call site (audit_cover_letter via "
        "_update_ats_report_letter) did not receive a non_claim"
    )
    assert "leiter operations" in non_claim.titles, non_claim.titles
    assert "rheinwerk verpackungen" in non_claim.employers, non_claim.employers


# ---------------------------------------------------------------------------
# Call site 4: services/cover_letter.py::_update_ats_report_letter
#              -> audit_cover_letter_docx (.docx)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_docx_audit_receives_non_claim_names(db_with_cover_letter):
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])

    captured: dict = {}

    def _spy(text, letter_data, keywords, ledger=None, **kwargs):
        captured["non_claim"] = kwargs.get("non_claim")
        return _make_report("cover_letter")

    with patch("applire.services.cover_letter.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_report("cover_letter")), \
         patch("applire.services.office_export.extract._audit_letter_text", side_effect=_spy):
        from applire.services.cover_letter import _update_ats_report_letter
        await _update_ats_report_letter(cl, session)

    non_claim = captured.get("non_claim")
    assert non_claim is not None, (
        "the letter .docx audit call site (audit_cover_letter_docx via "
        "_update_ats_report_letter) did not receive a non_claim"
    )
    assert "leiter operations" in non_claim.titles, non_claim.titles
    assert "rheinwerk verpackungen" in non_claim.employers, non_claim.employers


# ---------------------------------------------------------------------------
# The letter's own addition: recipient.company joins the posting's names.
# Runs the REAL (unpatched) _audit_letter_text directly.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_audit_adds_recipient_company_to_the_names():
    from applire.services.ats_audit import _audit_letter_text, non_claim_names

    text = "Ich habe fünf Jahre bei der Arnold Antriebstechnik GmbH als Ingenieur gearbeitet."
    letter_data = {
        "recipient": {"company": "Arnold Antriebstechnik GmbH"},
        "body": {"paragraphs": []},
    }
    keywords = ["Antriebstechnik"]
    ledger = [
        {
            "concept": "Antriebstechnik",
            "surface_forms": ["Antriebstechnik"],
            "claimable": False,
            "status": "gap",
            "sources": ["required"],
            "fit_weight": 1.0,
            "evidence": "",
        }
    ]

    grounded_report = _audit_letter_text(
        text,
        letter_data,
        keywords,
        ledger,
        non_claim=non_claim_names("Senior Controller (m/w/d)", []),
    )
    assert grounded_report.keywords.present_unsupported == [], (
        f"expected the recipient's own company name to clear "
        f"present_unsupported, got {grounded_report.keywords.present_unsupported!r}"
    )
    assert "Antriebstechnik" in grounded_report.keywords.present

    baseline_report = _audit_letter_text(
        text,
        letter_data,
        keywords,
        ledger,
        non_claim=None,
    )
    assert baseline_report.keywords.present_unsupported == ["Antriebstechnik"], (
        f"expected the non_claim=None baseline to keep flagging the keyword, "
        f"got {baseline_report.keywords.present_unsupported!r}"
    )
