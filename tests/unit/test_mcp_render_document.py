# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""US251 (E044, ADR-054) — MCP `render_document`.

À-la-carte contract: the tool must work with NO prior generate_*/start_flow
call (analyze_jd → render_document only), return the reports inline, and
reject bad input with agent-actionable errors.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

PROFILE_JSON = {
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
    "contact": {"name": "Anna Bauer", "email": "anna@example.de", "location": "Berlin"},
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
    "show_photo": False,
}

AGENT_LETTER_CONTENT = {
    "header": {"name": "Anna Bauer", "address": "Hauptstraße 42, 10115 Berlin"},
    "recipient": {"name": "Frau Schmidt", "company": "TechVision GmbH"},
    "body": {"paragraphs": ["Sehr geehrte Frau Schmidt,", "Hauptteil.", "Schluss."]},
    "signature": {"name": "Anna Bauer"},
}


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest_asyncio.fixture
async def seeded(db):
    """analyze_jd-equivalent state ONLY: one job, one profile. No flow, no
    generate_* row — proving the à-la-carte property."""
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    db.add_all(
        [
            JobAnalysis(
                id=job_id,
                raw_text_hash="mcp-render",
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
                profile_json=PROFILE_JSON,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db.commit()
    return {"db": db, "job_id": job_id}


def _cv_patches(session):
    return (
        patch("applire.mcp.server.get_db", return_value=_db_cm(session)),
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF")),
        patch(
            "applire.services.ats_audit.extract_text_and_pages",
            return_value=("Anna Bauer Python", 2),
        ),
    )


@pytest.mark.asyncio
async def test_input_validation_errors():
    from applire.mcp.server import render_document

    cases = [
        # bad kind
        {"document_kind": "resume", "content": {"x": 1}, "job_id": str(uuid.uuid4())},
        # empty content
        {"document_kind": "cv", "content": {}, "job_id": str(uuid.uuid4())},
        # bad template
        {
            "document_kind": "cv",
            "content": dict(AGENT_CV_CONTENT),
            "job_id": str(uuid.uuid4()),
            "template": "fancy",
        },
        # target_pages on a letter
        {
            "document_kind": "cover_letter",
            "content": dict(AGENT_LETTER_CONTENT),
            "job_id": str(uuid.uuid4()),
            "target_pages": 1,
        },
        # target_pages < 1
        {
            "document_kind": "cv",
            "content": dict(AGENT_CV_CONTENT),
            "job_id": str(uuid.uuid4()),
            "target_pages": 0,
        },
    ]
    for kwargs in cases:
        with pytest.raises(McpError) as exc_info:
            await render_document(**kwargs)
        assert exc_info.value.error.code == -32602, kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_target", [11, 999])
async def test_render_document_rejects_target_pages_above_max(bad_target):
    """#379: the floor (>= 1) was validated here; the ceiling was not — an
    unbounded target_pages fed straight into the per-role bullet-budget math and
    produced inert "max 1002 bullet(s)" ceilings on a captured target_pages=999
    run. Mirrors the existing fail-loud style (no silent clamping)."""
    from applire.constants import MAX_TARGET_PAGES
    from applire.mcp.server import render_document

    with pytest.raises(McpError, match=f"target_pages must be between 1 and {MAX_TARGET_PAGES}"):
        await render_document(
            document_kind="cv",
            content=dict(AGENT_CV_CONTENT),
            job_id=str(uuid.uuid4()),
            target_pages=bad_target,
        )


@pytest.mark.asyncio
async def test_alacarte_cv_render_returns_reports_inline(seeded):
    from applire.mcp.server import render_document
    from applire.models.cv import GeneratedCV
    from sqlalchemy import select

    p1, p2, p3, p4 = _cv_patches(seeded["db"])
    with p1, p2, p3, p4:
        result = await render_document(
            document_kind="cv",
            content=dict(AGENT_CV_CONTENT),
            job_id=str(seeded["job_id"]),
            target_pages=2,
        )

    assert result["status"] == "ready"
    assert result["document_kind"] == "cv"
    assert result["schema_version"] == "cv/1"
    assert result["ats_report"] is not None
    assert result["truthfulness_report"] is not None
    assert f"/api/cv/{result['document_id']}/pdf" in result["pdf_url"]

    row = (await seeded["db"].execute(select(GeneratedCV))).scalar_one()
    assert row.origin == "agent"
    assert str(row.id) == result["document_id"]


@pytest.mark.asyncio
async def test_alacarte_letter_render(seeded):
    from applire.mcp.server import render_document
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport

    report = ATSReport(
        document="cover_letter",
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
    )
    with (
        patch("applire.mcp.server.get_db", return_value=_db_cm(seeded["db"])),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            new=AsyncMock(return_value=b"%PDF"),
        ),
        patch("applire.services.ats_audit.audit_cover_letter", return_value=report),
    ):
        result = await render_document(
            document_kind="cover_letter",
            content=dict(AGENT_LETTER_CONTENT),
            job_id=str(seeded["job_id"]),
        )

    assert result["status"] == "ready"
    assert result["schema_version"] == "cover-letter/1"
    assert result["truthfulness_report"] is not None
    assert f"/api/cover-letter/{result['document_id']}/pdf" in result["pdf_url"]


@pytest.mark.asyncio
async def test_unknown_field_surfaces_field_path(seeded):
    from applire.mcp.server import render_document

    content = dict(AGENT_CV_CONTENT)
    content["work_experience"] = content.pop("work_history")  # classic agent typo
    with patch("applire.mcp.server.get_db", return_value=_db_cm(seeded["db"])):
        with pytest.raises(McpError) as exc_info:
            await render_document(
                document_kind="cv", content=content, job_id=str(seeded["job_id"])
            )
    assert exc_info.value.error.code == -32602
    assert "work_experience" in exc_info.value.error.message


@pytest.mark.asyncio
async def test_unknown_job_raises_not_found(db):
    from applire.mcp.server import render_document

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        with pytest.raises(McpError) as exc_info:
            await render_document(
                document_kind="cv",
                content=dict(AGENT_CV_CONTENT),
                job_id=str(uuid.uuid4()),
            )
    assert exc_info.value.error.code == -32001


@pytest.mark.asyncio
async def test_schema_resources_serve_versioned_contracts():
    import json

    from applire.mcp.server import resource_schema_cv, resource_schema_cover_letter

    cv = json.loads(await resource_schema_cv())
    assert cv["schema_version"] == "cv/1"
    assert "work_history" in cv["json_schema"]["properties"]

    letter = json.loads(await resource_schema_cover_letter())
    assert letter["schema_version"] == "cover-letter/1"
    assert "body" in letter["json_schema"]["properties"]
