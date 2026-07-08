"""
Unit tests for PATCH /api/cv/{id}/color endpoint.
Run: pytest tests/unit/test_cv_color_endpoint.py -v
"""
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.session
    import applire.models.application
    import applire.models.cover_letter
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


def _ready_cv(job_id, profile_id):
    from applire.models.cv import GeneratedCV
    minimal = {
        "contact": {"name": "T", "email": "t@t.com", "phone": "", "location": "", "linkedin": "", "photo_url": None},
        "summary": "", "work_history": [], "education": [],
        "skills": [], "languages": [], "show_photo": False,
    }
    return GeneratedCV(
        id=uuid.uuid4(), job_analysis_id=job_id, profile_id=profile_id,
        tailored_data=minimal, template="modern_swiss", status="ready",
    )


def _make_job():
    from applire.models.job import JobAnalysis
    return JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=str(uuid.uuid4()), raw_text="x",
        role_title="Dev", required_skills=[], nice_to_have_skills=[],
        keywords=[], seniority_level="mid", company_culture_signals=[],
        language_requirement="de",
    )


def _make_profile():
    from applire.models.profile import MasterProfile
    return MasterProfile(id=uuid.uuid4(), profile_json={"personal_info": {"name": "Test"}})


class TestPatchCvColor:
    @pytest.mark.asyncio
    async def test_patch_creates_color_profile_and_updates_cv(self, db):
        from applire.routers.cv_color import apply_cv_color
        job = _make_job()
        profile = _make_profile()
        db.add(job)
        db.add(profile)
        await db.commit()
        cv = _ready_cv(job.id, profile.id)
        db.add(cv)
        await db.commit()

        result = await apply_cv_color(cv.id, "#ff5500", db)
        assert result["derived"]["--cv-accent"] == "#ff5500"
        assert "color_profile_id" in result
        await db.refresh(cv)
        assert cv.color_profile_id is not None

    @pytest.mark.asyncio
    async def test_patch_raises_lookup_error_for_unknown_cv(self, db):
        from applire.routers.cv_color import apply_cv_color
        with pytest.raises(LookupError):
            await apply_cv_color(uuid.uuid4(), "#ff5500", db)

    @pytest.mark.asyncio
    async def test_patch_raises_value_error_for_invalid_hex(self, db):
        from applire.routers.cv_color import apply_cv_color
        job = _make_job()
        profile = _make_profile()
        db.add(job)
        db.add(profile)
        await db.commit()
        cv = _ready_cv(job.id, profile.id)
        db.add(cv)
        await db.commit()
        with pytest.raises(ValueError):
            await apply_cv_color(cv.id, "not-a-hex", db)
