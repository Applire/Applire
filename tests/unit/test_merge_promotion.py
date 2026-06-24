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
Issue #71 / finding F2 — a promotion must not collapse into one role.

The merge engine fused two same-employer work entries on *company + overlapping
dates* alone, never comparing the titles. A career progression at one employer
(e.g. "Senior Software Engineer" → "Engineering Lead, Platform") was therefore
folded into a single role: the newer (often current, more senior) title was
demoted to a `role_aliases` entry and the headline kept the older title. That
is silent data loss of a real role.

Design decision (implemented here): two same-employer entries stay TWO separate
roles when EITHER their titles differ (a promotion is a different title) OR their
date ranges do not overlap. Only merge when it is clearly the *same* role
re-imported — equivalent title AND overlapping/identical dates. When in doubt,
preserve both; never collapse.
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


class TestPromotionStaysSeparate:
    def test_promotion_same_employer_overlapping_dates_is_two_roles(self):
        """The F2 reproducer: Senior IC promoted to Lead at the same employer,
        both open-ended (overlapping) — must remain TWO roles, not collapse."""
        existing = _profile(
            WorkEntry(
                company="Logivia GmbH",
                role="Senior Software Engineer",
                start_date="2020-03",
                end_date=None,
            )
        )
        incoming = _profile(
            WorkEntry(
                company="Logivia GmbH",
                role="Engineering Lead, Platform",
                start_date="2023-01",
                end_date=None,
            )
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        roles = {w.role for w in result.merged_profile.work_experience}
        assert len(result.merged_profile.work_experience) == 2, (
            "promotion collapsed into one role (data loss) — got "
            f"{[w.role for w in result.merged_profile.work_experience]}"
        )
        assert "Senior Software Engineer" in roles
        assert "Engineering Lead, Platform" in roles

    def test_promotion_title_not_demoted_to_role_alias(self):
        """The newer, more senior title must survive as its own role, not be
        hidden inside the older role's role_aliases."""
        existing = _profile(
            WorkEntry(company="Logivia GmbH", role="Senior Software Engineer",
                      start_date="2020-03", end_date=None)
        )
        incoming = _profile(
            WorkEntry(company="Logivia GmbH", role="Engineering Lead, Platform",
                      start_date="2023-01", end_date=None)
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        senior = next(
            w for w in result.merged_profile.work_experience
            if w.role == "Senior Software Engineer"
        )
        assert "engineering lead, platform" not in {a.lower() for a in senior.role_aliases}

    def test_distinct_titles_same_employer_no_dates_stay_separate(self):
        """Even with no dates at all, two clearly distinct titles at the same
        employer must default to preserving both (never collapse on doubt)."""
        existing = _profile(WorkEntry(company="Acme GmbH", role="Marketing Manager"))
        incoming = _profile(WorkEntry(company="Acme GmbH", role="Software Architect"))
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 2


class TestSameRoleReimportStillMerges:
    """Companion guard — do not over-correct into duplicate roles."""

    def test_identical_role_overlapping_dates_merges(self):
        """The same role re-imported (identical title, overlapping dates) must
        still merge into one — accumulating facets, not duplicating the role."""
        existing = _profile(
            WorkEntry(company="Logivia GmbH", role="Senior Software Engineer",
                      start_date="2020-03", end_date=None,
                      responsibilities=["Owned the payments service"])
        )
        incoming = _profile(
            WorkEntry(company="Logivia GmbH", role="Senior Software Engineer",
                      start_date="2020-03", end_date=None,
                      responsibilities=["Mentored two juniors"])
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 1
        entry = result.merged_profile.work_experience[0]
        assert "Owned the payments service" in entry.responsibilities
        assert "Mentored two juniors" in entry.responsibilities

    def test_equivalent_title_refinement_overlapping_dates_merges(self):
        """A more-specific phrasing of the SAME role (one title's significant
        tokens are a subset of the other's) with overlapping dates is the same
        position, not a promotion — keep the existing chimera-test behaviour."""
        existing = _profile(
            WorkEntry(company="Roche Diagnostics GmbH", role="QA",
                      start_date="2020-01", end_date="2023-01")
        )
        incoming = _profile(
            WorkEntry(company="Roche Diagnostics GmbH", role="Senior QA",
                      start_date="2020-01", end_date="2023-01")
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 1
        entry = result.merged_profile.work_experience[0]
        aliases = {a.lower() for a in entry.role_aliases} | {entry.role.lower()}
        assert "senior qa" in aliases
