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
# German CV proficiency tiers (ADR-061 clause 5, #304/#317, PO follow-up
# 2026-07-27) — _PROFICIENCY_ALIASES was entirely English/LinkedIn. "Anwender"
# and "Grundkenntnisse" are the EXACT two words #304's own case used and are
# the words clause 5 names as declarations that must be honoured as a
# ceiling — before this fix they fell through the unknown-string fallback to
# "intermediate", one layer upstream of every site #317 already fixed, so the
# declared word never survived long enough to BE the ceiling.
# ---------------------------------------------------------------------------

class TestGermanProficiencyAliases:
    @pytest.mark.parametrize("word,expected", [
        ("Anwender", "basic"),
        ("anwender", "basic"),  # case-insensitivity
        ("Grundkenntnisse", "basic"),
        ("Grundlagen", "basic"),
        ("Fortgeschritten", "advanced"),
        ("Erfahren", "advanced"),
        ("Verhandlungssicher", "advanced"),
        ("Fließend", "advanced"),
        ("Fliessend", "advanced"),  # ASCII ß->ss transliteration (#213/#214 precedent)
        ("Muttersprache", "expert"),
    ])
    def test_german_tier_word_normalizes_to_declared_level(self, word, expected):
        from applire.schemas.profile import Skill
        skill = Skill(name="SAP", category="technical", proficiency=word)
        assert skill.proficiency == expected

    def test_sap_anwender_reaches_the_vault_at_basic_end_to_end(self):
        """The #304 shape, closed at BOTH layers: the schema no longer silently
        upgrades the German self-declaration to "intermediate", and the
        deterministic enrichment ladder (#317) no longer raises it further from
        elapsed time. "SAP (Anwender)" must reach "basic" — not "intermediate"
        (the old schema default) and not "expert" (the old ladder ratchet)."""
        from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
        from applire.services.skill_enrichment import _match_and_enrich

        profile = MasterProfileData(
            skills=[Skill(name="SAP", category="technical", proficiency="Anwender")],
            work_experience=[
                WorkEntry(company="Weberit", role="Schichtleiter",
                          start_date="2011-08", end_date="2015-01", technologies=["SAP"]),
                WorkEntry(company="Rheinwerk", role="Leiter Operations",
                          start_date="2015-02", end_date=None, technologies=["SAP"]),
            ],
        )
        # The schema validator already normalized "Anwender" -> "basic" at
        # construction time — assert that before enrichment runs at all.
        assert profile.skills[0].proficiency == "basic"

        enriched, unmatched = _match_and_enrich(profile)
        assert len(unmatched) == 0
        skill = enriched[0]
        assert skill.years_experience == 15  # old ladder would have said "expert"
        assert skill.proficiency == "basic"  # declared ceiling survives both layers


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


class TestApplyFloorRetired:
    """ADR-061 clauses 5 & 6 (#304/#317): the old `_apply_floor` helper — whose
    own docstring said "the LLM-extracted proficiency is never lowered by the
    calculation" — is the named defect (#304) and has been removed outright,
    not merely relabelled. It had no callers left once skill_enrichment.py
    stopped deriving `proficiency` from elapsed time. This test documents the
    removal rather than the old (inverted) behaviour."""

    def test_apply_floor_no_longer_exists(self):
        import applire.services.skill_enrichment as mod
        assert not hasattr(mod, "_apply_floor")


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
        # ADR-061 clause 7: renamed from "deterministic" — this provenance
        # labels years_experience/experience_refs as code-computed, not the
        # declared proficiency (which this pass never touches).
        assert skill.source == "computed"
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
        # ADR-061 clause 6: years_experience is computed; proficiency is not
        # derived from it — the declared "intermediate" passes through as-is.
        assert skill.proficiency == "intermediate"

    def test_declared_proficiency_never_raised_by_years(self):
        """ADR-061 clause 5/6 (#304): a declared LOW proficiency stays exactly as
        declared even when many matched years would previously have computed a
        much higher tier — this is the SAP (Anwender) regression case."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "Python", "category": "technical", "proficiency": "basic"}],
            work_experience=[{
                "company": "Startup",
                "role": "Dev",
                "start_date": "2011-01",
                "end_date": None,
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        skill = enriched[0]
        assert skill.years_experience >= 6  # old ladder would have said "expert"
        assert skill.proficiency == "basic"  # declared tier is a ceiling — untouched

    def test_declared_proficiency_never_lowered_either(self):
        """The service never WRITES proficiency at all in either direction —
        an existing "expert" declaration is equally untouched by a short span."""
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
        assert skill.source == "computed"

    def test_language_skills_passed_through_but_provenance_is_recorded(self):
        """#327 amended this test's old name (…_unchanged) and its ``source is
        None`` assertion. A language level is still never turned into a tenure —
        the skill is passed through untouched in every other respect — but
        leaving ``source`` null was what produced a vault holding two
        indistinguishable populations, enriched and never-visited."""
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
        assert len(enriched) == 1
        assert len(unmatched) == 0
        skill = enriched[0]
        assert skill.source == "transcribed"
        assert skill.years_experience is None
        assert skill.experience_refs == []

    def test_domain_skill_with_no_experience_at_all_is_unmatched(self):
        """#327: "domain" is now matchable, so a domain skill with nothing to
        match against lands in ``unmatched`` like any other — where phase 2
        declines to estimate it and stamps it transcribed."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile(
            skills=[{"name": "Healthcare", "category": "domain", "proficiency": "advanced"}],
            work_experience=[],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert enriched == []
        assert [s.name for s in unmatched] == ["Healthcare"]
        assert unmatched[0].experience_refs == []

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
# Task ST-B: Cross-kind skill accrual (US172 / ADR-044)
# Skills from volunteering and projects must also accrue years + experience_refs
# ---------------------------------------------------------------------------

class TestCrossKindSkillAccrual:
    """Regression tests for ADR-044 correctness fix: skill accrual via all_experiences."""

    def _make_profile_with_kinds(
        self,
        skills,
        work_experience=None,
        projects=None,
        volunteer_activities=None,
    ):
        from applire.schemas.profile import (
            MasterProfileData, Skill, WorkEntry, ProjectEntry, VolunteerActivity,
        )
        return MasterProfileData(
            skills=[Skill(**s) for s in skills],
            work_experience=[WorkEntry(**w) for w in (work_experience or [])],
            projects=[ProjectEntry(**p) for p in (projects or [])],
            volunteer_activities=[VolunteerActivity(**v) for v in (volunteer_activities or [])],
        )

    def test_skill_only_in_volunteer_activity_accrues(self):
        """NGO-software case: Python used ONLY in volunteering → must yield years > 0."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile_with_kinds(
            skills=[{"name": "Python", "category": "technical", "proficiency": "basic"}],
            work_experience=[],  # no paid work with Python
            volunteer_activities=[{
                "organization": "Code for Good e.V.",
                "role": "Software Volunteer",
                "start_date": "2021-01",
                "end_date": "2023-01",
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(unmatched) == 0, "Python must be matched via VolunteerActivity"
        skill = enriched[0]
        assert skill.years_experience > 0
        assert "Code for Good e.V." in skill.experience_refs
        assert skill.source == "computed"

    def test_skill_only_in_project_entry_accrues(self):
        """Skill used ONLY in a ProjectEntry must accrue years and reference the project name."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile_with_kinds(
            skills=[{"name": "Rust", "category": "technical", "proficiency": "basic"}],
            projects=[{
                "name": "OpenPerfMon",
                "role": "Lead Developer",
                "start_date": "2022-03",
                "end_date": "2024-03",
                "technologies": ["Rust"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(unmatched) == 0, "Rust must be matched via ProjectEntry"
        skill = enriched[0]
        assert skill.years_experience > 0
        assert "OpenPerfMon" in skill.experience_refs
        assert skill.source == "computed"

    def test_skill_across_work_and_volunteer_combines_non_overlapping_years(self):
        """Skill in both WorkEntry and VolunteerActivity: ranges merged, org_labels from both."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile_with_kinds(
            skills=[{"name": "Python", "category": "technical", "proficiency": "intermediate"}],
            work_experience=[{
                "company": "Siemens AG",
                "role": "Engineer",
                "start_date": "2018-01",
                "end_date": "2020-01",
                "technologies": ["Python"],
            }],
            volunteer_activities=[{
                "organization": "Code for Good e.V.",
                "role": "Software Volunteer",
                "start_date": "2021-01",
                "end_date": "2023-01",
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(unmatched) == 0
        skill = enriched[0]
        # 2 years Siemens + 2 years NGO, non-overlapping → 4 years
        assert skill.years_experience == 4
        assert "Siemens AG" in skill.experience_refs
        assert "Code for Good e.V." in skill.experience_refs

    def test_work_entry_accrual_still_works_after_refactor(self):
        """Regression: WorkEntry-based accrual must be unaffected by the cross-kind change."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile_with_kinds(
            skills=[{"name": "Django", "category": "technical", "proficiency": "intermediate"}],
            work_experience=[{
                "company": "BMW Group",
                "role": "Backend Dev",
                "start_date": "2019-01",
                "end_date": "2022-01",
                "technologies": ["Django"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(unmatched) == 0
        skill = enriched[0]
        assert skill.years_experience == 3
        assert skill.experience_refs == ["BMW Group"]
        assert skill.source == "computed"

    def test_skill_not_in_any_kind_goes_to_unmatched(self):
        """Skill not found in work, projects, or volunteering → still goes to unmatched."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile_with_kinds(
            skills=[{"name": "Kubernetes", "category": "technical", "proficiency": "basic"}],
            work_experience=[{
                "company": "Some Corp",
                "role": "Dev",
                "start_date": "2020-01",
                "end_date": "2022-01",
                "technologies": ["Docker"],
            }],
            projects=[{
                "name": "My Project",
                "role": "Author",
                "start_date": "2021-01",
                "end_date": "2022-01",
                "technologies": ["Terraform"],
            }],
            volunteer_activities=[{
                "organization": "Open Source Org",
                "role": "Contributor",
                "start_date": "2021-06",
                "end_date": "2022-06",
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(unmatched) == 1
        assert unmatched[0].name == "Kubernetes"

    def test_blank_org_label_not_stored_in_experience_refs(self):
        """A work entry with company="" (org_label() == "") must still accrue years but
        must NOT pollute experience_refs with an empty string (the `if label` guard)."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._make_profile_with_kinds(
            skills=[{"name": "Python", "category": "technical", "proficiency": "basic"}],
            work_experience=[{
                "company": "",  # freelance / unnamed → org_label() == ""
                "role": "Freelancer",
                "start_date": "2021-01",
                "end_date": "2023-01",
                "technologies": ["Python"],
            }],
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert len(unmatched) == 0
        skill = enriched[0]
        assert skill.years_experience > 0          # years still accrue
        assert "" not in skill.experience_refs      # empty label not stored


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
    # ADR-061 clauses 5 & 6 (#304/#317): the LLM estimates years only; the
    # declared "intermediate" proficiency is never derived from that estimate.
    assert skill.proficiency == "intermediate"
    assert skill.experience_refs == []


@pytest.mark.asyncio
async def test_enrich_skills_llm_estimate_never_touches_declared_proficiency():
    """ADR-061 clauses 5 & 6 (#304): renamed from
    test_enrich_skills_floor_applied_to_llm_estimate — there is no floor/ceiling
    merge left to apply; the LLM-estimated duration simply never writes
    proficiency, in either direction."""
    from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[
            Skill(name="Leadership", category="soft", proficiency="expert"),
        ],
        # #264: a plausibility ceiling now clamps LLM-estimated years to the
        # candidate's career span — a real span well over the estimate below.
        work_experience=[
            WorkEntry(company="Siemens AG", role="Team Lead", start_date="2015-01", end_date="2020-01")
        ],
    )
    mock_provider = AsyncMock()
    # LLM estimates only 1 year — must not touch the declared "expert" tier
    mock_provider.aparse_json.return_value = {"Leadership": 1}

    result = await enrich_skills(profile, mock_provider)

    skill = result.skills[0]
    assert skill.years_experience == 1
    assert skill.proficiency == "expert"  # declared tier untouched


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
    # #327: was "llm_estimated". The estimator returned no number, so labelling
    # the skill as estimated asserted an estimate that was never made.
    assert skill.source == "transcribed"
    assert skill.proficiency == "basic"  # unchanged


@pytest.mark.asyncio
async def test_enrich_skills_does_not_mutate_input():
    from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[Skill(name="Python", category="technical", proficiency="basic")],
        # #264: a real career span, well over the 3-year estimate below, so the
        # plausibility clamp doesn't interfere with this mutation-safety check.
        work_experience=[
            WorkEntry(company="Acme GmbH", role="Engineer", start_date="2015-01", end_date="2022-01")
        ],
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

    # German skill is never estimated — but it does carry provenance (#327)
    german = next(s for s in result.skills if s.name == "German")
    assert german.source == "transcribed"
    assert german.years_experience is None
    assert german.experience_refs == []


# ---------------------------------------------------------------------------
# #264 — deterministic plausibility ceiling on LLM-estimated skill years.
#
# skill_enrichment's LLM estimation call has NO deterministic control today: the
# only post-processing is the proficiency floor (never DOWNgrade an existing
# level) and rounding. A hallucinated duration that overstates a skill beyond the
# candidate's own career span was uncaught. This is the audit's closure — a
# deterministic clamp rather than a second LLM reviewer call on every profile,
# per the PO principle of preferring a cheap deterministic control where one can
# fully close the loop.
# ---------------------------------------------------------------------------


class TestMaxPlausibleYears:
    def test_no_experience_at_all_returns_zero(self):
        from applire.schemas.profile import MasterProfileData
        from applire.services.skill_enrichment import _max_plausible_years

        assert _max_plausible_years(MasterProfileData()) == 0

    def test_earliest_start_date_bounds_the_span(self):
        from applire.schemas.profile import MasterProfileData, WorkEntry
        from applire.services.skill_enrichment import _max_plausible_years

        profile = MasterProfileData(work_experience=[
            WorkEntry(company="A", role="Dev", start_date="2020-01", end_date="2021-01"),
            WorkEntry(company="B", role="Dev", start_date="2010-01", end_date="2012-01"),
        ])
        # Earliest start (2010) to today spans well over a decade.
        assert _max_plausible_years(profile) >= 14

    def test_unparseable_start_dates_are_skipped_not_fatal(self):
        from applire.schemas.profile import MasterProfileData, WorkEntry
        from applire.services.skill_enrichment import _max_plausible_years

        profile = MasterProfileData(work_experience=[
            WorkEntry(company="A", role="Dev", start_date=None, end_date=None),
        ])
        assert _max_plausible_years(profile) == 0


@pytest.mark.asyncio
async def test_enrich_skills_clamps_an_implausible_llm_estimate():
    """A skill duration estimate exceeding the candidate's entire career span is
    clamped to that span, not stored verbatim — no career history exists to
    ground an 8-year claim on a 2-year-old career."""
    from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[Skill(name="Kubernetes", category="technical", proficiency="basic")],
        work_experience=[
            WorkEntry(company="Acme GmbH", role="Junior Dev", start_date="2024-01", end_date=None)
        ],
    )
    mock_provider = AsyncMock()
    # Hallucinated: 8 years of Kubernetes on an ~1.5-year-old career.
    mock_provider.aparse_json.return_value = {"Kubernetes": 8}

    result = await enrich_skills(profile, mock_provider)

    skill = result.skills[0]
    assert skill.years_experience is not None
    assert skill.years_experience < 8
    assert skill.source == "llm_estimated"


@pytest.mark.asyncio
async def test_enrich_skills_drops_estimate_with_zero_career_span():
    """No experience entries at all → no basis for ANY duration estimate; a
    positive LLM estimate is dropped entirely rather than clamped to a
    nonsensical 0-that-gets-floored-to-1."""
    from applire.schemas.profile import MasterProfileData, Skill
    from applire.services.skill_enrichment import enrich_skills

    profile = MasterProfileData(
        skills=[Skill(name="Rust", category="technical", proficiency="basic")],
        work_experience=[],
    )
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = {"Rust": 5}

    result = await enrich_skills(profile, mock_provider)

    skill = result.skills[0]
    assert skill.years_experience is None
    assert skill.proficiency == "basic"  # unchanged — no grounds to enrich


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
            source="Siemens AG — Senior Engineer 2018-2023\nBosch GmbH — Engineer 2015-2018",
        )
        assert "Missing work entries" in prompt
        assert "Siemens AG" in prompt
        # US194: the corrector re-reads the source CV text.
        assert "Bosch GmbH" in prompt


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


# ---------------------------------------------------------------------------
# #327 — phase 1 matched nothing, for any profile.
#
# Ground truth, charter run #9 (2026-07-28, ``operations_marcus_de``, real
# provider, backend/logs/llm/2026-07-28.jsonl). The extractor returned, for a
# 14-year production-manager career:
#
#     work_experience[0].technologies = ["SAP"]
#     work_experience[1].technologies = ["SAP"]
#     work_experience[2].technologies = []
#     skills = 9x category "domain", 2x "technical", 1x "soft"
#
# while the role bullets it emitted alongside said, verbatim and dated:
#
#     "Begleitung der jährlichen ISO-9001-Audits als Bereichsverantwortlicher"
#     "... durch Einführung von Shopfloor-Management und KVP-Routinen"
#     "Termintreue von 87 % auf 96 % verbessert ... (SMED)"
#     "Mitarbeit bei der Einführung von SAP in der Fertigung"
#     "Ausbildung zum Lean-Multiplikator; Moderation von 5S- und Kaizen-Workshops"
#
# 1 of 24 stored skills came out ``computed``. The extractor is not at fault:
# US176's TECHNOLOGIES-vs-PRACTICES rule *instructs* it to keep ``technologies``
# to concrete tools and route practices/standards to skills as category
# "domain" — and ``_ELIGIBLE_CATEGORIES`` then excluded "domain" from
# enrichment entirely. The hygiene rule and the phase-1 join were designed
# against each other, so for any career whose competencies are practices rather
# than tools (operations, nursing, controlling) the matching corpus is empty by
# construction and the deterministic path cannot fire.
#
# The evidence a duration must be computed from is the experience's own dated
# text, not the one narrow list. ADR-062 classification: FACT — normalised
# surface presence of a name in a text is settled by the data, and the shared
# presence predicate (``ats_audit.surface_present``, ADR-048) already decides it
# for the ATS panel and the gap hints. No new matcher is introduced here.
# ---------------------------------------------------------------------------

class TestPhase1MatchesTheRolesOwnEvidence:
    def _run9_profile(self, skills):
        from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
        return MasterProfileData(
            skills=[Skill(**s) for s in skills],
            work_experience=[
                WorkEntry(
                    company="Weberit Kunststofftechnik GmbH",
                    role="Produktionsleiter",
                    start_date="2017-04",
                    end_date=None,
                    technologies=["SAP"],
                    responsibilities=[
                        "Verantwortung für zwei Fertigungsbereiche (Spritzguss, Montage)",
                        "Begleitung der jährlichen ISO-9001-Audits als Bereichsverantwortlicher",
                        "Eskalationsinstanz für Kundenreklamationen aus der Serienfertigung",
                    ],
                    achievements=[
                        "Ausschussquote von 4,1 % auf 2,3 % gesenkt durch Einführung von "
                        "Shopfloor-Management und KVP-Routinen",
                        "Termintreue von 87 % auf 96 % verbessert durch neue Feinplanung "
                        "und Rüstworkshops (SMED)",
                    ],
                ),
                WorkEntry(
                    company="Rasselstein Umformtechnik GmbH",
                    role="Schichtleiter",
                    start_date="2011-08",
                    end_date="2017-03",
                    technologies=[],
                    responsibilities=[
                        "Führung einer Schicht mit 14 Mitarbeitenden in der Blechumformung",
                        "Mitarbeit bei der Einführung von SAP in der Fertigung",
                    ],
                    achievements=[
                        "Ausbildung zum Lean-Multiplikator; Moderation von 5S- und "
                        "Kaizen-Workshops",
                    ],
                ),
            ],
        )

    def test_technical_skill_named_only_in_a_bullet_is_computed(self):
        """SAP is in role 0's ``technologies`` AND role 1's bullets. Keying on
        ``technologies`` alone credited one role and lost the six earlier years."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._run9_profile(
            [{"name": "SAP", "category": "technical", "proficiency": "intermediate"}]
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert unmatched == []
        skill = enriched[0]
        assert skill.source == "computed"
        assert skill.experience_refs == [
            "Weberit Kunststofftechnik GmbH",
            "Rasselstein Umformtechnik GmbH",
        ]

    def test_domain_skill_evidenced_by_a_dated_role_is_computed(self):
        """The category US176 routes every practice/standard into. Excluding it
        made the deterministic path unreachable for whole occupations."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._run9_profile(
            [{"name": "ISO 9001", "category": "domain", "proficiency": "advanced"}]
        )
        enriched, unmatched = _match_and_enrich(profile)
        assert unmatched == []
        assert enriched[0].source == "computed"
        assert enriched[0].experience_refs == ["Weberit Kunststofftechnik GmbH"]

    def test_punctuation_and_compounding_variants_match(self):
        """"ISO 9001" vs "ISO-9001-Audits", "SMED" vs "(SMED)", "5S" vs "5S-
        und ...": German CV compounding is exactly what the shared normaliser
        (NFKC + dash→space + casefold) already folds."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._run9_profile([
            {"name": "SMED", "category": "domain", "proficiency": "advanced"},
            {"name": "5S", "category": "domain", "proficiency": "advanced"},
            {"name": "Shopfloor-Management", "category": "domain", "proficiency": "advanced"},
        ])
        enriched, unmatched = _match_and_enrich(profile)
        assert unmatched == []
        assert {s.name for s in enriched} == {"SMED", "5S", "Shopfloor-Management"}
        assert all(s.source == "computed" for s in enriched)

    def test_a_skill_no_role_names_stays_unmatched(self):
        """The broadened corpus must not become a blanket match. "Lean
        Management" is NOT claimed by any role here — the bullet says
        "Lean-Multiplikator", which is a different string; inferring the skill
        from it would be a judgement, not a fact (ADR-062)."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._run9_profile([
            {"name": "Lean Management", "category": "domain", "proficiency": "advanced"},
            {"name": "Kubernetes", "category": "technical", "proficiency": "basic"},
        ])
        enriched, unmatched = _match_and_enrich(profile)
        assert {s.name for s in unmatched} == {"Lean Management", "Kubernetes"}

    def test_a_one_or_two_character_skill_name_is_never_substring_matched(self):
        """A name too short to be evidence on its own: "R" occurs inside
        "Reklamationen", "C" inside "Schicht". Crediting years and citing an
        employer off a letter is a false provenance, and this consumer's
        false positive is a number on the rendered CV — stricter than the ATS
        panel's, where the same predicate only marks a keyword covered."""
        from applire.services.skill_enrichment import _match_and_enrich
        profile = self._run9_profile([
            {"name": "R", "category": "technical", "proficiency": "advanced"},
            {"name": "C", "category": "technical", "proficiency": "advanced"},
        ])
        enriched, unmatched = _match_and_enrich(profile)
        assert {s.name for s in unmatched} == {"R", "C"}


# ---------------------------------------------------------------------------
# #327 — every skill in the vault must carry a provenance, and it must be true.
#
# ADR-061 clause 7 asks provenance to distinguish a *transcribed* fact from a
# *computed* one. Three values, and only these:
#   "computed"      — years derived from the dated roles that evidence the skill
#   "llm_estimated" — years came from the phase-2 estimator
#   "transcribed"   — read off the document; nothing was inferred
# Before this, language/domain pass-through left ``source`` null, and a skill
# the estimator declined to score was still stamped "llm_estimated" — a label
# asserting an estimate that was never made.
# ---------------------------------------------------------------------------

class TestProvenanceIsAlwaysSetAndAlwaysTrue:
    @pytest.mark.asyncio
    async def test_language_skill_is_transcribed_not_null(self):
        from applire.schemas.profile import MasterProfileData, Skill
        from applire.services.skill_enrichment import enrich_skills
        profile = MasterProfileData(
            skills=[Skill(name="Deutsch", category="language", proficiency="expert")]
        )
        mock_provider = AsyncMock()
        result = await enrich_skills(profile, mock_provider)
        assert result.skills[0].source == "transcribed"
        mock_provider.aparse_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_unevidenced_domain_skill_is_transcribed_never_estimated(self):
        """Making "domain" matchable does NOT make it guessable. A duration the
        candidate's own record does not support stays absent rather than being
        invented — the estimator's reach is unchanged (technical/soft)."""
        from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
        from applire.services.skill_enrichment import enrich_skills
        profile = MasterProfileData(
            skills=[Skill(name="GxP", category="domain", proficiency="advanced")],
            work_experience=[WorkEntry(
                company="Weberit", role="Produktionsleiter", start_date="2017-04",
            )],
        )
        mock_provider = AsyncMock()
        result = await enrich_skills(profile, mock_provider)
        assert result.skills[0].source == "transcribed"
        assert result.skills[0].years_experience is None
        mock_provider.aparse_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_estimator_declining_to_score_does_not_claim_an_estimate(self):
        from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
        from applire.services.skill_enrichment import enrich_skills
        profile = MasterProfileData(
            skills=[Skill(name="Blockchain", category="technical", proficiency="basic")],
            work_experience=[WorkEntry(
                company="Weberit", role="Produktionsleiter", start_date="2017-04",
            )],
        )
        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = {"Blockchain": None}
        result = await enrich_skills(profile, mock_provider)
        assert result.skills[0].years_experience is None
        assert result.skills[0].source == "transcribed"

    @pytest.mark.asyncio
    async def test_an_estimate_below_a_year_is_unknown_not_a_confident_one(self):
        """``max(1, round(raw))`` turned an unsure 0.3 into a vault fact reading
        "1 year", indistinguishable from a genuine one-year skill."""
        from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
        from applire.services.skill_enrichment import enrich_skills
        profile = MasterProfileData(
            skills=[Skill(name="Terraform", category="technical", proficiency="basic")],
            work_experience=[WorkEntry(
                company="Weberit", role="Produktionsleiter", start_date="2010-01",
            )],
        )
        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = {"Terraform": 0.3}
        result = await enrich_skills(profile, mock_provider)
        assert result.skills[0].years_experience is None
        assert result.skills[0].source == "transcribed"

    @pytest.mark.asyncio
    async def test_a_transcribed_duration_is_never_overwritten_by_an_estimate(self):
        """The CV said "14 Jahre". A model guess must not replace a stated
        figure — facts are deterministic (ADR-062)."""
        from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
        from applire.services.skill_enrichment import enrich_skills
        profile = MasterProfileData(
            skills=[Skill(
                name="Lean Management", category="technical",
                proficiency="advanced", years_experience=14,
            )],
            work_experience=[WorkEntry(
                company="Weberit", role="Produktionsleiter", start_date="2010-01",
            )],
        )
        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = {"Lean Management": 3}
        result = await enrich_skills(profile, mock_provider)
        assert result.skills[0].years_experience == 14
        assert result.skills[0].source == "transcribed"


# ---------------------------------------------------------------------------
# #327, second defect — skills contributed by a MERGE reached the vault with
# no provenance at all (33 of 67 on a three-document import; 21 of 24 on run
# #9's two-document profile).
#
# ``enrich_skills`` runs on ``incoming`` BEFORE reconciliation, but the merged
# profile is rebuilt from the ADR-046 op vocabulary, and ``UpsertSkill`` carries
# name/category/proficiency/evidence/status — no ``years_experience``, no
# ``source``. The enrichment is not overwritten; it is structurally unable to
# survive the round-trip, exactly like the certifications #190 recovered with
# ``_union_certifications`` at this same seam. Adding the fields to the op is
# the wrong fix: the reconciler LLM would then be emitting computed provenance,
# which ADR-062 reserves for code.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merged_profile_is_re_enriched_so_no_skill_loses_its_provenance():
    from unittest.mock import patch as _patch
    from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    existing = MasterProfileData(
        work_experience=[WorkEntry(
            id="w-existing", company="Weberit Kunststofftechnik GmbH",
            role="Produktionsleiter", start_date="2017-04",
            responsibilities=["Begleitung der jährlichen ISO-9001-Audits"],
        )],
    )
    incoming = MasterProfileData(
        skills=[Skill(name="ISO 9001", category="domain", proficiency="advanced")],
    )

    # The reconciler mints the skill through the op vocabulary — the shape that
    # drops years_experience/source on the floor.
    from applire.services.profile.reconcile.ops import UpsertSkill

    class _Result:
        ops = [UpsertSkill(name="ISO 9001", category="domain", evidence=["w-existing"])]
        ambiguities: list = []

    async def _fake_reconcile(*args, **kwargs):
        return _Result()

    provider = AsyncMock()
    with _patch(
        "applire.services.profile.reconcile.import_bridge.reconcile", _fake_reconcile
    ):
        merge_result = await reconcile_import(
            existing, incoming, source="cv_upload", provider=provider,
        )

    merged_skill = next(s for s in merge_result.merged_profile.skills if s.name == "ISO 9001")
    assert merged_skill.source == "computed"
    # The reconciler's own evidence id is KEPT and the computed org label added
    # beside it — enrichment may add provenance, never remove it.
    assert merged_skill.experience_refs == [
        "w-existing", "Weberit Kunststofftechnik GmbH",
    ]
    # Recovering the provenance costs no LLM call: the merge seam runs the
    # deterministic phase only.
    provider.aparse_json.assert_not_called()


@pytest.mark.asyncio
async def test_merge_does_not_blank_a_duration_the_first_import_already_showed():
    """#327 — a phase-2 estimate cannot survive ``UpsertSkill`` either, and the
    deterministic pass cannot re-derive it. Without the carry-over, importing a
    second document silently erased years the user had already seen."""
    from unittest.mock import patch as _patch
    from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
    from applire.services.profile.reconcile.import_bridge import reconcile_import
    from applire.services.profile.reconcile.ops import UpsertSkill

    existing = MasterProfileData(
        work_experience=[WorkEntry(
            id="w-existing", company="Acme GmbH", role="Engineer", start_date="2015-01",
        )],
    )
    # Already enriched pre-merge: no role's text names it, so phase 2 scored it.
    incoming = MasterProfileData(
        skills=[Skill(
            name="Kubernetes", category="technical", proficiency="basic",
            years_experience=4, source="llm_estimated",
        )],
    )

    class _Result:
        ops = [UpsertSkill(name="Kubernetes", category="technical")]
        ambiguities: list = []

    async def _fake_reconcile(*args, **kwargs):
        return _Result()

    provider = AsyncMock()
    with _patch(
        "applire.services.profile.reconcile.import_bridge.reconcile", _fake_reconcile
    ):
        merge_result = await reconcile_import(
            existing, incoming, source="cv_upload", provider=provider,
        )

    merged_skill = next(
        s for s in merge_result.merged_profile.skills if s.name == "Kubernetes"
    )
    assert merged_skill.years_experience == 4
    assert merged_skill.source == "llm_estimated"
    provider.aparse_json.assert_not_called()
