"""
Sprint 22 — Directed rewrite unit tests

Covers:
  - RewriteRequest / RewriteResponse Pydantic schemas
  - rewrite_section() service function (async, mocked LLM + SQLite)

No Docker, no real LLM.

Run:
    pytest tests/unit/test_cv_assist_rewrite.py -v
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def test_rewrite_request_schema_defaults():
    from applire.schemas.cv_sections import RewriteRequest
    req = RewriteRequest()
    assert req.directions == ""
    assert req.gap_ids == []


def test_rewrite_request_schema_with_values():
    from applire.schemas.cv_sections import RewriteRequest
    req = RewriteRequest(directions="I also did chromatography", gap_ids=["EU GMP Audit"])
    assert req.directions == "I also did chromatography"
    assert req.gap_ids == ["EU GMP Audit"]


def test_rewrite_response_schema():
    from applire.schemas.cv_sections import RewriteResponse
    resp = RewriteResponse(suggestion="Updated section text")
    assert resp.suggestion == "Updated section text"


# ---------------------------------------------------------------------------
# Service tests — rewrite_section()
# ---------------------------------------------------------------------------

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.session
    import applire.models.flow
    import applire.models.uploads
    import applire.models.application

    import applire.models.cover_letter

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def db_with_cv(db):
    """Insert User -> Job -> Profile -> GapAnalysis -> GeneratedCV -> FlowSession."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.gap import GapAnalysis
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    job_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    profile_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    gap_analysis_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    cv_id = uuid.UUID("00000000-0000-0000-0000-000000000005")
    flow_id = uuid.UUID("00000000-0000-0000-0000-000000000006")
    pos_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    content_snapshot = {
        "introduction": "Erfahrener Python-Entwickler",
        "positions": [
            {
                "id": pos_uuid,
                "index": 0,
                "title": "Software Engineer",
                "company": "Acme GmbH",
                "period": "2020-01",
                "bullets": ["Backend-Entwicklung", "REST APIs"],
            }
        ],
        "skills": ["Python", "FastAPI"],
    }

    db.add(User(
        id=user_id, email="test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db.add(JobAnalysis(
        id=job_id, raw_text_hash="hash123",
        raw_text="Senior Software Engineer job description",
        role_title="Senior Software Engineer",
        required_skills=["Python"], nice_to_have_skills=[],
        keywords=[], seniority_level="senior",
        company_culture_signals=[], language_requirement="de",
    ))
    db.add(MasterProfile(
        id=profile_id,
        profile_json={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db.add(GapAnalysis(
        id=gap_analysis_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        category_a=[], category_b=["Python"], category_c=["EU GMP Audit"],
        match_score=0.8,
    ))
    db.add(GeneratedCV(
        id=cv_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        tailored_data={},
        template="classic_german",
        content_snapshot=content_snapshot,
        status="ready",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db.add(FlowSession(
        id=flow_id,
        user_id=user_id,
        job_id=job_id,
        gap_analysis_id=gap_analysis_id,
        generated_cv_id=cv_id,
        current_step="complete",
        user_type="new",
        available_actions={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    await db.commit()

    return {
        "cv_id": cv_id,
        "section_id_intro": "introduction",
        "section_id_skills": "skills",
        "section_id_position": f"position::{pos_uuid}",
    }


def _make_provider(response: str = "Updated section text") -> AsyncMock:
    provider = AsyncMock()
    provider.acomplete = AsyncMock(return_value=response)
    return provider


@pytest.mark.asyncio
async def test_rewrite_section_introduction(db_with_cv, db):
    from applire.services.cv_assist import rewrite_section
    provider = _make_provider("New intro text")
    result = await rewrite_section(
        db_with_cv["cv_id"],
        db_with_cv["section_id_intro"],
        directions="Add Python expertise",
        gap_ids=["Python"],
        provider=provider,
        db=db,
    )
    assert result.suggestion == "New intro text"
    provider.acomplete.assert_called_once()
    # Prompt must mention the section label and user directions
    call_args = provider.acomplete.call_args
    prompt = call_args[0][0]
    assert "Introduction" in prompt or "introduction" in prompt.lower()
    assert "Add Python expertise" in prompt


@pytest.mark.asyncio
async def test_rewrite_section_position(db_with_cv, db):
    from applire.services.cv_assist import rewrite_section
    provider = _make_provider("• Added bullet\n• REST APIs")
    result = await rewrite_section(
        db_with_cv["cv_id"],
        db_with_cv["section_id_position"],
        directions="",
        gap_ids=[],
        provider=provider,
        db=db,
    )
    assert result.suggestion == "• Added bullet\n• REST APIs"


@pytest.mark.asyncio
async def test_rewrite_section_unknown_section_raises(db_with_cv, db):
    from applire.services.cv_assist import rewrite_section
    with pytest.raises(ValueError, match="Unknown section_id"):
        await rewrite_section(
            db_with_cv["cv_id"],
            "unknown::id",
            directions="test",
            gap_ids=[],
            provider=_make_provider(),
            db=db,
        )


@pytest.mark.asyncio
async def test_rewrite_section_unknown_cv_raises(db):
    from applire.services.cv_assist import rewrite_section
    with pytest.raises(LookupError):
        await rewrite_section(
            uuid.uuid4(),
            "introduction",
            directions="test",
            gap_ids=[],
            provider=_make_provider(),
            db=db,
        )
