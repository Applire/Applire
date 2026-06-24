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
Finding N2 (post-PQ fast-follow) — merged role bullets are not de-duplicated.

When merging two documents for the same person, a role's achievement/bullet list
accumulated near-identical phrasings of the same achievement (e.g. one CV says
"Cut median dispatch latency from 800 ms to 210 ms" and the other says
"…cutting median dispatch latency from 800ms to 210ms."). The union was only
de-duplicated on case-insensitive *exact* equality, so cosmetic differences
(trailing punctuation, internal spacing) and shorter-phrasings-contained-in-a-
longer-one survived as separate bullets.

Design decision (implemented here): collapse near-duplicate bullets when
combining a role's responsibilities/achievements:
- normalise whitespace/case/punctuation and drop exact-after-normalisation dups;
- drop a bullet whose normalised text is fully contained in another (keep the
  longer, more complete phrasing).
Conservative — genuinely distinct achievements are always preserved.
Deterministic — no LLM.
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


class TestMergedBulletDedup:
    def test_near_identical_achievements_are_collapsed(self):
        """The N2 reproducer: the same achievement phrased three ways across two
        documents must collapse to one bullet, keeping the fullest phrasing."""
        existing = _profile(
            WorkEntry(
                company="Logivia GmbH",
                role="Senior Software Engineer",
                start_date="2020-03",
                end_date=None,
                achievements=[
                    "Led the redesign of the order-routing service, cutting median dispatch latency from 800ms to 210ms.",
                ],
            )
        )
        incoming = _profile(
            WorkEntry(
                company="Logivia GmbH",
                role="Senior Software Engineer",
                start_date="2020-03",
                end_date=None,
                achievements=[
                    # Contained-in-longer (shorter phrasing of the same bullet)
                    "Led the redesign of the order-routing service",
                    # Exact-after-normalisation duplicate (spacing + trailing period)
                    "Led the redesign of the order-routing service, cutting median dispatch latency from 800 ms to 210 ms",
                ],
            )
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        assert len(result.merged_profile.work_experience) == 1
        entry = result.merged_profile.work_experience[0]
        assert len(entry.achievements) == 1, (
            "near-identical achievements were not de-duplicated — got "
            f"{entry.achievements}"
        )

    def test_distinct_achievements_are_all_preserved(self):
        """Genuinely different achievements must never be dropped by the guard."""
        existing = _profile(
            WorkEntry(
                company="PayBridge AG",
                role="Backend Engineer",
                start_date="2017-01",
                end_date="2020-02",
                achievements=["Reduced p99 checkout latency by 40%"],
            )
        )
        incoming = _profile(
            WorkEntry(
                company="PayBridge AG",
                role="Backend Engineer",
                start_date="2017-01",
                end_date="2020-02",
                achievements=[
                    "Reduced p99 checkout latency by 40%",  # exact dup → dropped
                    "Introduced contract testing across 12 services",  # distinct → kept
                    "Mentored three junior engineers to mid-level",  # distinct → kept
                ],
            )
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        entry = result.merged_profile.work_experience[0]
        assert "Reduced p99 checkout latency by 40%" in entry.achievements
        assert "Introduced contract testing across 12 services" in entry.achievements
        assert "Mentored three junior engineers to mid-level" in entry.achievements
        assert len(entry.achievements) == 3

    def test_responsibilities_are_also_deduped(self):
        """The same near-dup guard applies to responsibilities, not just achievements."""
        existing = _profile(
            WorkEntry(
                company="Logivia GmbH",
                role="Senior Software Engineer",
                start_date="2020-03",
                end_date=None,
                responsibilities=["Owned the payments platform roadmap."],
            )
        )
        incoming = _profile(
            WorkEntry(
                company="Logivia GmbH",
                role="Senior Software Engineer",
                start_date="2020-03",
                end_date=None,
                responsibilities=[
                    "Owned the payments platform roadmap",  # punctuation-only dup
                    "On-call rotation for the payments platform",  # distinct → kept
                ],
            )
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        entry = result.merged_profile.work_experience[0]
        assert len(entry.responsibilities) == 2
        assert "On-call rotation for the payments platform" in entry.responsibilities
