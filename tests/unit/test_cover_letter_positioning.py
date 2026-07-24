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

"""E048/US264 — deterministic (no-LLM) positioning inputs.

Hermetic: pure functions, no LLM, no DB.
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# detect_concurrent_roles
# ---------------------------------------------------------------------------


def test_detect_concurrent_roles_true_when_two_current_roles():
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    work_experience = [
        {"role": "CTO", "company": "Startup A", "is_current": True, "end_date": None},
        {"role": "Advisor", "company": "Startup B", "is_current": True, "end_date": None},
    ]
    assert detect_concurrent_roles(work_experience) is True


def test_detect_concurrent_roles_true_for_open_end_date_without_is_current_flag():
    """is_current unset (legacy rows) + blank end_date still counts as open-ended."""
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    work_experience = [
        {"role": "Engineer", "company": "A", "end_date": None},
        {"role": "Consultant", "company": "B", "end_date": ""},
    ]
    assert detect_concurrent_roles(work_experience) is True


def test_detect_concurrent_roles_false_for_single_current_role():
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    work_experience = [
        {"role": "Engineer", "company": "A", "is_current": True, "end_date": None},
        {"role": "Past role", "company": "B", "is_current": False, "end_date": "2019"},
    ]
    assert detect_concurrent_roles(work_experience) is False


def test_detect_concurrent_roles_false_when_explicitly_ended_with_blank_end_date():
    """is_current=False must never count as open, even with a blank end_date —
    the tri-state convention (#155): False always means known-ended."""
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    work_experience = [
        {"role": "A", "company": "X", "is_current": False, "end_date": None},
        {"role": "B", "company": "Y", "is_current": False, "end_date": None},
    ]
    assert detect_concurrent_roles(work_experience) is False


def test_detect_concurrent_roles_empty_list():
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    assert detect_concurrent_roles([]) is False


# ---------------------------------------------------------------------------
# find_gap_testimony
# ---------------------------------------------------------------------------


def test_find_gap_testimony_matches_regulated_industries_argument():
    from applire.services.cover_letter_positioning import find_gap_testimony

    category_c = ["regulated industries experience"]
    stories = [
        {
            "title": "Bringing GxP rigor to a startup",
            "challenge": "The team had never worked in regulated industries before.",
            "mechanism": "I brought my prior pharma QA discipline to the process.",
            "outcome": "We passed our first audit with zero findings.",
            "benchmark": None,
        }
    ]
    result = find_gap_testimony(category_c, stories)
    assert result is not None
    assert result["gap"] == "regulated industries experience"
    assert result["story"]["title"] == "Bringing GxP rigor to a startup"


def test_find_gap_testimony_none_when_no_story_overlaps():
    from applire.services.cover_letter_positioning import find_gap_testimony

    category_c = ["Kubernetes orchestration"]
    stories = [
        {
            "title": "Winning a design award",
            "challenge": "Our brand felt generic.",
            "mechanism": "I ran a full visual identity overhaul.",
            "outcome": "We won a regional design award.",
        }
    ]
    assert find_gap_testimony(category_c, stories) is None


def test_find_gap_testimony_none_when_no_stories():
    from applire.services.cover_letter_positioning import find_gap_testimony

    assert find_gap_testimony(["some gap"], []) is None


def test_find_gap_testimony_first_matching_gap_wins():
    """category_c is already severity-ordered; the first gap with a positive
    story match wins (deterministic, no re-ranking across gaps)."""
    from applire.services.cover_letter_positioning import find_gap_testimony

    category_c = ["no story here at all", "regulated industries experience"]
    stories = [
        {
            "title": "Regulated industries pivot",
            "challenge": "New to regulated industries.",
            "mechanism": "Applied adjacent QA rigor.",
            "outcome": "Delivered a compliant release.",
        }
    ]
    result = find_gap_testimony(category_c, stories)
    assert result["gap"] == "regulated industries experience"


# ---------------------------------------------------------------------------
# find_availability_testimony
# ---------------------------------------------------------------------------


def test_find_availability_testimony_from_signature_story():
    from applire.services.cover_letter_positioning import find_availability_testimony

    stories = [
        {
            "title": "Managing concurrent commitments",
            "challenge": "I run two advisory roles in parallel.",
            "mechanism": "I block dedicated hours for each and communicate availability clearly.",
            "outcome": "Both engagements stayed on schedule.",
        }
    ]
    result = find_availability_testimony(stories, [])
    assert result is not None
    assert "parallel" in result


def test_find_availability_testimony_from_enrichment_history():
    from applire.services.cover_letter_positioning import find_availability_testimony

    enrichment_history = [
        {
            "source": "interview",
            "changes": [
                {
                    "section": "personal_info",
                    "field": "availability",
                    "action": "added",
                    "new_value": "Available immediately; current contract ends this month.",
                    "rationale": "candidate stated their availability",
                }
            ],
        }
    ]
    result = find_availability_testimony([], enrichment_history)
    assert result is not None
    assert "Available immediately" in result


def test_find_availability_testimony_none_when_nothing_matches():
    from applire.services.cover_letter_positioning import find_availability_testimony

    stories = [
        {"title": "Winning an award", "challenge": "x", "mechanism": "y", "outcome": "z"}
    ]
    enrichment_history = [
        {"changes": [{"rationale": "added a new skill", "new_value": "Python"}]}
    ]
    assert find_availability_testimony(stories, enrichment_history) is None


def test_find_availability_testimony_empty_inputs():
    from applire.services.cover_letter_positioning import find_availability_testimony

    assert find_availability_testimony([], []) is None
