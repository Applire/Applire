# backend/tests/unit/test_iter23_section_editor.py
"""Unit tests for Sprint 9 CV Section Editor endpoints (23.14)."""
import json
import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.auth import get_auth_provider
from applire.db.session import get_db
from applire.routers.cv import router

_CV_ID = str(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
_SECTION_ID = "introduction"
_POSITION_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_POSITION_SECTION_ID = f"position::{_POSITION_UUID}"


async def _stub_db():
    yield None


@pytest.fixture()
def client():
    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: None
    app.dependency_overrides[get_db] = _stub_db
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /api/cv/{id}/sections
# ---------------------------------------------------------------------------


def test_get_sections_returns_200_with_sections(client):
    from applire.schemas.cv_sections import CVSectionsResponse, SectionItem, GapHintItem
    mock_response = CVSectionsResponse(
        sections=[
            SectionItem(
                section_id="introduction",
                label="Introduction",
                content="Experienced developer",
                has_override=False,
                gaps=[GapHintItem(id="Python", label="Python")],
            )
        ],
        general_gaps=[],
    )
    with patch(
        "applire.routers.cv.get_cv_sections",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.get(f"/api/cv/{_CV_ID}/sections")

    assert response.status_code == 200
    data = response.json()
    assert len(data["sections"]) == 1
    assert data["sections"][0]["section_id"] == "introduction"
    assert data["sections"][0]["gaps"][0]["label"] == "Python"


def test_get_sections_returns_404_when_cv_not_found(client):
    with patch(
        "applire.routers.cv.get_cv_sections",
        new_callable=AsyncMock,
        side_effect=LookupError("CV not found"),
    ):
        response = client.get(f"/api/cv/{_CV_ID}/sections")

    assert response.status_code == 404


def test_get_sections_returns_empty_list_when_no_snapshot(client):
    from applire.schemas.cv_sections import CVSectionsResponse
    mock_response = CVSectionsResponse(sections=[], general_gaps=[])
    with patch(
        "applire.routers.cv.get_cv_sections",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.get(f"/api/cv/{_CV_ID}/sections")

    assert response.status_code == 200
    assert response.json()["sections"] == []


# ---------------------------------------------------------------------------
# PATCH /api/cv/{id}/sections/{section_id}
# ---------------------------------------------------------------------------


def test_patch_section_returns_html_and_overrides_applied(client):
    from applire.schemas.cv_sections import SectionPatchResponse
    mock_response = SectionPatchResponse(
        html="<html><body>Updated CV</body></html>",
        overrides_applied=["introduction"],
    )
    with patch(
        "applire.routers.cv.patch_cv_section",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.patch(
            f"/api/cv/{_CV_ID}/sections/{_SECTION_ID}",
            json={"content": "My edited summary", "save_to_profile": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert "<html>" in data["html"]
    assert "introduction" in data["overrides_applied"]


def test_patch_section_returns_422_for_invalid_section_id(client):
    with patch(
        "applire.routers.cv.patch_cv_section",
        new_callable=AsyncMock,
        side_effect=ValueError("Unknown section_id: 'nonexistent'"),
    ):
        response = client.patch(
            f"/api/cv/{_CV_ID}/sections/nonexistent",
            json={"content": "text", "save_to_profile": False},
        )

    assert response.status_code == 422


def test_patch_section_rejects_content_over_10000_chars(client):
    """Pydantic max_length=10_000 validation fires before the service is called."""
    long_content = "x" * 10_001
    response = client.patch(
        f"/api/cv/{_CV_ID}/sections/{_SECTION_ID}",
        json={"content": long_content, "save_to_profile": False},
    )
    assert response.status_code == 422


def test_patch_section_passes_save_to_profile_true(client):
    from applire.schemas.cv_sections import SectionPatchResponse
    mock_service = AsyncMock(
        return_value=SectionPatchResponse(
            html="<html></html>",
            overrides_applied=["introduction"],
        )
    )
    with patch("applire.routers.cv.patch_cv_section", new=mock_service):
        client.patch(
            f"/api/cv/{_CV_ID}/sections/{_SECTION_ID}",
            json={"content": "text", "save_to_profile": True},
        )

    mock_service.assert_called_once()
    call_args = mock_service.call_args.args
    # patch_cv_section(cv_id, section_id, content, save_to_profile, db)
    assert call_args[3] is True  # save_to_profile is the 4th positional arg


def test_patch_section_position_id_with_double_colon(client):
    """Verify the :path converter captures position::uuid correctly."""
    from applire.schemas.cv_sections import SectionPatchResponse
    mock_service = AsyncMock(
        return_value=SectionPatchResponse(
            html="<html></html>",
            overrides_applied=[_POSITION_SECTION_ID],
        )
    )
    with patch("applire.routers.cv.patch_cv_section", new=mock_service):
        response = client.patch(
            f"/api/cv/{_CV_ID}/sections/{_POSITION_SECTION_ID}",
            json={"content": "Built APIs\nLed team", "save_to_profile": False},
        )

    assert response.status_code == 200
    call_args = mock_service.call_args.args
    assert call_args[1] == _POSITION_SECTION_ID  # section_id captured with ::


# ---------------------------------------------------------------------------
# GET /api/cv/{id}/html — regression: overrides applied but endpoint still works
# ---------------------------------------------------------------------------


def test_html_endpoint_still_returns_html_with_overrides_applied(client):
    test_html = "<html><body>Patched CV</body></html>"
    with patch("applire.routers.cv.get_cv_html", new_callable=AsyncMock, return_value=test_html):
        response = client.get(f"/api/cv/{_CV_ID}/html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Patched CV" in response.text


# ---------------------------------------------------------------------------
# Unit tests for build_content_snapshot
# ---------------------------------------------------------------------------


def test_build_content_snapshot_extracts_all_fields():
    from applire.services.cv_section_editor import build_content_snapshot
    from applire.schemas.cv import TailoredCVData, TailoredWorkEntry, TailoredContact

    tailored = TailoredCVData(
        contact=TailoredContact(name="Max"),
        summary="Experienced Python developer",
        work_history=[
            TailoredWorkEntry(
                company="ACME",
                role="Engineer",
                start_date="2020-01",
                end_date="2023-12",
                bullets=["Built APIs", "Led team"],
            )
        ],
        skills=["Python", "FastAPI"],
    )

    snapshot = build_content_snapshot(tailored)

    assert snapshot["introduction"] == "Experienced Python developer"
    assert snapshot["skills"] == ["Python", "FastAPI"]
    assert len(snapshot["positions"]) == 1
    pos = snapshot["positions"][0]
    assert pos["title"] == "Engineer"
    assert pos["company"] == "ACME"
    assert pos["bullets"] == ["Built APIs", "Led team"]
    assert pos["index"] == 0
    uuid.UUID(pos["id"])  # raises ValueError if not valid UUID


def test_apply_overrides_replaces_introduction():
    from applire.services.cv_section_editor import apply_overrides_to_tailored
    from applire.schemas.cv import TailoredCVData, TailoredContact

    tailored = TailoredCVData(
        contact=TailoredContact(name="Max"),
        summary="Original summary",
        work_history=[],
        skills=[],
    )
    result = apply_overrides_to_tailored(
        tailored,
        content_snapshot=None,
        section_overrides={"introduction": "My new summary"},
    )
    assert result.summary == "My new summary"


def test_apply_overrides_replaces_skills():
    from applire.services.cv_section_editor import apply_overrides_to_tailored
    from applire.schemas.cv import TailoredCVData, TailoredContact

    tailored = TailoredCVData(
        contact=TailoredContact(name="Max"),
        summary="",
        work_history=[],
        skills=["Java"],
    )
    result = apply_overrides_to_tailored(
        tailored,
        content_snapshot=None,
        section_overrides={"skills": "Python\nFastAPI\nPostgreSQL"},
    )
    assert result.skills == ["Python", "FastAPI", "PostgreSQL"]


def test_apply_overrides_with_no_overrides_returns_unchanged():
    from applire.services.cv_section_editor import apply_overrides_to_tailored
    from applire.schemas.cv import TailoredCVData, TailoredContact

    tailored = TailoredCVData(
        contact=TailoredContact(name="Max"),
        summary="Original",
        work_history=[],
        skills=["Java"],
    )
    result = apply_overrides_to_tailored(tailored, None, None)
    assert result.summary == "Original"
    assert result is tailored  # same object — no copy made


# ---------------------------------------------------------------------------
# Regression: _save_section_to_profile must JSON-serialize date fields
#
# Pydantic's model_dump() (default mode="python") leaves `date` objects intact
# in the dict, which raises TypeError: Object of type date is not JSON
# serializable at DB flush time (JSONB column). Every sibling write site in
# services/profile/__init__.py uses model_dump(mode="json"); the section
# editor's save-to-profile path did not.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered (mirrors
    tests/unit/test_ats_report_persistence.py)."""
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


def _profile_json_with_certification_date() -> dict:
    """A profile whose only date-typed field is a certification's date_obtained."""
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
        "languages": [],
        "certifications": [
            {
                "name": "AWS Certified Solutions Architect",
                "issuer": "Amazon",
                "date_obtained": "2023-05-01",
                "expiry_date": None,
            }
        ],
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


@pytest_asyncio.fixture
async def db_with_dated_profile(db):
    """User → Job → Profile(with a date-typed certification) → GeneratedCV,
    matching the position:: save path used by _save_section_to_profile."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.cv import GeneratedCV

    user_id = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
    job_id = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
    profile_id = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
    cv_id = uuid.UUID("00000000-0000-0000-0000-0000000000a5")
    position_uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    content_snapshot = {
        "introduction": "Erfahrener Python-Entwickler",
        "positions": [
            {
                "id": position_uuid,
                "index": 0,
                "title": "Software Engineer",
                "company": "Acme GmbH",
                "period": "2020-01",
                "bullets": ["Backend-Entwicklung"],
            }
        ],
        "skills": ["Python"],
    }
    tailored_data = {
        "contact": {"name": "Max Mustermann", "email": "max@example.com"},
        "summary": "Erfahrener Python-Entwickler",
        "work_history": [
            {
                "company": "Acme GmbH",
                "role": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "bullets": ["Backend-Entwicklung"],
            }
        ],
        "skills": ["Python"],
    }

    user = User(
        id=user_id,
        email="section-editor-date-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="section-editor-date-test",
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
        profile_json=_profile_json_with_certification_date(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cv = GeneratedCV(
        id=cv_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        tailored_data=tailored_data,
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
        "position_uuid": position_uuid,
    }


@pytest.mark.asyncio
async def test_save_section_to_profile_persists_json_serializable_dates(db_with_dated_profile):
    """Regression: saving a section edit to the Master Profile must leave
    profile_json fully JSON-serializable even when the profile contains a
    date-typed field (certification.date_obtained).

    Before the fix, _save_section_to_profile wrote
    profile.profile_json = profile_data.model_dump() (default mode="python"),
    which keeps raw `date` objects in the dict and raises
    TypeError: Object of type date is not JSON serializable at DB flush.
    """
    from applire.models.profile import MasterProfile
    from applire.services.cv_section_editor import patch_cv_section

    ctx = db_with_dated_profile
    session = ctx["db"]
    cv_id = ctx["cv_id"]
    profile_id = ctx["profile_id"]
    position_uuid = ctx["position_uuid"]

    section_id = f"position::{position_uuid}"

    # Drives the real save-to-profile path (save_to_profile=True), same as a
    # user saving a section edit back to their Master Profile.
    await patch_cv_section(cv_id, section_id, "Built cloud infrastructure", True, session)

    profile = await session.get(MasterProfile, profile_id)
    assert profile is not None

    # The certification's date must survive the round trip...
    cert = profile.profile_json["certifications"][0]
    assert cert["date_obtained"] in ("2023-05-01", "2023-05-01T00:00:00"), cert["date_obtained"]
    assert not isinstance(cert["date_obtained"], date), (
        "date_obtained must be serialized to a string/ISO value, not left as a "
        "raw python date object"
    )

    # ...and the whole persisted dict must be JSON-serializable (this is what
    # actually raises TypeError at DB flush when mode="json" is missing).
    json.dumps(profile.profile_json)
