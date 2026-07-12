"""Tests for gap_detector_mode_c — now a thin delegation to the unified
completeness model (US179 / ADR-041).

Key behaviour changes after the routing refactor:
- team_size / budget_managed / industry_context are ROLE-AWARE: only emitted
  when the entry's expected_fields annotation includes them.
- start_date / end_date are FLOOR fields: always emitted when missing.
"""
import pytest
from applire.services.interview_graph import gap_detector_mode_c


# FULL_PROFILE: entries annotated with expected_fields so that all conditional
# fields are in scope.  Mirrors what the LLM annotation produces for a people-
# manager role (team_size + budget_managed) and an IC role with industry context.
FULL_PROFILE = {
    "work_experience": [
        {
            "company": "Beta GmbH",
            "role": "Product Lead",
            "expected_fields": ["team_size", "budget_managed"],
            "achievements": [],
            "team_size": None,
            "budget_managed": None,
            "industry_context": "SaaS",
        },
        {
            "company": "Acme Corp",
            "role": "Senior Engineer",
            "expected_fields": ["budget_managed"],
            "achievements": ["Led migration to microservices"],
            "team_size": 8,
            "budget_managed": None,
            "industry_context": "Fintech",
        },
    ],
    "professional_summary": "",
}


def test_detects_achievements_gap():
    gaps = gap_detector_mode_c(FULL_PROFILE)
    assert "achievements: Product Lead @ Beta GmbH" in gaps


def test_detects_team_size_gap():
    # team_size is in expected_fields for Product Lead → gap emitted
    gaps = gap_detector_mode_c(FULL_PROFILE)
    assert "team_size: Product Lead @ Beta GmbH" in gaps


def test_detects_budget_gap():
    # budget_managed is in expected_fields for Product Lead → gap emitted
    gaps = gap_detector_mode_c(FULL_PROFILE)
    assert "budget_managed: Product Lead @ Beta GmbH" in gaps


def test_detects_professional_summary_gap():
    gaps = gap_detector_mode_c(FULL_PROFILE)
    assert "professional_summary" in gaps


def test_floor_fields_always_emitted():
    # start_date / end_date are floor fields — emitted regardless of expected_fields.
    # Beta GmbH entry has no start_date/end_date.
    gaps = gap_detector_mode_c(FULL_PROFILE)
    assert "start_date: Product Lead @ Beta GmbH" in gaps
    assert "end_date: Product Lead @ Beta GmbH" in gaps


def test_conditional_fields_not_emitted_without_annotation():
    # An entry with expected_fields=[] (IC role) → only floor gaps emitted.
    profile = {
        "work_experience": [
            {
                "company": "Beta GmbH",
                "role": "IC Dev",
                "expected_fields": [],
                "achievements": ["Delivered feature X"],
                "start_date": "2020",
                "end_date": "2023",
                "team_size": None,
                "budget_managed": None,
            }
        ]
    }
    gaps = gap_detector_mode_c(profile)
    assert not any(g.startswith("team_size") for g in gaps)
    assert not any(g.startswith("budget_managed") for g in gaps)


def test_no_achievements_gap_when_filled():
    profile = {
        "work_experience": [
            {
                "company": "Beta GmbH",
                "role": "Product Lead",
                "expected_fields": ["team_size", "budget_managed"],
                "achievements": ["Grew MRR by 40%"],
                "start_date": "2020",
                "end_date": "2023",
                "team_size": 5,
                "budget_managed": "€200k",
                "industry_context": "SaaS",
            }
        ],
        "professional_summary": "Experienced product leader.",
    }
    gaps = gap_detector_mode_c(profile)
    assert gaps == []


def test_achievements_gap_prioritised_first():
    gaps = gap_detector_mode_c(FULL_PROFILE)
    achievement_gaps = [g for g in gaps if g.startswith("achievements:")]
    other_gaps = [g for g in gaps if not g.startswith("achievements:") and g != "professional_summary"]
    if achievement_gaps and other_gaps:
        assert gaps.index(achievement_gaps[0]) < gaps.index(other_gaps[0])


def test_na_fields_excluded():
    profile = {
        **FULL_PROFILE,
        "_meta": {
            "na_fields": ["budget_managed: Product Lead @ Beta GmbH"]
        },
    }
    gaps = gap_detector_mode_c(profile)
    assert "budget_managed: Product Lead @ Beta GmbH" not in gaps


def test_scope_filters_to_single_entry():
    gaps = gap_detector_mode_c(
        FULL_PROFILE,
        scope="work_experience:Beta GmbH:Product Lead",
    )
    assert all("Beta GmbH" in g or g == "professional_summary" for g in gaps
               if g != "professional_summary")
    assert "budget_managed: Senior Engineer @ Acme Corp" not in gaps
    # professional_summary excluded when scope is set to a specific entry
    assert "professional_summary" not in gaps


def test_complete_entry_with_missing_budget_detected():
    # Acme Corp has budget_managed in expected_fields but value is None → gap emitted
    gaps = gap_detector_mode_c(FULL_PROFILE)
    assert "budget_managed: Senior Engineer @ Acme Corp" in gaps


def test_empty_profile_returns_empty_list():
    gaps = gap_detector_mode_c({})
    assert gaps == []


def test_is_current_suppresses_end_date_gap():
    # #155 — "this is my current position" is a valid answer: the marker makes
    # end_date count as present, so the enrich loop converges instead of re-asking.
    profile = {
        "work_experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "is_current": True,
                "achievements": ["Led migration"],
                "expected_fields": [],
            }
        ]
    }
    gaps = gap_detector_mode_c(profile)
    assert "end_date: Senior Engineer @ Acme Corp" not in gaps
