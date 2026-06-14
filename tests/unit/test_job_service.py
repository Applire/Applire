"""Unit tests for services/job.py — analyze_jd validation.

Covers:
  - LLM returns null role_title (cookie wall / non-JD content) → ValueError raised
  - LLM returns null seniority_level → stored as empty string, no DB crash
  - LLM returns valid JD → stored correctly

No Docker, no real LLM. Uses SQLite in-memory + stub LLM provider.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.job import analyze_jd


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user          # noqa: F401
    import applire.models.job           # noqa: F401
    import applire.models.profile       # noqa: F401
    import applire.models.gap           # noqa: F401
    import applire.models.cv            # noqa: F401
    import applire.models.session       # noqa: F401
    import applire.models.flow          # noqa: F401
    import applire.models.uploads       # noqa: F401
    import applire.models.application   # noqa: F401
    import applire.models.color_profile # noqa: F401
    import applire.models.company       # noqa: F401
    import applire.models.user_settings # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _make_provider(response: dict):
    """Stub LLM provider that returns a fixed dict from aparse_json."""
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=response)
    return provider


_VALID_JD_RESPONSE = {
    "company_name": "BioNTech SE",
    "role_title": "Director QC Processes",
    "required_skills": ["GMP", "LIMS"],
    "nice_to_have_skills": ["SAP"],
    "keywords": ["QC", "pharmaceutical"],
    "seniority_level": "Director",
    "company_culture_signals": ["international"],
    "language_requirement": "German (C1)",
    "berufsbild_code": None,
    "berufsbild_label": None,
}


# ---------------------------------------------------------------------------
# JD validity guard (US159 / FMEA JF-M-4.5)
# ---------------------------------------------------------------------------


class TestJdValidityGuard:
    """
    Validity must NOT hinge solely on role_title. A real JD without an explicit
    title line (requirements extracted) is accepted — the UI asks for the title
    inline. Only true garbage (no title AND no requirements/skills) is rejected,
    so the only garbage detector still functions (router surfaces 422, not 500).
    """

    @pytest.mark.asyncio
    async def test_no_title_and_no_requirements_raises(self, db):
        # Cookie wall / non-JD page: neither a title nor any requirements.
        response = {
            **_VALID_JD_RESPONSE,
            "role_title": None,
            "required_skills": [],
            "nice_to_have_skills": [],
        }
        provider = _make_provider(response)
        with pytest.raises(ValueError, match="job description"):
            await analyze_jd("cookie consent page content", db, provider)

    @pytest.mark.asyncio
    async def test_empty_title_and_no_requirements_raises(self, db):
        response = {
            **_VALID_JD_RESPONSE,
            "role_title": "   ",
            "required_skills": [],
            "nice_to_have_skills": [],
        }
        provider = _make_provider(response)
        with pytest.raises(ValueError, match="job description"):
            await analyze_jd("some scraped text", db, provider)

    @pytest.mark.asyncio
    async def test_no_title_but_with_requirements_is_accepted(self, db):
        """FMEA 4.5: a plausible JD missing only its title line is accepted
        (title left empty for the UI to fill inline), not hard-rejected."""
        response = {**_VALID_JD_RESPONSE, "role_title": None}  # keeps GMP/LIMS requirements
        provider = _make_provider(response)
        result = await analyze_jd("a genuine job ad without an explicit title line", db, provider)
        assert result.role_title == ""

    @pytest.mark.asyncio
    async def test_valid_role_title_does_not_raise(self, db):
        provider = _make_provider(_VALID_JD_RESPONSE)
        result = await analyze_jd("full job description text", db, provider)
        assert result.role_title == "Director QC Processes"


# ---------------------------------------------------------------------------
# Null seniority_level handling
# ---------------------------------------------------------------------------


class TestNullSeniorityLevel:
    """
    The LLM sometimes returns null for seniority_level (e.g. when it cannot
    determine level from context). The service must not crash; null should be
    stored as an empty string to satisfy the NOT NULL DB constraint.
    """

    @pytest.mark.asyncio
    async def test_null_seniority_level_stored_as_empty_string(self, db):
        response = {**_VALID_JD_RESPONSE, "seniority_level": None}
        provider = _make_provider(response)
        result = await analyze_jd("full job description text", db, provider)
        assert result.seniority_level == ""

    @pytest.mark.asyncio
    async def test_valid_seniority_level_stored_correctly(self, db):
        provider = _make_provider(_VALID_JD_RESPONSE)
        result = await analyze_jd("full job description text", db, provider)
        assert result.seniority_level == "Director"
