# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Founder ruling B-2 (2026-09-20) — `job_analyses.education_requirement`.

Founder-UAT F-6 measured "Master's degree", "Computer science", "Data science" and
"Engineering" in `required_skills` — four entries minted from ONE posting sentence,
each of them a requirement slot in the ADR-035 denominator that no CV bullet can
ever evidence. Prompt v9 stopped emitting them there; without a home the bar would
simply have been lost from every derived surface, so ruling B-2 gave it one:
migration 0068, ONE nullable text column carrying the posting's own words.

What these tests pin, and why each one exists:
  * the honest NULL is stored as NULL, never laundered into `""` — the exact
    distinction migration 0067 had to restore for `seniority_level` after
    `or ""` made "the posting stated no tier" and "we lost the tier" the same
    stored value;
  * the field is ONE string, so an unconverged review loop returning a LIST of
    fields of study (the shape the whole change exists to prevent) is flattened
    at the payload boundary rather than crashing the write or reaching the ORM;
  * the reviewer's own view carries it, so its grounding check can see it;
  * the agent door marks it as untrusted job-posting text.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _payload(**overrides) -> dict:
    data = {
        "company_name": "KION Group",
        "role_title": "Team Lead GenAI Platform",
        "required_skills": ["LLMOps", "Vector databases"],
        "nice_to_have_skills": [],
        "keywords": ["GenAI"],
        "seniority_level": "Lead",
        "company_culture_signals": [],
        "language_requirement": "English (C1)",
        "education_requirement": (
            "Master's degree in computer science, data science, engineering"
        ),
        "berufsbild_code": None,
        "berufsbild_label": None,
    }
    data.update(overrides)
    return data


def _provider(payload: dict):
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=payload)
    return provider


_JD = (
    "Team Lead GenAI Platform at KION Group. Master's degree in computer science, "
    "data science, engineering. You own LLMOps and vector databases."
)


@pytest.mark.asyncio
async def test_the_postings_own_education_wording_is_persisted_as_one_string(db):
    from applire.services.job import analyze_jd

    result = await analyze_jd(_JD, db, _provider(_payload()))

    assert result.education_requirement == (
        "Master's degree in computer science, data science, engineering"
    )
    # …and it did NOT also get split across the concept lists (v10's rule: this
    # field is the bar's only home). The payload above is the model complying.
    assert "Master's degree" not in result.required_skills
    assert "Computer science" not in result.required_skills


@pytest.mark.asyncio
async def test_a_posting_that_states_no_education_bar_stores_null_not_empty_string(db):
    from applire.models.job import JobAnalysis
    from applire.services.job import analyze_jd

    result = await analyze_jd(
        _JD + " ",
        db,
        _provider(_payload(education_requirement=None)),
    )

    assert result.education_requirement is None
    row = await db.get(JobAnalysis, result.id)
    assert row.education_requirement is None, (
        "an honest 'the posting states no bar' must be NULL — migration 0067 had to "
        "undo exactly this laundering for seniority_level"
    )


@pytest.mark.asyncio
async def test_a_whitespace_only_value_is_the_model_saying_nothing(db):
    from applire.services.job import analyze_jd

    result = await analyze_jd(
        _JD + "  ",
        db,
        _provider(_payload(education_requirement="   ")),
    )
    assert result.education_requirement is None


@pytest.mark.asyncio
async def test_a_list_of_fields_of_study_is_flattened_not_crashed(db):
    """The shape this field exists to prevent, arriving in the field itself.

    An unconverged review loop is free to drift a payload's SHAPE, so the
    boundary coerces rather than trusting (charter run #8's rule, `_JD_TEXT_FIELDS`).
    Losing the information would trade a crash for silent data loss.
    """
    from applire.services.job import analyze_jd

    result = await analyze_jd(
        _JD + "   ",
        db,
        _provider(
            _payload(
                education_requirement=[
                    "Master's degree",
                    "Computer science",
                    "Engineering",
                ]
            )
        ),
    )
    assert result.education_requirement == (
        "Master's degree, Computer science, Engineering"
    )


def test_the_reviewer_view_carries_the_field_so_its_check_can_see_it():
    from applire.services.jd_grounding import JD_SCHEMA_KEYS, reviewer_view

    assert "education_requirement" in JD_SCHEMA_KEYS
    view = reviewer_view(_payload() | {"level_changes": [{"concept": "x"}]})
    assert view["education_requirement"] == (
        "Master's degree in computer science, data science, engineering"
    )
    # The allowlist still fails in the safe direction (ADR-078 clause 2).
    assert "level_changes" not in view


def test_the_extraction_prompt_asks_for_it_and_names_its_only_home():
    from applire.prompts.job_analysis import SYSTEM_PROMPT

    assert '"education_requirement"' in SYSTEM_PROMPT
    assert "EDUCATION REQUIREMENT" in SYSTEM_PROMPT
    # v10's two load-bearing clauses: one string, and not restated in the lists.
    assert "NOT split into the fields of study it names" in SYSTEM_PROMPT
    assert "this field is its only home" in SYSTEM_PROMPT


def test_the_auditor_grounds_it_and_its_approval_bar_admits_the_finding():
    from applire.prompts.review_job_analysis import (
        JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT as reviewer,
    )

    assert "EDUCATION REQUIREMENT GROUNDING" in reviewer
    # A check outside the bar's own closed list would be unusable: the bar says
    # "checks 1-5 below name the full list; nothing outside it is material".
    assert reviewer.count("education bar") >= 2, (
        "both the approval bar and the blocking-scope sentence must name it"
    )


def test_the_agent_door_marks_it_as_untrusted_posting_text():
    from applire.mcp.server import _JD_DERIVED_FIELDS

    assert "education_requirement" in _JD_DERIVED_FIELDS["analyze_jd"]


def test_migration_0068_chains_onto_the_previous_head():
    import importlib.util
    from pathlib import Path

    import applire

    root = Path(applire.__path__[0]).parent
    path = root / "alembic" / "versions" / "0068_job_education_requirement.py"
    spec = importlib.util.spec_from_file_location("m0068", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0068"
    assert module.down_revision == "0067"
