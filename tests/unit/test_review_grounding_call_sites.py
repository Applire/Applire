# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-090 clause 4 — seam tests for the FOUR call sites that must pass
``vault_index=grounding_vault_index(profile_json)`` into the ATS audit engine:

1. ``services/cv.py::_update_ats_report`` -> ``_audit_cv_text`` (PDF report)
2. ``services/cv.py::_update_ats_report`` -> ``audit_cv_docx`` (.docx report)
3. ``services/cover_letter.py::_update_ats_report_letter`` -> ``audit_cover_letter`` (PDF)
4. ``services/cover_letter.py::_update_ats_report_letter`` -> ``audit_cover_letter_docx`` (.docx)

Each is a NAMED test that drives the real ``_update_ats_report`` /
``_update_ats_report_letter`` seam over an in-memory SQLite DB with a real
``MasterProfile`` row (a skill "AI Governance"), and asserts the audit
ENGINE at that one site received a non-``None`` ``vault_index`` that grounds
"AI governance" via ``ground_skill_claim`` — reverting the wiring at any ONE
call site must turn only that call site's own named test red.

Patch targets mirror ``tests/unit/test_docx_ats_report_persistence.py`` and
its letter twin (``test_docx_ats_report_persistence_letter.py``):

* CV PDF site: ``applire.services.ats_audit._audit_cv_text`` — imported
  LOCALLY inside ``_update_ats_report``'s own try block, so patching the
  origin module's attribute is picked up on every call.
* CV .docx site: ``applire.services.office_export.extract._audit_cv_text`` —
  bound into ``office_export.extract``'s OWN module namespace at THAT
  module's import time (a *different* binding from the one above); patching
  the origin does not reach it.
* Letter PDF site: ``applire.services.ats_audit.audit_cover_letter`` —
  also a local import inside ``_update_ats_report_letter``'s try block.
* Letter .docx site: ``applire.services.office_export.extract._audit_letter_text``
  — same module-namespace-binding reason as the CV .docx site.

The site NOT under test in each pairing is stubbed (not spied) with a valid
``ATSReport`` so its real render/audit machinery never runs and can never
fail the test for an unrelated reason.
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

def _profile_json(skill_name: str | None) -> dict:
    """A minimal, valid MasterProfileData-shaped dict. ``skill_name`` (e.g.
    "AI Governance") becomes a vault skill entry when given; a profile with
    no matching skill is built by passing ``None`` (falls back to an
    unrelated skill so the vault is non-empty either way)."""
    skills = (
        [{"name": skill_name, "level": None, "category": "technical"}]
        if skill_name
        else [{"name": "Python", "level": None, "category": "technical"}]
    )
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
        "skills": skills,
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
            "company": "Acme GmbH",
            "date": "11. Juni 2026",
        },
        "body": {
            "paragraphs": [
                "ich bewerbe mich hiermit um die Stelle als Python Developer.",
                "Mit fünf Jahren Erfahrung in der Backend-Entwicklung bringe ich alle geforderten Kenntnisse mit.",
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
# SQLite DB fixtures (mirrors test_docx_ats_report_persistence*.py)
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
    """User -> Job -> Profile (skill "AI Governance") -> GeneratedCV (ready)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV

    user_id = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
    job_id = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
    profile_id = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
    cv_id = uuid.UUID("00000000-0000-0000-0000-0000000000c5")

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
        email="grounding-cv-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="grounding_cv_abc123",
        raw_text="AI Governance role",
        role_title="AI Governance Lead",
        required_skills=["AI Governance"],
        nice_to_have_skills=[],
        keywords=["AI Governance"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="en",
    )
    profile = make_master_profile(
        id=profile_id,
        profile_json=_profile_json("AI Governance"),
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
    """User -> Job -> Profile (skill "AI Governance") -> GeneratedCoverLetter (ready)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    user_id = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
    job_id = uuid.UUID("00000000-0000-0000-0000-0000000000d2")
    profile_id = uuid.UUID("00000000-0000-0000-0000-0000000000d3")
    cl_id = uuid.UUID("00000000-0000-0000-0000-0000000000d5")

    user = User(
        id=user_id,
        email="grounding-letter-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="grounding_letter_abc123",
        raw_text="AI Governance role",
        role_title="AI Governance Lead",
        required_skills=["AI Governance"],
        nice_to_have_skills=[],
        keywords=["AI Governance"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="en",
    )
    profile = make_master_profile(
        id=profile_id,
        profile_json=_profile_json("AI Governance"),
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
async def test_cv_pdf_audit_receives_vault_index(db_with_cv):
    from applire.models.cv import GeneratedCV
    from applire.services.oracle.matchers.grounding import ground_skill_claim

    ctx = db_with_cv
    session = ctx["db"]
    record = await session.get(GeneratedCV, ctx["cv_id"])

    captured: dict = {}

    def _spy(text, tailored, keywords, ledger=None, **kwargs):
        captured["vault_index"] = kwargs.get("vault_index")
        return _make_report("cv")

    with patch("applire.services.cv.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 1)), \
         patch("applire.services.ats_audit._audit_cv_text", side_effect=_spy), \
         patch("applire.services.office_export.extract._audit_cv_text", return_value=_make_report("cv")):
        from applire.services.cv import _update_ats_report
        await _update_ats_report(record, session)

    vault_index = captured.get("vault_index")
    assert vault_index is not None, (
        "the CV PDF audit call site (_audit_cv_text via _update_ats_report) "
        "did not receive a vault_index"
    )
    assert ground_skill_claim("AI governance", vault_index) is not None, (
        "vault_index passed to the CV PDF audit site does not ground 'AI governance'"
    )


# ---------------------------------------------------------------------------
# Call site 2: services/cv.py::_update_ats_report -> audit_cv_docx (.docx)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cv_docx_audit_receives_vault_index(db_with_cv):
    from applire.models.cv import GeneratedCV
    from applire.services.oracle.matchers.grounding import ground_skill_claim

    ctx = db_with_cv
    session = ctx["db"]
    record = await session.get(GeneratedCV, ctx["cv_id"])

    captured: dict = {}

    def _spy(text, tailored, keywords, ledger=None, **kwargs):
        captured["vault_index"] = kwargs.get("vault_index")
        return _make_report("cv")

    with patch("applire.services.cv.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 1)), \
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_report("cv")), \
         patch("applire.services.office_export.extract._audit_cv_text", side_effect=_spy):
        from applire.services.cv import _update_ats_report
        await _update_ats_report(record, session)

    vault_index = captured.get("vault_index")
    assert vault_index is not None, (
        "the CV .docx audit call site (audit_cv_docx via _update_ats_report) "
        "did not receive a vault_index"
    )
    assert ground_skill_claim("AI governance", vault_index) is not None, (
        "vault_index passed to the CV .docx audit site does not ground 'AI governance'"
    )


# ---------------------------------------------------------------------------
# Call site 3: services/cover_letter.py::_update_ats_report_letter
#              -> audit_cover_letter (PDF)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_pdf_audit_receives_vault_index(db_with_cover_letter):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.oracle.matchers.grounding import ground_skill_claim

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])

    captured: dict = {}

    def _spy(pdf_bytes, letter_data, keywords, ledger=None, **kwargs):
        captured["vault_index"] = kwargs.get("vault_index")
        return _make_report("cover_letter")

    with patch("applire.services.cover_letter.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", side_effect=_spy), \
         patch("applire.services.office_export.extract._audit_letter_text", return_value=_make_report("cover_letter")):
        from applire.services.cover_letter import _update_ats_report_letter
        await _update_ats_report_letter(cl, session)

    vault_index = captured.get("vault_index")
    assert vault_index is not None, (
        "the letter PDF audit call site (audit_cover_letter via "
        "_update_ats_report_letter) did not receive a vault_index"
    )
    assert ground_skill_claim("AI governance", vault_index) is not None, (
        "vault_index passed to the letter PDF audit site does not ground 'AI governance'"
    )


# ---------------------------------------------------------------------------
# Call site 4: services/cover_letter.py::_update_ats_report_letter
#              -> audit_cover_letter_docx (.docx)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_docx_audit_receives_vault_index(db_with_cover_letter):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.oracle.matchers.grounding import ground_skill_claim

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])

    captured: dict = {}

    def _spy(text, letter_data, keywords, ledger=None, **kwargs):
        captured["vault_index"] = kwargs.get("vault_index")
        return _make_report("cover_letter")

    with patch("applire.services.cover_letter.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_report("cover_letter")), \
         patch("applire.services.office_export.extract._audit_letter_text", side_effect=_spy):
        from applire.services.cover_letter import _update_ats_report_letter
        await _update_ats_report_letter(cl, session)

    vault_index = captured.get("vault_index")
    assert vault_index is not None, (
        "the letter .docx audit call site (audit_cover_letter_docx via "
        "_update_ats_report_letter) did not receive a vault_index"
    )
    assert ground_skill_claim("AI governance", vault_index) is not None, (
        "vault_index passed to the letter .docx audit site does not ground 'AI governance'"
    )


# ---------------------------------------------------------------------------
# End-to-end-ish: the real _audit_cv_text (not spied) on the CV PDF site,
# with a ledger row that only the vault-index widening can resolve.
# ---------------------------------------------------------------------------

async def _seed_grounding_cv(db, *, skill_name: str | None, suffix: str):
    """Independent Job/Profile/GeneratedCV chain — no fixture reuse, since
    this test needs TWO profiles (with/without the vault skill) that must
    not collide with each other's ids."""
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV

    job_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    cv_id = uuid.uuid4()

    job = JobAnalysis(
        id=job_id,
        raw_text_hash=f"grounding-e2e-{suffix}",
        raw_text="IT Data & AI Governance role",
        role_title="IT Data & AI Governance Lead",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=["IT Data & AI Governance"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="en",
    )
    profile = make_master_profile(
        id=profile_id,
        profile_json=_profile_json(skill_name),
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
        content_snapshot={
            "introduction": "Erfahrener Python-Entwickler",
            "positions": [],
            "skills": ["Python"],
        },
        section_overrides=None,
        ats_report=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([job, profile, cv])
    await db.commit()
    return cv


_UNCLAIMABLE_LEDGER = [
    {
        "concept": "IT Data & AI Governance",
        "surface_forms": ["IT Data & AI Governance", "AI governance"],
        "claimable": False,
        "status": "gap",
    }
]


@pytest.mark.asyncio
async def test_cv_pdf_grounding_widens_present_unsupported(db):
    """ADR-090 clause 4's own worked example: a keyword flagged
    present_unsupported by the literal-only rule (the vault never says the
    JD's phrase "IT Data & AI Governance" verbatim) is widened OUT of that
    bucket once the candidate's own vault carries the matched surface form
    ("AI governance") as a skill — and stays IN it when the vault does not.
    Runs the REAL `_audit_cv_text` (not spied); only `_latest_keyword_ledger`
    and the render seam are patched."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _update_ats_report

    grounded_cv = await _seed_grounding_cv(db, skill_name="AI Governance", suffix="grounded")
    ungrounded_cv = await _seed_grounding_cv(db, skill_name=None, suffix="ungrounded")

    async def _run(cv):
        with patch("applire.services.cv.get_provider", side_effect=RuntimeError("no provider in test")), \
             patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
             patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
             patch(
                 "applire.services.ats_audit.extract_text_and_pages",
                 return_value=("Background in AI governance across regulated programmes.", 1),
             ), \
             patch(
                 "applire.services.cv._latest_keyword_ledger",
                 new=AsyncMock(return_value=_UNCLAIMABLE_LEDGER),
             ), \
             patch(
                 "applire.services.office_export.extract._audit_cv_text",
                 return_value=_make_report("cv"),
             ):
            await _update_ats_report(cv, db)

    await _run(grounded_cv)

    grounded = await db.get(GeneratedCV, grounded_cv.id)
    assert grounded.ats_report["keywords"]["present_unsupported"] == [], (
        f"expected the vault-grounded run to clear present_unsupported, got "
        f"{grounded.ats_report['keywords']['present_unsupported']!r}"
    )

    await _run(ungrounded_cv)

    ungrounded = await db.get(GeneratedCV, ungrounded_cv.id)
    assert ungrounded.ats_report["keywords"]["present_unsupported"] == [
        "IT Data & AI Governance"
    ], (
        f"expected the ungrounded run to keep flagging the keyword, got "
        f"{ungrounded.ats_report['keywords']['present_unsupported']!r}"
    )
    assert ungrounded.ats_report["keywords"]["present_unsupported_matches"] == {
        "IT Data & AI Governance": [{"form": "AI governance", "stem": False}]
    }, (
        f"unexpected present_unsupported_matches: "
        f"{ungrounded.ats_report['keywords']['present_unsupported_matches']!r}"
    )
