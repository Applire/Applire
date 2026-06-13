"""
Iteration 11 — Master Profile Data Foundation (unit tests)

Done when:
  - GET /api/profile returns a fully structured MasterProfileResponse.
  - Uploading two conflicting CVs flags a conflict without data loss.
  - enrichment_history reflects every change.
  - All unit tests pass.

Covers:
  - Pydantic model defaults and validation
  - Legacy field migration (work_history, contact, skills list)
  - Completeness score weighting
  - Date overlap / contradiction helpers
  - Merge work experience (accumulation, role_aliases, conflict detection)
  - Merge skills (higher proficiency/years wins, no conflicts generated)
  - Full merge_profiles orchestration (education, languages, certs, personal info,
    professional summary, publications, volunteer activities)
  - SQLite persistence via ORM (JSONB.with_variant(JSON) — no raw DDL needed)
  - Conflict and ConflictResolutionRequest schemas

No Docker or real Postgres required.

Run:
    pytest tests/unit/ -v
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite session.  JSONB.with_variant(JSON) lets create_all work directly."""
    from applire.db.session import Base  # noqa: F401 — ensures all models are registered
    import applire.models.profile  # noqa: F401
    import applire.models.job     # noqa: F401
    import applire.models.cv      # noqa: F401
    import applire.models.gap     # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.user    # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _work_entry(**kwargs):
    from applire.schemas.profile import WorkEntry
    defaults = {"company": "Acme GmbH", "role": "Software Developer", "start_date": "2020-01"}
    return WorkEntry(**{**defaults, **kwargs})


def _profile(**kwargs):
    from applire.schemas.profile import MasterProfileData
    return MasterProfileData(**kwargs)


# ---------------------------------------------------------------------------
# TestSchemaDefaults
# ---------------------------------------------------------------------------


class TestSchemaDefaults:
    def test_work_entry_list_fields_default_to_empty(self):
        e = _work_entry()
        assert e.role_aliases == []
        assert e.responsibilities == []
        assert e.achievements == []
        assert e.technologies == []

    def test_work_entry_optional_fields_default_to_none(self):
        e = _work_entry()
        assert e.location is None
        assert e.end_date is None
        assert e.industry_context is None
        assert e.team_size is None
        assert e.budget_managed is None

    def test_skill_default_proficiency_and_category(self):
        from applire.schemas.profile import Skill
        s = Skill(name="Python")
        assert s.proficiency == "intermediate"
        assert s.category == "technical"

    def test_master_profile_data_list_sections_default_to_empty(self):
        p = _profile()
        assert p.work_experience == []
        assert p.education == []
        assert p.certifications == []
        assert p.skills == []
        assert p.languages == []
        assert p.publications == []
        assert p.volunteer_activities == []

    def test_personal_info_name_defaults_to_empty_string(self):
        from applire.schemas.profile import PersonalInfo
        pi = PersonalInfo()
        assert pi.name == ""
        assert pi.email is None
        assert pi.phone is None

    def test_conflict_resolved_defaults_to_false(self):
        from applire.schemas.profile import Conflict
        c = Conflict(section="work_experience", field="start_date",
                     existing_value="2020-01", incoming_value="2020-03", source="cv_upload")
        assert c.resolved is False

    def test_conflict_has_auto_generated_uuid(self):
        from applire.schemas.profile import Conflict
        c1 = Conflict(section="s", field="f", existing_value="a", incoming_value="b", source="x")
        c2 = Conflict(section="s", field="f", existing_value="a", incoming_value="b", source="x")
        assert c1.conflict_id != c2.conflict_id
        uuid.UUID(c1.conflict_id)  # must be valid UUID — raises if not

    def test_conflict_resolution_request_accepts_valid_literals(self):
        from applire.schemas.profile import ConflictResolutionRequest
        for resolution in ("existing", "incoming", "manual"):
            req = ConflictResolutionRequest(resolution=resolution)
            assert req.resolution == resolution

    def test_conflict_resolution_request_manual_value_can_be_none(self):
        from applire.schemas.profile import ConflictResolutionRequest
        req = ConflictResolutionRequest(resolution="manual", value=None)
        assert req.value is None

    def test_conflict_resolution_request_manual_value_can_be_set(self):
        from applire.schemas.profile import ConflictResolutionRequest
        req = ConflictResolutionRequest(resolution="manual", value="2020-02")
        assert req.value == "2020-02"


# ---------------------------------------------------------------------------
# TestLegacyMigration
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    def test_work_history_migrates_to_work_experience(self):
        from applire.schemas.profile import MasterProfileData
        data = {"work_history": [{"company": "Old Corp", "role": "Dev"}]}
        p = MasterProfileData(**data)
        assert len(p.work_experience) == 1
        assert p.work_experience[0].company == "Old Corp"

    def test_bullets_migrates_to_responsibilities(self):
        from applire.schemas.profile import MasterProfileData
        data = {"work_history": [{"company": "X", "role": "Y", "bullets": ["Task A", "Task B"]}]}
        p = MasterProfileData(**data)
        assert p.work_experience[0].responsibilities == ["Task A", "Task B"]

    def test_contact_migrates_to_personal_info(self):
        from applire.schemas.profile import MasterProfileData
        data = {"contact": {"name": "Alice", "email": "alice@example.com"}}
        p = MasterProfileData(**data)
        assert p.personal_info.name == "Alice"
        assert p.personal_info.email == "alice@example.com"

    def test_linkedin_key_migrates_to_linkedin_url(self):
        from applire.schemas.profile import MasterProfileData
        data = {"contact": {"name": "Bob", "linkedin": "https://linkedin.com/in/bob"}}
        p = MasterProfileData(**data)
        assert p.personal_info.linkedin_url == "https://linkedin.com/in/bob"

    def test_skills_str_list_migrates_to_skill_objects(self):
        from applire.schemas.profile import MasterProfileData, Skill
        data = {"skills": ["Python", "Docker"]}
        p = MasterProfileData(**data)
        assert len(p.skills) == 2
        assert all(isinstance(s, Skill) for s in p.skills)
        assert p.skills[0].name == "Python"
        assert p.skills[0].proficiency == "intermediate"
        assert p.skills[0].category == "technical"


# ---------------------------------------------------------------------------
# TestCompletenessScore
# ---------------------------------------------------------------------------


class TestCompletenessScore:
    def test_empty_profile_score_is_zero(self):
        p = _profile()
        assert p.calculate_completeness() == 0.0

    def test_profile_with_work_experience_only_scores_0_30(self):
        p = _profile(work_experience=[_work_entry()])
        assert p.calculate_completeness() == 0.30

    def test_profile_with_work_and_education_scores_0_50(self):
        from applire.schemas.profile import EducationEntry
        p = _profile(
            work_experience=[_work_entry()],
            education=[EducationEntry(institution="TU Berlin", degree="B.Sc.")],
        )
        assert p.calculate_completeness() == 0.50

    def test_fully_populated_profile_score_is_near_1(self):
        from applire.schemas.profile import (
            Certification, EducationEntry, Language, MasterProfileData,
            PersonalInfo, ProfessionalSummary, Publication, Skill, VolunteerActivity,
        )
        p = MasterProfileData(
            personal_info=PersonalInfo(name="Ana"),
            professional_summary=ProfessionalSummary(en="Summary"),
            work_experience=[_work_entry()],
            education=[EducationEntry(institution="ETH", degree="M.Sc.")],
            skills=[Skill(name="Python")],
            languages=[Language(language="German", level="C2")],
            certifications=[Certification(name="AWS", issuing_organization="Amazon")],
            publications=[Publication(title="Paper")],
            volunteer_activities=[VolunteerActivity(role="Mentor", organization="Code4Good")],
        )
        score = p.calculate_completeness()
        assert 0.95 <= score <= 1.0

    def test_completeness_returns_float_rounded_to_two_decimals(self):
        from applire.schemas.profile import Skill
        p = _profile(skills=[Skill(name="Python")])
        score = p.calculate_completeness()
        assert isinstance(score, float)
        assert score == round(score, 2)


# ---------------------------------------------------------------------------
# TestDateHelpers
# ---------------------------------------------------------------------------


class TestDateHelpers:
    def _overlap(self, *args):
        from applire.services.profile.merge import _dates_overlap
        return _dates_overlap(*args)

    def _contradict(self, a, b):
        from applire.services.profile.merge import _dates_contradict
        return _dates_contradict(a, b)

    def _merge_lists(self, a, b):
        from applire.services.profile.merge import _merge_str_lists
        return _merge_str_lists(a, b)

    def test_same_period_overlaps(self):
        assert self._overlap("2020-01", "2022-12", "2020-01", "2022-12") is True

    def test_adjacent_periods_overlap(self):
        assert self._overlap("2018-01", "2020-06", "2019-01", "2021-12") is True

    def test_non_overlapping_periods_do_not_overlap(self):
        assert self._overlap("2015-01", "2017-12", "2019-01", "2021-12") is False

    def test_missing_start_date_returns_true(self):
        # Safe default: treat unknown dates as overlapping
        assert self._overlap(None, "2022-12", "2020-01", "2022-12") is True
        assert self._overlap("2020-01", "2022-12", None, "2022-12") is True

    def test_open_ended_job_overlaps_with_any_recent_date(self):
        # end_date=None means current job — should overlap with anything recent
        assert self._overlap("2020-01", None, "2022-01", "2023-12") is True

    def test_year_only_dates_overlap(self):
        assert self._overlap("2020", "2022", "2021", "2023") is True

    def test_contradict_different_months(self):
        assert self._contradict("2020-01", "2020-03") is True

    def test_contradict_different_years(self):
        assert self._contradict("2019-06", "2020-06") is True

    def test_contradict_same_month_is_not_contradiction(self):
        assert self._contradict("2020-01", "2020-01") is False

    def test_contradict_year_only_vs_year_month_is_not_contradiction(self):
        # "2020" vs "2020-01" — just different precision, not a true conflict
        assert self._contradict("2020", "2020-01") is False

    def test_contradict_missing_date_is_not_contradiction(self):
        assert self._contradict(None, "2020-01") is False
        assert self._contradict("2020-01", None) is False
        assert self._contradict(None, None) is False

    def test_merge_str_lists_deduplicates_case_insensitively(self):
        result = self._merge_lists(["Python", "Docker"], ["python", "Kubernetes"])
        assert result.count("Python") == 1  # deduplicated
        assert "Kubernetes" in result

    def test_merge_str_lists_preserves_original_order(self):
        result = self._merge_lists(["A", "B"], ["C", "D"])
        assert result == ["A", "B", "C", "D"]

    def test_merge_str_lists_empty_inputs(self):
        assert self._merge_lists([], []) == []
        assert self._merge_lists(["A"], []) == ["A"]
        assert self._merge_lists([], ["B"]) == ["B"]


# ---------------------------------------------------------------------------
# TestMergeWorkExperience
# ---------------------------------------------------------------------------


class TestMergeWorkExperience:
    def _merge(self, existing, incoming, source="cv_upload"):
        from applire.services.profile.merge import _merge_work_experience
        # [:3] — these tests predate the US145 structured-changes 4th element
        merged, added, conflicts, _changes = _merge_work_experience(existing, incoming, source)
        return merged, added, conflicts

    def test_same_company_same_period_accumulates_into_one_entry(self):
        existing = [_work_entry(company="Acme", start_date="2020-01", end_date="2022-12")]
        incoming = [_work_entry(company="Acme", role="Senior Dev", start_date="2020-01", end_date="2022-12")]
        result, added, conflicts = self._merge(existing, incoming)
        assert len(result) == 1  # no duplicate

    def test_different_role_title_becomes_alias(self):
        existing = [_work_entry(company="Acme", role="Team Lead", start_date="2020-01", end_date="2022-12")]
        incoming = [_work_entry(company="Acme", role="2nd Level Support", start_date="2020-01", end_date="2022-12")]
        result, _, _ = self._merge(existing, incoming)
        assert "2nd Level Support" in result[0].role_aliases

    def test_existing_role_not_added_to_aliases(self):
        existing = [_work_entry(company="Acme", role="Team Lead", start_date="2020-01")]
        incoming = [_work_entry(company="Acme", role="Team Lead", start_date="2020-01")]
        result, _, _ = self._merge(existing, incoming)
        assert result[0].role_aliases == []

    def test_responsibilities_are_unioned(self):
        existing = [_work_entry(company="Acme", start_date="2020-01",
                                responsibilities=["Led team of 5"])]
        incoming = [_work_entry(company="Acme", start_date="2020-01",
                                responsibilities=["Handled on-call rotation"])]
        result, _, _ = self._merge(existing, incoming)
        assert "Led team of 5" in result[0].responsibilities
        assert "Handled on-call rotation" in result[0].responsibilities

    def test_technologies_are_unioned(self):
        existing = [_work_entry(company="Acme", start_date="2020-01", technologies=["Python"])]
        incoming = [_work_entry(company="Acme", start_date="2020-01", technologies=["Docker"])]
        result, _, _ = self._merge(existing, incoming)
        assert "Python" in result[0].technologies
        assert "Docker" in result[0].technologies

    def test_duplicate_responsibilities_are_not_repeated(self):
        existing = [_work_entry(company="Acme", start_date="2020-01",
                                responsibilities=["Task A"])]
        incoming = [_work_entry(company="Acme", start_date="2020-01",
                                responsibilities=["task a"])]  # case-insensitive duplicate
        result, _, _ = self._merge(existing, incoming)
        assert len(result[0].responsibilities) == 1

    def test_gap_fill_industry_context_when_existing_empty(self):
        existing = [_work_entry(company="Acme", start_date="2020-01", industry_context=None)]
        incoming = [_work_entry(company="Acme", start_date="2020-01", industry_context="FinTech")]
        result, added, _ = self._merge(existing, incoming)
        assert result[0].industry_context == "FinTech"
        assert any("industry_context" in a for a in added)

    def test_gap_fill_team_size_when_existing_none(self):
        existing = [_work_entry(company="Acme", start_date="2020-01", team_size=None)]
        incoming = [_work_entry(company="Acme", start_date="2020-01", team_size=8)]
        result, _, _ = self._merge(existing, incoming)
        assert result[0].team_size == 8

    def test_existing_industry_context_not_overwritten(self):
        existing = [_work_entry(company="Acme", start_date="2020-01", industry_context="SaaS")]
        incoming = [_work_entry(company="Acme", start_date="2020-01", industry_context="FinTech")]
        result, _, _ = self._merge(existing, incoming)
        assert result[0].industry_context == "SaaS"

    def test_date_contradiction_on_start_date_generates_conflict(self):
        existing = [_work_entry(company="Acme", start_date="2020-01", end_date="2022-12")]
        incoming = [_work_entry(company="Acme", start_date="2020-03", end_date="2022-12")]
        _, _, conflicts = self._merge(existing, incoming)
        assert any(c.field == "start_date" for c in conflicts)

    def test_date_contradiction_on_end_date_generates_conflict(self):
        existing = [_work_entry(company="Acme", start_date="2020-01", end_date="2022-12")]
        incoming = [_work_entry(company="Acme", start_date="2020-01", end_date="2023-03")]
        _, _, conflicts = self._merge(existing, incoming)
        assert any(c.field == "end_date" for c in conflicts)

    def test_year_only_vs_year_month_not_flagged_as_conflict(self):
        # "2020" vs "2020-01" is just different precision, not a contradiction
        existing = [_work_entry(company="Acme", start_date="2020", end_date="2022-12")]
        incoming = [_work_entry(company="Acme", start_date="2020-01", end_date="2022-12")]
        _, _, conflicts = self._merge(existing, incoming)
        assert not any(c.field == "start_date" for c in conflicts)

    def test_different_company_appended_as_new_entry(self):
        existing = [_work_entry(company="Acme")]
        incoming = [_work_entry(company="Globex")]
        result, added, _ = self._merge(existing, incoming)
        assert len(result) == 2
        assert any("Globex" in a for a in added)

    def test_non_overlapping_dates_appended_as_new_entry(self):
        existing = [_work_entry(company="Acme", start_date="2015-01", end_date="2017-12")]
        incoming = [_work_entry(company="Acme", start_date="2019-01", end_date="2021-12")]
        result, _, _ = self._merge(existing, incoming)
        assert len(result) == 2

    def test_adjacent_roles_same_employer_not_merged(self):
        # System Engineer ends 2021-05, AD starts 2021-05 — boundary month is a
        # job transition, not concurrent employment; must remain two entries.
        existing = [
            _work_entry(company="BioNTech SE", role="System Engineer",
                        start_date="2018-10", end_date="2021-05"),
        ]
        incoming = [
            _work_entry(company="BioNTech SE", role="Associate Director",
                        start_date="2021-05", end_date="2024-11"),
        ]
        result, _, _ = self._merge(existing, incoming)
        assert len(result) == 2
        roles = {r.role for r in result}
        assert "System Engineer" in roles
        assert "Associate Director" in roles

    def test_three_sequential_roles_same_employer_stay_separate(self):
        # Real-world case: three BioNTech SE promotions, each starting when the
        # previous ended — none should collapse into a single entry.
        existing = [
            _work_entry(company="BioNTech SE", role="System Engineer",
                        start_date="2018-10", end_date="2021-05"),
            _work_entry(company="BioNTech SE", role="Associate Director System Architecture",
                        start_date="2021-05", end_date="2024-11"),
            _work_entry(company="BioNTech SE", role="Associate Director E2E Supply Chain",
                        start_date="2024-12", end_date=None),
        ]
        incoming = [
            _work_entry(company="BioNTech SE", role="System Engineer",
                        start_date="2018-10", end_date="2021-05",
                        responsibilities=["Additional bullet"]),
        ]
        result, _, _ = self._merge(existing, incoming)
        assert len(result) == 3
        se = next(r for r in result if r.role == "System Engineer")
        assert "Additional bullet" in se.responsibilities

    def test_shell_entry_with_empty_company_is_dropped(self):
        existing = [_work_entry(company="Acme", start_date="2020-01")]
        incoming = [_work_entry(company="", role="Phantom Role")]  # no company, no content
        result, _, _ = self._merge(existing, incoming)
        assert len(result) == 1  # shell entry discarded

    def test_shell_entry_with_company_but_no_dates_or_content_becomes_alias(self):
        existing = [_work_entry(company="Acme", role="System Engineer", start_date="2018-10")]
        incoming = [_work_entry(company="Acme", role="Solution Architect", start_date=None)]
        result, added, _ = self._merge(existing, incoming)
        assert len(result) == 1
        assert "Solution Architect" in result[0].role_aliases
        assert any("role_alias" in a for a in added)

    def test_shell_entry_with_unknown_company_is_discarded(self):
        existing = [_work_entry(company="Acme", start_date="2020-01")]
        incoming = [_work_entry(company="Unknown Corp", role="Ghost", start_date=None)]
        result, _, _ = self._merge(existing, incoming)
        # Unknown Corp has no matching existing entry — shell is discarded entirely
        assert len(result) == 1

    # -- Fuzzy company matching --

    def test_fuzzy_match_short_name_subset_of_long(self):
        # "Roche" should match "Roche Diagnostics GmbH" (same org, shorter alias)
        existing = [_work_entry(
            company="Roche Diagnostics GmbH", start_date="2011-06", end_date="2012-07"
        )]
        incoming = [_work_entry(
            company="Roche", start_date="2011-06", end_date="2012-07",
            responsibilities=["Validated dashboard"],
        )]
        result, _, _ = self._merge(existing, incoming)
        assert len(result) == 1
        assert "Validated dashboard" in result[0].responsibilities

    def test_fuzzy_match_shared_distinctive_token(self):
        # "Blutspendedienst des BRK" vs "Bayerischer Blutspendedienst gGmbH"
        existing = [_work_entry(
            company="Bayerischer Blutspendedienst gGmbH",
            start_date="2012-08", end_date="2018-09",
            responsibilities=["Managed systems"],
        )]
        incoming = [_work_entry(
            company="Blutspendedienst des BRK",
            start_date="2012-08", end_date="2018-09",
            responsibilities=["Led QC-LIMS implementation"],
        )]
        result, _, _ = self._merge(existing, incoming)
        assert len(result) == 1
        assert "Managed systems" in result[0].responsibilities
        assert "Led QC-LIMS implementation" in result[0].responsibilities

    def test_fuzzy_no_false_match_different_companies(self):
        # "Deutsche Bank" and "Deutsche Post" share a token but are different orgs —
        # neither token set is a subset of the other, so no merge.
        existing = [_work_entry(
            company="Deutsche Bank", start_date="2015-01", end_date="2017-12"
        )]
        incoming = [_work_entry(
            company="Deutsche Post", start_date="2015-01", end_date="2017-12",
            responsibilities=["Delivered parcels"],
        )]
        result, _, _ = self._merge(existing, incoming)
        assert len(result) == 2

    # -- Chronological ordering --

    def test_merge_result_is_sorted_reverse_chronological(self):
        existing = [
            _work_entry(company="OldCo", start_date="2010-01", end_date="2014-12"),
            _work_entry(company="NewCo", start_date="2018-01", end_date="2022-06"),
        ]
        incoming = [_work_entry(company="MidCo", start_date="2015-01", end_date="2017-12")]
        result, _, _ = self._merge(existing, incoming)
        dates = [r.end_date for r in result]
        assert dates == ["2022-06", "2017-12", "2014-12"]

    def test_ongoing_role_sorts_first(self):
        existing = [
            _work_entry(company="OldCo", start_date="2010-01", end_date="2014-12"),
        ]
        incoming = [_work_entry(
            company="CurrentCo", start_date="2020-01", end_date=None,
            responsibilities=["Ongoing work"],
        )]
        result, _, _ = self._merge(existing, incoming)
        assert result[0].company == "CurrentCo"
        assert result[0].end_date is None


# ---------------------------------------------------------------------------
# TestMergeSkills
# ---------------------------------------------------------------------------


class TestMergeSkills:
    def _merge(self, existing, incoming, source="cv_upload"):
        from applire.services.profile.merge import _merge_skills
        return _merge_skills(existing, incoming, source)

    def _skill(self, name, proficiency="intermediate", years=None):
        from applire.schemas.profile import Skill
        return Skill(name=name, proficiency=proficiency, years_experience=years)

    def test_higher_proficiency_wins(self):
        existing = [self._skill("Python", "intermediate")]
        incoming = [self._skill("Python", "expert")]
        result, _, _ = self._merge(existing, incoming)
        assert result[0].proficiency == "expert"

    def test_lower_proficiency_does_not_overwrite_existing(self):
        existing = [self._skill("Python", "expert")]
        incoming = [self._skill("Python", "basic")]
        result, _, _ = self._merge(existing, incoming)
        assert result[0].proficiency == "expert"

    def test_higher_years_experience_wins(self):
        existing = [self._skill("Python", years=3)]
        incoming = [self._skill("Python", years=7)]
        result, _, _ = self._merge(existing, incoming)
        assert result[0].years_experience == 7

    def test_years_experience_keeps_existing_when_incoming_is_none(self):
        existing = [self._skill("Python", years=5)]
        incoming = [self._skill("Python", years=None)]
        result, _, _ = self._merge(existing, incoming)
        assert result[0].years_experience == 5

    def test_new_skill_is_appended(self):
        existing = [self._skill("Python")]
        incoming = [self._skill("Docker")]
        result, added, _ = self._merge(existing, incoming)
        assert len(result) == 2
        assert any("Docker" in a for a in added)

    def test_skills_merge_never_generates_conflicts(self):
        existing = [self._skill("Python", "basic")]
        incoming = [self._skill("Python", "expert")]
        _, _, conflicts = self._merge(existing, incoming)
        assert conflicts == []

    def test_skill_name_matching_is_case_insensitive(self):
        existing = [self._skill("python")]
        incoming = [self._skill("Python", "expert")]
        result, _, _ = self._merge(existing, incoming)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestMergeProfiles
# ---------------------------------------------------------------------------


class TestMergeProfiles:
    def _merge(self, existing, incoming, source="cv_upload"):
        from applire.services.profile.merge import merge_profiles
        return merge_profiles(existing, incoming, source)

    def test_education_dedup_same_institution_and_degree(self):
        from applire.schemas.profile import EducationEntry
        edu = EducationEntry(institution="TU Berlin", degree="B.Sc. Informatik")
        existing = _profile(education=[edu])
        incoming = _profile(education=[edu])
        result = self._merge(existing, incoming)
        assert len(result.merged_profile.education) == 1

    def test_education_different_degree_is_appended(self):
        from applire.schemas.profile import EducationEntry
        existing = _profile(education=[EducationEntry(institution="TU", degree="B.Sc.")])
        incoming = _profile(education=[EducationEntry(institution="TU", degree="M.Sc.")])
        result = self._merge(existing, incoming)
        assert len(result.merged_profile.education) == 2

    def test_language_dedup_keeps_existing_level(self):
        from applire.schemas.profile import Language
        existing = _profile(languages=[Language(language="German", level="C2")])
        incoming = _profile(languages=[Language(language="German", level="B2")])
        result = self._merge(existing, incoming)
        assert len(result.merged_profile.languages) == 1
        assert result.merged_profile.languages[0].level == "C2"

    def test_new_language_is_appended(self):
        from applire.schemas.profile import Language
        existing = _profile(languages=[Language(language="German", level="C2")])
        incoming = _profile(languages=[Language(language="English", level="C1")])
        result = self._merge(existing, incoming)
        assert len(result.merged_profile.languages) == 2

    def test_certification_dedup_by_name(self):
        from applire.schemas.profile import Certification
        cert = Certification(name="AWS Certified", issuing_organization="Amazon")
        existing = _profile(certifications=[cert])
        incoming = _profile(certifications=[cert])
        result = self._merge(existing, incoming)
        assert len(result.merged_profile.certifications) == 1

    def test_personal_info_gap_fill_empty_email(self):
        from applire.schemas.profile import PersonalInfo
        existing = _profile(personal_info=PersonalInfo(name="Ana", email=None))
        incoming = _profile(personal_info=PersonalInfo(name="Ana", email="ana@example.com"))
        result = self._merge(existing, incoming)
        assert result.merged_profile.personal_info.email == "ana@example.com"

    def test_personal_info_conflict_on_different_email(self):
        from applire.schemas.profile import PersonalInfo
        existing = _profile(personal_info=PersonalInfo(email="old@example.com"))
        incoming = _profile(personal_info=PersonalInfo(email="new@example.com"))
        result = self._merge(existing, incoming)
        assert any(c.field == "email" for c in result.conflicts)

    def test_personal_info_no_conflict_when_existing_is_empty(self):
        from applire.schemas.profile import PersonalInfo
        existing = _profile(personal_info=PersonalInfo(email=None))
        incoming = _profile(personal_info=PersonalInfo(email="new@example.com"))
        result = self._merge(existing, incoming)
        assert not any(c.field == "email" for c in result.conflicts)

    def test_professional_summary_fills_missing_de(self):
        from applire.schemas.profile import ProfessionalSummary
        existing = _profile(professional_summary=ProfessionalSummary(en="Summary", de=None))
        incoming = _profile(professional_summary=ProfessionalSummary(de="Zusammenfassung"))
        result = self._merge(existing, incoming)
        assert result.merged_profile.professional_summary.de == "Zusammenfassung"
        assert result.merged_profile.professional_summary.en == "Summary"

    def test_professional_summary_fills_missing_en(self):
        from applire.schemas.profile import ProfessionalSummary
        existing = _profile(professional_summary=ProfessionalSummary(de="Zusammenfassung", en=None))
        incoming = _profile(professional_summary=ProfessionalSummary(en="Summary"))
        result = self._merge(existing, incoming)
        assert result.merged_profile.professional_summary.en == "Summary"

    def test_publications_dedup_by_title(self):
        from applire.schemas.profile import Publication
        pub = Publication(title="My Paper")
        existing = _profile(publications=[pub])
        incoming = _profile(publications=[pub])
        result = self._merge(existing, incoming)
        assert len(result.merged_profile.publications) == 1

    def test_volunteer_activities_dedup_by_role_and_organization(self):
        from applire.schemas.profile import VolunteerActivity
        act = VolunteerActivity(role="Mentor", organization="Code4Good")
        existing = _profile(volunteer_activities=[act])
        incoming = _profile(volunteer_activities=[act])
        result = self._merge(existing, incoming)
        assert len(result.merged_profile.volunteer_activities) == 1

    def test_merge_result_added_list_reflects_new_items(self):
        from applire.schemas.profile import Skill
        existing = _profile(skills=[Skill(name="Python")])
        incoming = _profile(skills=[Skill(name="Docker")])
        result = self._merge(existing, incoming)
        assert any("Docker" in a for a in result.added)


# ---------------------------------------------------------------------------
# TestSQLitePersistence
# ---------------------------------------------------------------------------


class TestSQLitePersistence:
    @pytest.mark.asyncio
    async def test_create_and_read_master_profile(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        profile_data = {"personal_info": {"name": "Alice"}, "work_experience": []}
        record = MasterProfile(profile_json=profile_data)
        sqlite_session.add(record)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        assert row.profile_json["personal_info"]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_profile_json_round_trips_role_aliases(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        data = {
            "work_experience": [{
                "company": "Acme",
                "role": "Team Lead",
                "role_aliases": ["2nd Level Support", "Senior Dev"],
            }]
        }
        record = MasterProfile(profile_json=data)
        sqlite_session.add(record)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        aliases = row.profile_json["work_experience"][0]["role_aliases"]
        assert "2nd Level Support" in aliases
        assert "Senior Dev" in aliases

    @pytest.mark.asyncio
    async def test_profile_json_round_trips_enrichment_history(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        data = {
            "metadata": {
                "enrichment_history": [{
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "cv_upload",
                    "changes": [],
                }]
            }
        }
        record = MasterProfile(profile_json=data)
        sqlite_session.add(record)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        history = row.profile_json["metadata"]["enrichment_history"]
        assert len(history) == 1
        assert history[0]["source"] == "cv_upload"

    @pytest.mark.asyncio
    async def test_profile_json_round_trips_pending_conflicts(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        conflict_id = str(uuid.uuid4())
        data = {
            "metadata": {
                "pending_conflicts": [{
                    "conflict_id": conflict_id,
                    "section": "work_experience",
                    "field": "start_date",
                    "existing_value": "2020-01",
                    "incoming_value": "2020-03",
                    "source": "cv_upload",
                    "resolved": False,
                }]
            }
        }
        record = MasterProfile(profile_json=data)
        sqlite_session.add(record)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        pending = row.profile_json["metadata"]["pending_conflicts"]
        assert len(pending) == 1
        assert pending[0]["conflict_id"] == conflict_id

    @pytest.mark.asyncio
    async def test_deleted_at_defaults_to_null(self, sqlite_session):
        from applire.models.profile import MasterProfile

        record = MasterProfile(profile_json={})
        sqlite_session.add(record)
        await sqlite_session.commit()
        assert record.deleted_at is None

    @pytest.mark.asyncio
    async def test_soft_delete_sets_deleted_at(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        record = MasterProfile(profile_json={})
        sqlite_session.add(record)
        await sqlite_session.commit()

        now = datetime.now(timezone.utc)
        record.deleted_at = now
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        assert row.deleted_at is not None
