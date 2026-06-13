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

"""
US143 — chimera-merge guardrail (JF-M-3.4).

A chimera is two genuinely distinct positions fused into one false entry. The
risk vector is a *loose* (subset) company-name match — "Apple" ⊆ "Apple Bank" —
combined with the date-overlap "safe default" that returns True when a date is
missing. Exact company matches are strong enough to merge on fuzzy dates; subset
matches must be corroborated by a real, determinate date overlap.

ADR-013 accumulation rules are unchanged — this only tightens the *match*.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData, WorkEntry  # noqa: E402
from applire.services.profile.merge import merge_profiles  # noqa: E402


def _profile(*entries: WorkEntry) -> MasterProfileData:
    return MasterProfileData(work_experience=list(entries))


class TestChimeraGuard:
    def test_subset_company_with_indeterminate_dates_does_not_fuse(self):
        """'Apple' (real dates) vs 'Apple Bank' (no dates) are different employers.
        Subset match + missing date must NOT silently fuse them (JF-M-3.4)."""
        existing = _profile(WorkEntry(company="Apple Bank", role="Analyst"))
        incoming = _profile(
            WorkEntry(company="Apple", role="Engineer", start_date="2019-01", end_date="2021-01")
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 2

    def test_no_data_loss_two_distinct_subset_companies(self):
        """Both distinct entries survive — never discard data (ADR-013)."""
        existing = _profile(WorkEntry(company="Apple Bank", role="Analyst"))
        incoming = _profile(
            WorkEntry(company="Apple", role="Engineer", start_date="2019-01", end_date="2021-01")
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        companies = {w.company for w in result.merged_profile.work_experience}
        assert "Apple Bank" in companies and "Apple" in companies


class TestLegitimateMergesPreserved:
    def test_exact_company_fuzzy_dates_still_merges(self):
        """Exact company name is strong evidence — merge even when one side lacks dates."""
        existing = _profile(
            WorkEntry(company="Roche Diagnostics GmbH", role="QA", start_date="2020-01", end_date="2023-01")
        )
        incoming = _profile(WorkEntry(company="Roche Diagnostics GmbH", role="Senior QA"))
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 1
        entry = result.merged_profile.work_experience[0]
        aliases = {a.lower() for a in entry.role_aliases} | {entry.role.lower()}
        assert "senior qa" in aliases

    def test_subset_company_with_real_overlap_merges(self):
        """'Roche' ⊆ 'Roche Diagnostics GmbH' with a real date overlap is a legit short-form match."""
        existing = _profile(
            WorkEntry(company="Roche Diagnostics GmbH", role="QA", start_date="2020-01", end_date="2023-01")
        )
        incoming = _profile(
            WorkEntry(company="Roche", role="Senior QA", start_date="2021-01", end_date="2022-06")
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 1

    def test_different_company_never_fuses(self):
        existing = _profile(
            WorkEntry(company="BioNTech SE", role="Dev", start_date="2020-01", end_date="2022-01")
        )
        incoming = _profile(
            WorkEntry(company="CureVac AG", role="Dev", start_date="2020-06", end_date="2021-06")
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 2

    def test_adjacent_dates_same_company_stay_separate(self):
        """Shared boundary month is a job transition, not concurrent employment."""
        existing = _profile(
            WorkEntry(company="Acme GmbH", role="Junior", start_date="2019-01", end_date="2021-05")
        )
        incoming = _profile(
            WorkEntry(company="Acme GmbH", role="Senior", start_date="2021-05", end_date="2023-01")
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 2
