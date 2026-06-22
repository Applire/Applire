"""Unit tests for skill enrichment — schema, date calc, matching, LLM estimation."""
import pytest
from datetime import date


# ---------------------------------------------------------------------------
# Task 1: Skill schema — experience_refs field (US172: renamed from work_entry_refs)
# ---------------------------------------------------------------------------

class TestSkillWorkEntryRefs:
    def test_experience_refs_defaults_to_empty_list(self):
        from applire.schemas.profile import Skill
        skill = Skill(name="Python", category="technical", proficiency="advanced")
        assert skill.experience_refs == []

    def test_experience_refs_coerces_null_to_empty_list(self):
        from applire.schemas.profile import Skill
        skill = Skill(
            name="Python",
            category="technical",
            proficiency="advanced",
            experience_refs=None,
        )
        assert skill.experience_refs == []

    def test_experience_refs_accepts_list_of_strings(self):
        from applire.schemas.profile import Skill
        skill = Skill(
            name="Python",
            category="technical",
            proficiency="advanced",
            experience_refs=["Siemens AG", "BMW Group"],
        )
        assert skill.experience_refs == ["Siemens AG", "BMW Group"]

    def test_existing_jsonb_without_field_loads_cleanly(self):
        """Simulate a legacy JSONB record that has no work_entry_refs key."""
        from applire.schemas.profile import Skill
        skill = Skill.model_validate(
            {"name": "Django", "category": "technical", "proficiency": "intermediate"}
        )
        assert skill.experience_refs == []


# ---------------------------------------------------------------------------
# Task 2: Date parsing and range calculation
# ---------------------------------------------------------------------------

class TestParsePartialDate:
    def test_year_only(self):
        from applire.services.skill_enrichment import _parse_partial_date
        assert _parse_partial_date("2020") == date(2020, 1, 1)

    def test_year_month(self):
        from applire.services.skill_enrichment import _parse_partial_date
        assert _parse_partial_date("2020-06") == date(2020, 6, 1)

    def test_full_date(self):
        from applire.services.skill_enrichment import _parse_partial_date
        assert _parse_partial_date("2020-06-15") == date(2020, 6, 15)


class TestCalculateYears:
    def test_empty_list_returns_zero(self):
        from applire.services.skill_enrichment import _calculate_years
        assert _calculate_years([]) == 0

    def test_single_entry_one_year(self):
        from applire.services.skill_enrichment import _calculate_years
        result = _calculate_years([(date(2020, 1, 1), date(2021, 1, 1))])
        assert result == 1

    def test_minimum_one_for_short_tenure(self):
        from applire.services.skill_enrichment import _calculate_years
        # 3 months — rounds to 0 but minimum is 1
        result = _calculate_years([(date(2020, 1, 1), date(2020, 4, 1))])
        assert result == 1

    def test_non_overlapping_ranges_sum(self):
        from applire.services.skill_enrichment import _calculate_years
        result = _calculate_years([
            (date(2018, 1, 1), date(2019, 1, 1)),  # 1 year
            (date(2020, 1, 1), date(2022, 1, 1)),  # 2 years
        ])
        assert result == 3

    def test_overlapping_ranges_not_double_counted(self):
        from applire.services.skill_enrichment import _calculate_years
        # Two concurrent roles, overlapping 2019-2020
        result = _calculate_years([
            (date(2018, 1, 1), date(2020, 1, 1)),  # 2 years
            (date(2019, 1, 1), date(2021, 1, 1)),  # overlaps — merged to 2018–2021 = 3 yrs
        ])
        assert result == 3

    def test_fully_contained_range_not_double_counted(self):
        from applire.services.skill_enrichment import _calculate_years
        result = _calculate_years([
            (date(2018, 1, 1), date(2022, 1, 1)),  # 4 years
            (date(2019, 1, 1), date(2020, 1, 1)),  # fully inside — no extra time added
        ])
        assert result == 4

    def test_adjacent_ranges_are_summed(self):
        from applire.services.skill_enrichment import _calculate_years
        result = _calculate_years([
            (date(2018, 1, 1), date(2020, 1, 1)),
            (date(2020, 1, 1), date(2022, 1, 1)),
        ])
        assert result == 4

    def test_six_years_rounds_correctly(self):
        from applire.services.skill_enrichment import _calculate_years
        result = _calculate_years([(date(2016, 1, 1), date(2022, 1, 1))])
        assert result == 6

    def test_zero_duration_range_returns_minimum_one(self):
        from applire.services.skill_enrichment import _calculate_years
        # A zero-duration range (end == start) — treated as minimum 1, not 0
        result = _calculate_years([(date(2020, 6, 1), date(2020, 6, 1))])
        assert result == 1


# ---------------------------------------------------------------------------
# Task 3: Proficiency thresholds and floor rule
# ---------------------------------------------------------------------------

class TestYearsToProficiency:
    def test_zero_years_is_basic(self):
        from applire.services.skill_enrichment import _years_to_proficiency
        assert _years_to_proficiency(0) == "basic"

    def test_one_year_is_intermediate(self):
        from applire.services.skill_enrichment import _years_to_proficiency
        assert _years_to_proficiency(1) == "intermediate"

    def test_two_years_is_intermediate(self):
        from applire.services.skill_enrichment import _years_to_proficiency
        assert _years_to_proficiency(2) == "intermediate"

    def test_three_years_is_advanced(self):
        from applire.services.skill_enrichment import _years_to_proficiency
        assert _years_to_proficiency(3) == "advanced"

    def test_five_years_is_advanced(self):
        from applire.services.skill_enrichment import _years_to_proficiency
        assert _years_to_proficiency(5) == "advanced"

    def test_six_years_is_expert(self):
        from applire.services.skill_enrichment import _years_to_proficiency
        assert _years_to_proficiency(6) == "expert"

    def test_ten_years_is_expert(self):
        from applire.services.skill_enrichment import _years_to_proficiency
        assert _years_to_proficiency(10) == "expert"


class TestApplyFloor:
    def test_calculated_higher_than_existing(self):
        from applire.services.skill_enrichment import _apply_floor
        # calculated=advanced (rank 2) > existing=basic (rank 0) → use advanced
        assert _apply_floor("advanced", "basic") == "advanced"

    def test_existing_higher_than_calculated(self):
        from applire.services.skill_enrichment import _apply_floor
        # calculated=basic (rank 0) < existing=expert (rank 3) → keep expert
        assert _apply_floor("basic", "expert") == "expert"

    def test_equal_levels_returns_either(self):
        from applire.services.skill_enrichment import _apply_floor
        assert _apply_floor("intermediate", "intermediate") == "intermediate"

    def test_calculated_expert_upgrades_intermediate(self):
        from applire.services.skill_enrichment import _apply_floor
        assert _apply_floor("expert", "intermediate") == "expert"

    def test_never_downgrades_expert_to_advanced(self):
        from applire.services.skill_enrichment import _apply_floor
        assert _apply_floor("advanced", "expert") == "expert"


# ---------------------------------------------------------------------------
# Task 4: Deterministic match phase
# ---------------------------------------------------------------------------

class TestMatchAndEnrich:
    def _make_profile(self, skills, work_experience):
        from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
        return MasterProfileData(
            skills=[Skill(**s) for s in skills],
            work_experience=[WorkEntry(**w) for w in work_experience],
        )

    def test_case_insensitive_match(self):
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "python", "category": "technical", "proficiency": "intermediate"}],
            work_experience=[{
                "company": "Siemens AG",
                "role": "Engineer",
                "start_date": "2020-01",
                "end_date": "2021-01",
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(enriched) == 1
        assert len(unmatched) == 0
        skill = enriched[0]
        assert skill.experience_refs == ["Siemens AG"]
        assert skill.source == "deterministic"
        assert skill.years_experience == 1

    def test_no_match_goes_to_unmatched(self):
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "Kubernetes", "category": "technical", "proficiency": "basic"}],
            work_experience=[{
                "company": "Siemens AG",
                "role": "Engineer",
                "start_date": "2020-01",
                "end_date": "2021-01",
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(enriched) == 0
        assert len(unmatched) == 1
        assert unmatched[0].name == "Kubernetes"

    def test_multiple_matching_entries_combined(self):
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "Python", "category": "technical", "proficiency": "intermediate"}],
            work_experience=[
                {
                    "company": "Siemens AG",
                    "role": "Junior Engineer",
                    "start_date": "2018-01",
                    "end_date": "2020-01",
                    "technologies": ["Python"],
                },
                {
                    "company": "BMW Group",
                    "role": "Senior Engineer",
                    "start_date": "2021-01",
                    "end_date": "2024-01",
                    "technologies": ["Python", "Django"],
                },
            ],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(enriched) == 1
        skill = enriched[0]
        assert set(skill.experience_refs) == {"Siemens AG", "BMW Group"}
        assert skill.years_experience == 5  # 2 + 3 non-overlapping
        assert skill.proficiency == "advanced"  # 5 years → advanced

    def test_floor_rule_preserves_higher_existing_proficiency(self):
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "Python", "category": "technical", "proficiency": "expert"}],
            work_experience=[{
                "company": "Startup",
                "role": "Dev",
                "start_date": "2023-01",
                "end_date": "2024-01",
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        skill = enriched[0]
        assert skill.years_experience == 1
        # Calculated would be intermediate (1 yr) but existing is expert — floor keeps expert
        assert skill.proficiency == "expert"

    def test_null_end_date_treated_as_current(self):
        from applire.services.skill_enrichment import _match_and_enrich
        from datetime import date
        profile = self._make_profile(
            skills=[{"name": "Python", "category": "technical", "proficiency": "basic"}],
            work_experience=[{
                "company": "Current Corp",
                "role": "Dev",
                "start_date": "2020-01",
                "end_date": None,  # current role
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        skill = enriched[0]
        # years since 2020 — should be >= 5 at time of writing (2026-04-16)
        assert skill.years_experience >= 5
        assert skill.source == "deterministic"

    def test_language_skills_passed_through_unchanged(self):
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "German", "category": "language", "proficiency": "expert"}],
            work_experience=[{
                "company": "Siemens AG",
                "role": "Engineer",
                "start_date": "2020-01",
                "end_date": "2021-01",
                "technologies": ["German"],  # even if listed, language skills are skipped
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        # Language skills go to enriched (passthrough) with NO modification
        assert len(enriched) == 1
        assert len(unmatched) == 0
        skill = enriched[0]
        assert skill.source is None  # unchanged — no source tag added
        assert skill.experience_refs == []

    def test_domain_skills_passed_through_unchanged(self):
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "Healthcare", "category": "domain", "proficiency": "advanced"}],
            work_experience=[],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(enriched) == 1
        assert enriched[0].name == "Healthcare"
        assert enriched[0].experience_refs == []

    def test_entry_with_null_start_date_skipped(self):
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "Python", "category": "technical", "proficiency": "intermediate"}],
            work_experience=[{
                "company": "Siemens AG",
                "role": "Engineer",
                "start_date": None,  # can't calculate a range
                "end_date": "2021-01",
                "technologies": ["Python"],
            }],
        )
        # Entry has no start_date → can't form a range → skill goes to unmatched
        enriched, unmatched = _match_and_enrich(profile)
        assert len(unmatched) == 1
        assert unmatched[0].name == "Python"


# ---------------------------------------------------------------------------
# Task 6: LLM estimation phase and enrich_skills()
# ---------------------------------------------------------------------------

import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_enrich_skills_empty_profile_returns_unchanged():
    from applire.schemas.profile import MasterProfileData
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData()
    mock_provider = AsyncMock()
    result = await enrich_skills(profile, mock_provider)

    assert result.skills == []
    mock_provider.aparse_json.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_skills_unmatched_calls_llm_estimation():
    from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[
            Skill(name="Agile", category="soft", proficiency="intermediate"),
        ],
        work_experience=[
            WorkEntry(
                company="Siemens AG",
                role="Scrum Master",
                start_date="2018-01",
                end_date="2021-01",
                technologies=["Jira"],  # "Agile" not in technologies
            )
        ],
    )
    mock_provider = AsyncMock()
    # LLM returns 4 years for Agile
    mock_provider.aparse_json.return_value = {"Agile": 4}

    result = await enrich_skills(profile, mock_provider)

    mock_provider.aparse_json.assert_called_once()
    skill = result.skills[0]
    assert skill.name == "Agile"
    assert skill.years_experience == 4
    assert skill.source == "llm_estimated"
    assert skill.proficiency == "advanced"  # 4 years → advanced
    assert skill.experience_refs == []


@pytest.mark.asyncio
async def test_enrich_skills_floor_applied_to_llm_estimate():
    from applire.schemas.profile import MasterProfileData, Skill
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[
            Skill(name="Leadership", category="soft", proficiency="expert"),
        ],
        work_experience=[],
    )
    mock_provider = AsyncMock()
    # LLM estimates only 1 year — should not downgrade existing expert
    mock_provider.aparse_json.return_value = {"Leadership": 1}

    result = await enrich_skills(profile, mock_provider)

    skill = result.skills[0]
    assert skill.years_experience == 1
    assert skill.proficiency == "expert"  # floor preserved


@pytest.mark.asyncio
async def test_enrich_skills_null_llm_estimate_leaves_skill_without_years():
    from applire.schemas.profile import MasterProfileData, Skill
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[
            Skill(name="Blockchain", category="technical", proficiency="basic"),
        ],
        work_experience=[],
    )
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = {"Blockchain": None}

    result = await enrich_skills(profile, mock_provider)

    skill = result.skills[0]
    assert skill.years_experience is None
    assert skill.source == "llm_estimated"
    assert skill.proficiency == "basic"  # unchanged


@pytest.mark.asyncio
async def test_enrich_skills_does_not_mutate_input():
    from applire.schemas.profile import MasterProfileData, Skill
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[Skill(name="Python", category="technical", proficiency="basic")],
        work_experience=[],
    )
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = {"Python": 3}

    result = await enrich_skills(profile, mock_provider)

    # Original profile untouched
    assert profile.skills[0].years_experience is None
    assert profile.skills[0].source is None
    # New profile enriched
    assert result.skills[0].years_experience == 3


@pytest.mark.asyncio
async def test_enrich_skills_language_skills_not_sent_to_llm():
    from applire.schemas.profile import MasterProfileData, Skill
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[
            Skill(name="German", category="language", proficiency="expert"),
            Skill(name="Python", category="technical", proficiency="basic"),
        ],
        work_experience=[],
    )
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = {"Python": 2}

    result = await enrich_skills(profile, mock_provider)

    # Only Python sent to LLM — verify the call's arguments
    call_args = mock_provider.aparse_json.call_args
    prompt_arg = call_args[0][0]  # first positional arg is the user prompt
    assert "Python" in prompt_arg
    assert "German" not in prompt_arg

    # German skill passes through unchanged
    german = next(s for s in result.skills if s.name == "German")
    assert german.source is None
    assert german.experience_refs == []


# ---------------------------------------------------------------------------
# Task 7: CV extraction review prompt smoke tests
# ---------------------------------------------------------------------------

class TestCVExtractionReviewPrompt:
    def test_review_system_prompt_references_work_experience(self):
        from applire.prompts.review_cv_extraction import CV_EXTRACTION_REVIEW_SYSTEM_PROMPT
        assert "work_experience" in CV_EXTRACTION_REVIEW_SYSTEM_PROMPT
        # Must NOT use the LinkedIn field names
        assert "work_history" not in CV_EXTRACTION_REVIEW_SYSTEM_PROMPT

    def test_review_prompt_includes_source_and_draft(self):
        from applire.prompts.review_cv_extraction import build_cv_extraction_review_prompt
        prompt = build_cv_extraction_review_prompt(
            "Max Mustermann, Software Engineer",
            {"work_experience": [{"company": "Siemens AG", "role": "Engineer"}]},
        )
        assert "Max Mustermann" in prompt
        assert "Siemens AG" in prompt

    def test_retry_prompt_includes_feedback_and_previous_draft(self):
        from applire.prompts.review_cv_extraction import build_cv_extraction_retry_prompt
        prompt = build_cv_extraction_retry_prompt(
            previous_draft={"work_experience": [{"company": "Siemens AG"}]},
            feedback="Missing work entries",
        )
        assert "Missing work entries" in prompt
        assert "Siemens AG" in prompt


# ---------------------------------------------------------------------------
# Task 10: patch_profile_section with provider
# ---------------------------------------------------------------------------

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch


@pytest_asyncio.fixture
async def sqlite_session_for_patch():
    """In-memory SQLite session with MasterProfile table."""
    from applire.db.session import Base
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[MasterProfile.__table__, User.__table__],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_patch_profile_section_without_provider_skips_enrichment(sqlite_session_for_patch):
    """Patching without a provider must not call enrich_skills."""
    from applire.schemas.profile import MasterProfileData, ProfileMetadata
    from applire.models.profile import MasterProfile
    from applire.services.profile import patch_profile_section
    from datetime import datetime, timezone

    # Seed a profile
    profile_data = MasterProfileData(
        skills=[],
        work_experience=[],
    )
    profile_data.metadata = ProfileMetadata(
        completeness_score=0.0,
        created_via="manual",
        created_at=datetime.now(timezone.utc),
        last_updated=datetime.now(timezone.utc),
    )
    record = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
    sqlite_session_for_patch.add(record)
    await sqlite_session_for_patch.commit()

    with patch("applire.services.profile.enrich_skills") as mock_enrich:
        await patch_profile_section(
            section="skills",
            value=[{"name": "Python", "category": "technical", "proficiency": "basic"}],
            db=sqlite_session_for_patch,
        )
        mock_enrich.assert_not_called()


@pytest.mark.asyncio
async def test_patch_profile_section_with_provider_calls_enrich_for_skills(sqlite_session_for_patch):
    """Patching skills with a provider must call enrich_skills."""
    from applire.schemas.profile import MasterProfileData, ProfileMetadata
    from applire.models.profile import MasterProfile
    from applire.services.profile import patch_profile_section
    from datetime import datetime, timezone

    profile_data = MasterProfileData(skills=[], work_experience=[])
    profile_data.metadata = ProfileMetadata(
        completeness_score=0.0,
        created_via="manual",
        created_at=datetime.now(timezone.utc),
        last_updated=datetime.now(timezone.utc),
    )
    record = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
    sqlite_session_for_patch.add(record)
    await sqlite_session_for_patch.commit()

    mock_provider = AsyncMock()

    with patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)) as mock_enrich:
        await patch_profile_section(
            section="skills",
            value=[{"name": "Python", "category": "technical", "proficiency": "basic"}],
            db=sqlite_session_for_patch,
            provider=mock_provider,
        )
        mock_enrich.assert_called_once()


@pytest.mark.asyncio
async def test_patch_personal_info_with_provider_does_not_call_enrich(sqlite_session_for_patch):
    """Patching personal_info (non-skills section) must not call enrich_skills even with provider."""
    from applire.schemas.profile import MasterProfileData, ProfileMetadata
    from applire.models.profile import MasterProfile
    from applire.services.profile import patch_profile_section
    from datetime import datetime, timezone

    profile_data = MasterProfileData(skills=[], work_experience=[])
    profile_data.metadata = ProfileMetadata(
        completeness_score=0.0,
        created_via="manual",
        created_at=datetime.now(timezone.utc),
        last_updated=datetime.now(timezone.utc),
    )
    record = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
    sqlite_session_for_patch.add(record)
    await sqlite_session_for_patch.commit()

    mock_provider = AsyncMock()

    with patch("applire.services.profile.enrich_skills") as mock_enrich:
        await patch_profile_section(
            section="personal_info",
            value={"name": "Max Mustermann"},
            db=sqlite_session_for_patch,
            provider=mock_provider,
        )
        mock_enrich.assert_not_called()
