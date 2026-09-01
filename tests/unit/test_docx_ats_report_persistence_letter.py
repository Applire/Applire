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

"""E057 adversarial review, Finding 3 (MEDIUM) — persisting the cover
letter's `.docx` export's OWN ATS report on
``GeneratedCoverLetter.docx_ats_report`` (TDD).

The letter twin of ``tests/unit/test_docx_ats_report_persistence.py``
(read that file's own module docstring for the full CV-side rationale;
not re-derived here). ``GeneratedCoverLetter.docx_ats_report`` has existed
in the schema since E057 task 1.1 (``models/cover_letter.py``'s own
comment: "Schema only at this point: no writer in
services/cover_letter.py populates it yet") — this file is TDD for the
writer that finally lands.

``_update_ats_report_letter`` (``services/cover_letter.py``) is the single
audit-and-persist seam reached by three call sites; every one of the three
must independently persist ``docx_ats_report``:

1. ``test_generation_persists_docx_ats_report_letter`` — the generation
   path (``_render_cover_letter_background``).
2. ``test_section_editor_reaudit_persists_docx_ats_report_letter`` — the
   section-editor re-audit (``_update_ats_report_letter_by_id``, enqueued
   by ``patch_cover_letter_section``).
3. ``test_agent_authored_letter_persists_docx_ats_report`` — the agent
   door (``render_agent_letter``).

Plus the property the whole design exists for:

4. ``test_letter_docx_audit_reflects_section_overrides`` — the audited
   TEXT must come from the OVERRIDE-APPLIED document (what
   ``get_cover_letter_docx`` actually serves), never the raw pre-override
   ``letter_data``. On the CV side this is guaranteed by sharing ONE
   preparation function (``_prepare_cv_docx_render``) between the download
   and the audit; the letter mount is
   ``_prepare_cover_letter_docx_render``, shared between
   ``get_cover_letter_docx`` and ``_update_ats_report_letter``'s new docx
   block — see that helper's own docstring for why it is NOT also folded
   into ``get_cover_letter_html`` (mirrors the CV side's own precedent and
   stated reason).

And the existing contract ("an audit failure must NEVER fail or alter
generation status" / "deliberately wipes any previous report on error"),
extended to the new column:

5. ``test_letter_docx_audit_engine_error_leaves_report_null_and_ready`` —
   the direct-call demonstration.
6. ``test_letter_docx_audit_engine_error_via_generation_path_leaves_status_
   ready`` — the end-to-end demonstration: with no try/except of its own
   around the new block, the outer generation handler would flip status to
   'failed' over a measurement-only audit failure.

Never mocks ``render_letter_docx`` itself — it is a pure, fast, no-I/O
function (ADR-079 clause 2), so it runs for real in every test here; only
the audit ENGINE is mocked. Mock TARGET, mirroring the CV file's own
documented reason: ``_audit_letter_text`` as imported into
``applire.services.office_export.extract``'s OWN module namespace at that
module's import time — a *different* binding from
``applire.services.ats_audit._audit_letter_text`` itself (patched by
``tests/unit/test_ats_report_persistence.py`` for the PDF audit, via the
higher-level ``audit_cover_letter``). Every test below patches
``applire.services.office_export.extract._audit_letter_text`` specifically
for this reason.
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
# Helpers — LOCAL to this file (this test suite's own convention: every
# sibling ATS-report test file defines its own stubs rather than importing
# another's — see test_docx_ats_report_persistence.py's own module docstring).
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


def _stub_letter_data() -> dict:
    """The RAW shape the LLM writer returns — `LetterData`-schema-shaped
    (`header`/`recipient`/`body`/`signature`, `signature` a NESTED dict),
    matching the real prompt schema's own contract, NOT
    ``tests/unit/test_ats_report_persistence.py``'s own ``_stub_letter_data``
    (that file's stub has `signature` as a bare STRING and three top-level
    keys — `subject`/`salutation`/`closing` — `LetterData` does not
    declare at all; harmless for its OWN suite, which only ever renders
    the raw dict through Jinja, never `LetterData.model_validate`, but a
    real `ValidationError` at this file's `_coerce_stored_letter_data`
    step, found running this file's own tests against the fix:
    ``cover_letter.py``'s own `_normalize_signature_closing` comment
    confirms "a non-dict, non-None signature is a legacy/unexpected shape
    ... production always uses the nested dict schema" — this stub matches
    that, and what `render_agent_letter`'s own `AGENT_LETTER_CONTENT`
    fixture (`backend/tests/unit/test_render_agent.py`) already uses)."""
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


def _make_pdf_ats_report() -> "ATSReport":  # type: ignore[name-defined]
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    return ATSReport(
        document="cover_letter",
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
    )


def _make_docx_ats_report() -> "ATSReport":  # type: ignore[name-defined]
    """Distinguishable from `_make_pdf_ats_report` (passed=88, not 1) so a
    test can prove docx_ats_report reflects THIS report, not a stray read
    of the PDF's ats_report column."""
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    return ATSReport(
        document="cover_letter",
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=88,
        failed=0,
    )


# ---------------------------------------------------------------------------
# SQLite DB fixtures (mirrors test_ats_report_persistence.py's
# db / db_with_cover_letter)
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
async def db_with_cover_letter(db):
    """User -> Job -> Profile -> GeneratedCoverLetter (ready, no reports)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus

    user_id = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
    job_id = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
    profile_id = uuid.UUID("00000000-0000-0000-0000-0000000000b3")
    cl_id = uuid.UUID("00000000-0000-0000-0000-0000000000b5")

    user = User(
        id=user_id,
        email="docx-letter-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="docx_letter_abc123",
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
        docx_ats_report=None,
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
# Call site 1: generation path (_render_cover_letter_background)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generation_persists_docx_ats_report_letter(db_with_cover_letter):
    """After _render_cover_letter_background succeeds, cl.docx_ats_report
    is populated AND distinct from ats_report — proving the generation
    path wires the new .docx audit block, not a re-read of the PDF one."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    letter_raw = _stub_letter_data()
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = letter_raw

    async def fake_review(**kwargs):
        return kwargs["draft"]

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_letter_text", return_value=_make_docx_ats_report()):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready", f"expected status 'ready', got {cl.status!r}"
    assert cl.ats_report is not None and cl.ats_report["passed"] == 1, (
        "PDF ats_report must be unaffected by the new docx wiring"
    )
    assert cl.docx_ats_report is not None, (
        "docx_ats_report should be populated after successful generation"
    )
    assert cl.docx_ats_report["passed"] == 88, (
        f"docx_ats_report must reflect the DOCX audit engine, got {cl.docx_ats_report}"
    )


# ---------------------------------------------------------------------------
# Call site 2: section-editor re-audit (patch_cover_letter_section)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_section_editor_reaudit_persists_docx_ats_report_letter(db_with_cover_letter):
    """patch_cover_letter_section enqueues one background task
    (_update_ats_report_letter_by_id); executing it must recompute
    docx_ats_report too, not just ats_report."""
    from fastapi import BackgroundTasks
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import patch_cover_letter_section

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]

    bg = BackgroundTasks()

    with patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_pdf_ats_report()), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-patched")), \
         patch("applire.services.office_export.extract._audit_letter_text", return_value=_make_docx_ats_report()):
        await patch_cover_letter_section(cl_id, "body", "Neuer Absatz", session, bg)

    assert len(bg.tasks) == 1, f"expected 1 background task, got {len(bg.tasks)}"

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_pdf_ats_report()), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-patched")), \
         patch("applire.services.office_export.extract._audit_letter_text", return_value=_make_docx_ats_report()):
        mock_session_local.return_value.__aenter__.return_value = session
        await bg.tasks[0]()

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.docx_ats_report is not None, (
        "docx_ats_report should be recomputed after the section-editor re-audit"
    )
    assert cl.docx_ats_report["passed"] == 88, (
        f"docx_ats_report should reflect the sentinel audit, got {cl.docx_ats_report}"
    )


# ---------------------------------------------------------------------------
# Call site 3: agent door (render_agent_letter)
# ---------------------------------------------------------------------------

AGENT_LETTER_CONTENT = {
    "header": {
        "name": "Anna Bauer",
        "address": "Hauptstraße 42, 10115 Berlin",
        "phone": None,
        "email": "anna@example.de",
        "photo_url": "/etc/passwd",  # hostile — render_agent_letter must strip this
    },
    "recipient": {
        "name": "Frau Schmidt",
        "title": None,
        "company": "TechVision GmbH",
        "address": None,
        "date": None,
    },
    "body": {"paragraphs": ["Sehr geehrte Frau Schmidt,", "Hauptteil.", "Schluss."]},
    "signature": {"closing": None, "name": "Anna Bauer"},
}


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.job import JobAnalysis

    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    db.add_all(
        [
            JobAnalysis(
                id=job_id,
                raw_text_hash="docx-render-agent-letter",
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
                profile_json=_stub_profile_json(),
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db.commit()
    return {"db": db, "job_id": job_id, "profile_id": profile_id}


@pytest.mark.asyncio
async def test_agent_authored_letter_persists_docx_ats_report(seeded):
    """render_agent_letter's audit-only tail must persist docx_ats_report
    in the same commit as ats_report/truthfulness_report — "ready implies
    reports available" extended to the new column."""
    from applire.services.cover_letter import render_agent_letter

    with patch(
        "applire.services.cover_letter_pdf.render_pdf",
        new=AsyncMock(return_value=b"%PDF"),
    ), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_letter_text", return_value=_make_docx_ats_report()):
        cl = await render_agent_letter(
            dict(AGENT_LETTER_CONTENT), seeded["job_id"], seeded["db"]
        )

    assert cl.origin == "agent"
    assert cl.status == "ready"
    assert cl.docx_ats_report is not None, (
        "docx_ats_report should be populated by the agent-authored re-audit"
    )
    assert cl.docx_ats_report["passed"] == 88, (
        f"docx_ats_report must reflect the DOCX audit engine, got {cl.docx_ats_report}"
    )


# ---------------------------------------------------------------------------
# The property the whole design exists for: audited bytes == delivered bytes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_docx_audit_reflects_section_overrides(db_with_cover_letter):
    """The persisted docx_ats_report must describe the OVERRIDE-APPLIED
    document get_cover_letter_docx actually serves, never the raw
    pre-override letter_data. Captures the TEXT the docx audit engine
    actually received (extracted from the REAL rendered .docx bytes, via
    the real render_letter_docx + extract_docx_text — nothing about the
    render or extraction is mocked) and asserts the override marker is
    present while the pre-override body paragraph is gone.

    This is the property a fix that skips `_apply_section_overrides` before
    rendering the audited document would break: the audit would describe
    content nobody downloads."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]

    cl = await session.get(GeneratedCoverLetter, cl_id)
    cl.section_overrides = {"body": "MUTATION-MARKER-OVERRIDDEN-BODY"}
    await session.commit()

    captured: dict = {}

    def fake_docx_audit(text, letter_data, keywords, ledger=None, **kwargs):
        captured["text"] = text
        return _make_docx_ats_report()

    with patch("applire.services.cover_letter.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_letter_text", side_effect=fake_docx_audit):
        from applire.services.cover_letter import _update_ats_report_letter
        await _update_ats_report_letter(cl, session)

    text = captured.get("text")
    assert text is not None, "the docx audit engine was never called"
    assert "MUTATION-MARKER-OVERRIDDEN-BODY" in text, (
        "the audited .docx text does not contain the section override — "
        "the report was computed from a document that is not the one "
        "get_cover_letter_docx actually serves"
    )
    assert "ich bewerbe mich hiermit" not in text, (
        "the audited .docx text still contains the PRE-override body — "
        "the override was not applied before rendering the audited document"
    )


# ---------------------------------------------------------------------------
# Contract: engine errors leave docx_ats_report NULL, never raise / alter status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letter_docx_audit_engine_error_leaves_report_null_and_ready(db_with_cover_letter):
    """_update_ats_report_letter's own docstring contract ("an audit
    failure must NEVER fail or alter generation status"; "deliberately
    wipes any previous report on error") extended to docx_ats_report: if
    the docx audit engine raises, docx_ats_report stays NULL, status is
    untouched, and the INDEPENDENT PDF-side ats_report is unaffected (the
    four report blocks are deliberately independent try blocks)."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]

    cl = await session.get(GeneratedCoverLetter, cl_id)

    with patch("applire.services.cover_letter.get_provider", side_effect=RuntimeError("no provider in test")), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_letter_text", side_effect=RuntimeError("docx audit boom")):
        from applire.services.cover_letter import _update_ats_report_letter
        await _update_ats_report_letter(cl, session)

    assert cl.status == "ready", (
        f"a docx-audit engine error must never alter generation status, got {cl.status!r}"
    )
    assert cl.docx_ats_report is None, (
        "docx_ats_report must be NULL when the docx audit engine errors"
    )
    assert cl.ats_report is not None and cl.ats_report["passed"] == 1, (
        "the PDF-side ats_report must be unaffected by the docx audit's failure"
    )


@pytest.mark.asyncio
async def test_letter_docx_audit_engine_error_via_generation_path_leaves_status_ready(db_with_cover_letter):
    """The end-to-end demonstration of the same contract:
    _render_cover_letter_background has NO handler of its own for a
    docx-audit error — the ONLY thing standing between a docx-audit
    exception and CoverLetterStatus.failed is _update_ats_report_letter's
    own try/except around the new block. If that block's except clause
    stopped catching real errors, this exception would propagate out of
    the whole background task and flip status to 'failed', discarding an
    otherwise-successful generation over a measurement-only audit
    failure."""
    from applire.models.cover_letter import GeneratedCoverLetter

    ctx = db_with_cover_letter
    session = ctx["db"]
    cl_id = ctx["cl_id"]
    job_id = ctx["job_id"]

    letter_raw = _stub_letter_data()
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = letter_raw

    async def fake_review(**kwargs):
        return kwargs["draft"]

    with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
         patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF-fake")), \
         patch("applire.services.ats_audit.audit_cover_letter", return_value=_make_pdf_ats_report()), \
         patch("applire.services.office_export.extract._audit_letter_text", side_effect=RuntimeError("docx audit boom")):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id, None, job_id)

    cl = await session.get(GeneratedCoverLetter, cl_id)
    assert cl.status == "ready", (
        "a docx-audit engine error reaching the generation path must NOT "
        f"change generation status to 'failed' — got {cl.status!r}"
    )
    assert cl.docx_ats_report is None
    assert cl.ats_report is not None and cl.ats_report["passed"] == 1
