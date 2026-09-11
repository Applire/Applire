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

"""ST-F / US172 — CV extraction prompt contracts for projects + volunteer capabilities.

Tests assert:
  1. Prompt-contract: GENERIC_CV_EXTRACTION_PROMPT contains a `projects` schema block (with
     `associated_experience` field) and a no-folding instruction that prevents projects from
     being merged into work_experience. (JD_AWARE_CV_EXTRACTION_PROMPT was retired M5.1.3,
     2026-09-11 — see prompts/cv_extraction.py's version header.)
  2. Prompt-contract: volunteer block now documents `achievements` and `technologies`.
  3. Mock: the mock provider's "cv analyst" path returns a raw dict whose `projects` key
     is non-empty AND validates cleanly as MasterProfileData.
  4. M5.1.3 positive claim: upload_cv always selects the generic prompt, even when a
     caller supplies job_id.

Run:
    pytest tests/unit/test_cv_extraction_projects.py -v
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# 1. Prompt-contract tests — projects block
# ---------------------------------------------------------------------------


def test_generic_prompt_contains_projects_key():
    """The system prompt must expose a 'projects' key so the LLM extracts it."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    assert "projects" in GENERIC_CV_EXTRACTION_PROMPT


def test_generic_prompt_contains_associated_experience_field():
    """The projects block must document `associated_experience` (links a project to a job)."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    assert "associated_experience" in GENERIC_CV_EXTRACTION_PROMPT


# test_jd_aware_prompt_contains_projects_key and test_jd_aware_prompt_contains_associated_
# experience_field removed M5.1.3 (2026-09-11): they asserted the projects schema block was
# inherited by JD_AWARE_CV_EXTRACTION_PROMPT, and that constant no longer exists — the variant
# is gone, not merely unassertable.


def test_generic_prompt_no_folding_instruction():
    """System prompt must instruct the LLM NOT to fold CV projects into work_experience."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    lowered = GENERIC_CV_EXTRACTION_PROMPT.lower()
    # Assert the SPECIFIC projects-routing prohibition, not just any negation word
    # (a global "not" would pass even if the PROJECTS rule were deleted).
    assert "not folded into" in lowered and "work_experience" in lowered, (
        "Prompt must explicitly prohibit folding projects into work_experience"
    )


def test_generic_prompt_single_home_dedup_instruction():
    """Projects block must instruct single-home/dedup against work_experience (ADR-044)."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    lowered = GENERIC_CV_EXTRACTION_PROMPT.lower()
    assert "single home" in lowered or "exactly one place" in lowered


def test_generic_prompt_null_dates_instruction():
    """Projects block must instruct: absent dates must be null, never inferred."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    # Assert the SPECIFIC date-inference prohibition, not any global "null" usage.
    assert "never infer" in GENERIC_CV_EXTRACTION_PROMPT.lower()


def test_build_generic_prompt_output_contains_cv_text():
    """The user message returned by build_generic_prompt embeds the raw CV text."""
    from applire.prompts.cv_extraction import build_generic_prompt

    result = build_generic_prompt("John Doe\nSoftware Engineer")
    assert "John Doe" in result
    assert "Software Engineer" in result


# test_build_jd_aware_prompt_output_contains_cv_text removed M5.1.3 (2026-09-11): it exercised
# build_jd_aware_prompt, which was deleted along with JD_AWARE_CV_EXTRACTION_PROMPT.


# ---------------------------------------------------------------------------
# 2. Prompt-contract tests — volunteer capabilities (ADR-044)
# ---------------------------------------------------------------------------


def test_volunteer_block_contains_achievements():
    """The volunteer_activities schema block must now expose `achievements`."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    assert "achievements" in GENERIC_CV_EXTRACTION_PROMPT


def test_volunteer_block_contains_technologies():
    """The volunteer_activities schema block must now expose `technologies`."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    assert "technologies" in GENERIC_CV_EXTRACTION_PROMPT


def test_volunteer_block_contains_responsibilities():
    """The volunteer_activities schema block must now expose `responsibilities`."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    assert "responsibilities" in GENERIC_CV_EXTRACTION_PROMPT


# ---------------------------------------------------------------------------
# 3. Mock provider tests — projects non-empty and schema-valid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_cv_analyst_returns_projects():
    """MockLLMProvider 'cv analyst' path must return at least one project entry."""
    from applire.providers.llm.mock import MockLLMProvider

    provider = MockLLMProvider()
    result = await provider.aparse_json(
        "Extract the structured profile from the following CV text and return the JSON:\n\nAnna Bauer...",
        system="You are an expert CV analyst specialised in the DACH job market.",
    )

    assert "projects" in result, "Mock response must include 'projects' key"
    assert isinstance(result["projects"], list), "'projects' must be a list"
    assert len(result["projects"]) >= 1, "Mock must return at least one project"


@pytest.mark.asyncio
async def test_mock_cv_analyst_projects_schema_valid():
    """The project entries in the mock response must validate as MasterProfileData."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.schemas.profile import MasterProfileData

    provider = MockLLMProvider()
    raw = await provider.aparse_json(
        "Extract profile",
        system="You are an expert CV analyst specialised in the DACH job market.",
    )

    # Must validate without error
    profile = MasterProfileData.model_validate(raw)
    assert len(profile.projects) >= 1
    # Non-displacement: the project must NOT have consumed/replaced a work entry
    assert len(profile.work_experience) == 2

    # Spot-check the first project
    project = profile.projects[0]
    assert project.name, "Project must have a non-empty name"
    assert isinstance(project.achievements, list)
    assert isinstance(project.technologies, list)


# ---------------------------------------------------------------------------
# 4. M5.1.3 (2026-09-11) — the positive claim behind the deletion: the browser-upload
#    path always builds the generic prompt now, even when job_id is supplied.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _upload_cv_sqlite_session():
    """In-memory SQLite session carrying just the tables upload_cv() touches
    (mirrors tests/unit/test_cv_upload.py's sqlite_session fixture)."""
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.models.user_settings import UserSettings

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    ProfileSnapshot.__table__,
                    UploadRecord.__table__,
                    User.__table__,
                    UserSettings.__table__,
                ],
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_upload_cv_builds_generic_prompt_even_when_job_id_is_supplied(
    _upload_cv_sqlite_session, tmp_path
):
    """M5.1.3 pinned positive claim: the path the browser upload actually reaches
    (services.profile.upload_cv) builds GENERIC_CV_EXTRACTION_PROMPT — and does so even
    when a caller supplies job_id. Before M5.1.3, a supplied job_id with a live
    JobAnalysis row switched extraction to JD_AWARE_CV_EXTRACTION_PROMPT; that branch is
    gone, so this is the behaviour change itself, not an assumption about it."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT
    from applire.services.profile import upload_cv
    from applire.storage.local import LocalStorageProvider

    captured_system: list[str] = []

    class _CapturingProvider:
        """Cheapest honest fake: records the system= prompt of the extraction call
        (its first call) and returns a minimal valid profile dict."""

        async def aparse_json(self, prompt, *, system, **kwargs):
            captured_system.append(system)
            return {
                "personal_info": {"name": "Jane Roe", "email": "jane@example.de"},
                "work_experience": [
                    {
                        "company": "Acme GmbH",
                        "role": "Data Scientist",
                        "start_date": "2020-01",
                        "responsibilities": ["Built ML pipelines"],
                    }
                ],
            }

    fake_provider = _CapturingProvider()
    mock_ocr = AsyncMock()

    with patch(
        "applire.services.cv_parser.extract_text",
        new=AsyncMock(return_value="Jane Roe\nData Scientist\nAcme GmbH"),
    ), patch(
        "applire.services.profile.review_and_refine",
        new=AsyncMock(side_effect=lambda **kw: kw["draft"]),
    ), patch(
        "applire.services.profile.enrich_skills",
        new=AsyncMock(side_effect=lambda p, _: p),
    ), patch(
        "applire.services.profile.annotate_expected_fields",
        new=AsyncMock(return_value=None),
    ):
        storage = LocalStorageProvider(str(tmp_path))
        await upload_cv(
            file_bytes=b"fake-pdf",
            filename="cv.pdf",
            content_type="application/pdf",
            db=_upload_cv_sqlite_session,
            provider=fake_provider,
            storage=storage,
            ocr_extractor=mock_ocr,
            job_id=uuid.uuid4(),  # supplied, but must no longer select a different prompt
        )

    assert captured_system == [GENERIC_CV_EXTRACTION_PROMPT], (
        "upload_cv must build GENERIC_CV_EXTRACTION_PROMPT for the extraction call, "
        "even when job_id is supplied — JD-aware extraction was retired M5.1.3"
    )
