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

"""US179 / #66 — Health hub completeness count must equal enrich-interview gap count.

``assess_health`` now exposes ``completeness.field_gaps`` (list[str]) populated
by the same role-aware ``field_gaps()`` function the no-JD enrichment interview
(Mode C) uses.  This test suite asserts the parity contract.

The section-level ``completeness.gaps`` list is NOT changed: the frontend renders
it as "Missing sections: education, languages" (HealthPanel gapsLabel), so it
must keep containing section names.
"""
from __future__ import annotations

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.health import assess_health


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ic_profile_with_missing_end_date_and_achievements() -> MasterProfileData:
    """IC (Individual Contributor) work entry missing end_date + achievements.

    ``expected_fields`` is an empty list → no conditional fields (team_size,
    budget_managed, industry_context) are expected.  Only the two floor gaps
    should be reported.
    """
    return MasterProfileData.model_validate({
        "work_experience": [
            {
                "role": "Junior Developer",
                "company": "Acme",
                "expected_fields": [],   # IC: no conditional fields expected
                "start_date": "2021-01",
                "end_date": None,        # GAP
                "achievements": [],      # GAP (empty list = missing)
            }
        ]
    })


def _manager_profile_missing_all_conditional() -> MasterProfileData:
    """Manager entry missing team_size + budget_managed (both expected)."""
    return MasterProfileData.model_validate({
        "work_experience": [
            {
                "role": "Engineering Manager",
                "company": "BigCorp",
                "expected_fields": ["team_size", "budget_managed"],
                "start_date": "2019-03",
                "end_date": "2023-06",
                "achievements": ["Led reorg"],
                "team_size": None,        # GAP
                "budget_managed": None,   # GAP
            }
        ]
    })


def _full_ic_profile() -> MasterProfileData:
    """IC entry with all floor fields present — no field gaps."""
    return MasterProfileData.model_validate({
        "work_experience": [
            {
                "role": "Senior Developer",
                "company": "Widgets Inc",
                "expected_fields": [],
                "start_date": "2018-01",
                "end_date": "2023-12",
                "achievements": ["Built the thing"],
            }
        ],
        "professional_summary": {"de": "Zusammenfassung", "en": "Summary"},
    })


# ---------------------------------------------------------------------------
# Tests: field_gaps parity
# ---------------------------------------------------------------------------

class TestHealthFieldGapsParity:
    """The hub's ``completeness.field_gaps`` must equal what the enrich interview sees."""

    def test_field_gaps_attribute_exists_on_completeness_block(self):
        """CompletenessBlock must expose a ``field_gaps`` field (list[str])."""
        health = assess_health(_ic_profile_with_missing_end_date_and_achievements())
        assert hasattr(health.completeness, "field_gaps"), (
            "CompletenessBlock is missing the field_gaps attribute"
        )
        assert isinstance(health.completeness.field_gaps, list)

    def test_ic_entry_reports_exactly_two_enrichable_gaps(self):
        """IC entry missing end_date + achievements → 2 field gaps, not 4.

        team_size and budget_managed must NOT appear because expected_fields=[]
        means they are NOT expected for this IC entry.
        """
        health = assess_health(_ic_profile_with_missing_end_date_and_achievements())
        field_gaps = health.completeness.field_gaps

        assert len(field_gaps) == 2, (
            f"Expected 2 enrichable gaps for IC entry; got {len(field_gaps)}: {field_gaps}"
        )

    def test_team_size_and_budget_not_in_ic_field_gaps(self):
        """Conditional fields must not appear in IC gap list."""
        health = assess_health(_ic_profile_with_missing_end_date_and_achievements())
        field_gaps = health.completeness.field_gaps

        gap_names = {g.split(":")[0] for g in field_gaps}
        assert "team_size" not in gap_names, (
            f"team_size should not be in IC field_gaps; got: {field_gaps}"
        )
        assert "budget_managed" not in gap_names, (
            f"budget_managed should not be in IC field_gaps; got: {field_gaps}"
        )

    def test_end_date_and_achievements_are_in_ic_field_gaps(self):
        """The two missing floor fields must appear in the gap list."""
        health = assess_health(_ic_profile_with_missing_end_date_and_achievements())
        field_gaps = health.completeness.field_gaps

        gap_names = {g.split(":")[0] for g in field_gaps}
        assert "end_date" in gap_names, f"end_date gap missing; got: {field_gaps}"
        assert "achievements" in gap_names, f"achievements gap missing; got: {field_gaps}"

    def test_manager_entry_reports_two_conditional_gaps(self):
        """Manager entry missing team_size + budget_managed → 2 field gaps."""
        health = assess_health(_manager_profile_missing_all_conditional())
        field_gaps = health.completeness.field_gaps

        gap_names = {g.split(":")[0] for g in field_gaps}
        assert "team_size" in gap_names
        assert "budget_managed" in gap_names
        # Floor fields are all present → no floor gaps.
        assert "end_date" not in gap_names
        assert "achievements" not in gap_names

    def test_field_gaps_matches_completeness_field_gaps_function_directly(self):
        """health.completeness.field_gaps must equal completeness.field_gaps(profile_dict)."""
        from applire.services.profile.completeness import field_gaps as compute_field_gaps

        profile = _ic_profile_with_missing_end_date_and_achievements()
        health = assess_health(profile)
        expected = compute_field_gaps(profile.model_dump())

        assert health.completeness.field_gaps == expected, (
            f"Mismatch between hub field_gaps={health.completeness.field_gaps!r} "
            f"and compute_field_gaps={expected!r}"
        )

    def test_full_ic_profile_has_no_field_gaps(self):
        """A fully filled IC profile must report zero field gaps."""
        health = assess_health(_full_ic_profile())
        assert health.completeness.field_gaps == []

    def test_section_gaps_unchanged_still_contains_section_names(self):
        """Existing completeness.gaps must still return section-level names for the UI.

        HealthPanel renders ``gaps.join(", ")`` as "Missing sections: X, Y" — this
        contract must not break.
        """
        profile = MasterProfileData.model_validate({
            "work_experience": [],
            "education": [],
        })
        health = assess_health(profile)
        # Section gaps still exist and don't look like field:label strings.
        for section_gap in health.completeness.gaps:
            assert ":" not in section_gap, (
                f"gaps should contain section names, not field:label strings; "
                f"got: {section_gap!r}"
            )
