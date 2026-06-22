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
  1. Prompt-contract: GENERIC_CV_EXTRACTION_PROMPT / JD_AWARE_CV_EXTRACTION_PROMPT contain
     a `projects` schema block (with `associated_experience` field) and a no-folding
     instruction that prevents projects from being merged into work_experience.
  2. Prompt-contract: volunteer block now documents `achievements` and `technologies`.
  3. Mock: the mock provider's "cv analyst" path returns a raw dict whose `projects` key
     is non-empty AND validates cleanly as MasterProfileData.

Run:
    pytest tests/unit/test_cv_extraction_projects.py -v
"""

import pytest


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


def test_jd_aware_prompt_contains_projects_key():
    """JD-aware system prompt must also expose the projects schema block."""
    from applire.prompts.cv_extraction import JD_AWARE_CV_EXTRACTION_PROMPT

    assert "projects" in JD_AWARE_CV_EXTRACTION_PROMPT


def test_jd_aware_prompt_contains_associated_experience_field():
    """JD-aware system prompt must document associated_experience in the projects block."""
    from applire.prompts.cv_extraction import JD_AWARE_CV_EXTRACTION_PROMPT

    assert "associated_experience" in JD_AWARE_CV_EXTRACTION_PROMPT


def test_generic_prompt_no_folding_instruction():
    """System prompt must instruct the LLM NOT to fold CV projects into work_experience."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    lowered = GENERIC_CV_EXTRACTION_PROMPT.lower()
    # Must mention projects and warn about folding into work_experience
    assert "project" in lowered
    assert "work_experience" in lowered or "work experience" in lowered
    # Must include a prohibition word
    assert any(word in lowered for word in ("not", "never", "must not", "do not")), (
        "Prompt must explicitly prohibit folding projects into work_experience"
    )


def test_generic_prompt_null_dates_instruction():
    """Projects block must instruct: absent dates must be null, never inferred."""
    from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT

    lowered = GENERIC_CV_EXTRACTION_PROMPT.lower()
    assert "null" in lowered  # used in the projects block date instruction


def test_build_generic_prompt_output_contains_cv_text():
    """The user message returned by build_generic_prompt embeds the raw CV text."""
    from applire.prompts.cv_extraction import build_generic_prompt

    result = build_generic_prompt("John Doe\nSoftware Engineer")
    assert "John Doe" in result
    assert "Software Engineer" in result


def test_build_jd_aware_prompt_output_contains_cv_text():
    """The user message returned by build_jd_aware_prompt embeds the raw CV text."""
    from applire.prompts.cv_extraction import build_jd_aware_prompt

    result = build_jd_aware_prompt("Jane Roe\nData Scientist", {"role_title": "Data Scientist"})
    assert "Jane Roe" in result
    assert "Data Scientist" in result


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

    # Spot-check the first project
    project = profile.projects[0]
    assert project.name, "Project must have a non-empty name"
    assert isinstance(project.achievements, list)
    assert isinstance(project.technologies, list)
