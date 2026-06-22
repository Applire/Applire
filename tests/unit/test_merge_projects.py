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
US172 (E034 / ADR-044) — additive merge for projects.

Projects are merged additively (ADR-013 accumulation-first):
- Identity/dedup key: project name (trimmed, casefolded).
- New name → append.
- Matching name → accumulate responsibilities/achievements/technologies (union,
  preserve order, no duplicates); fill empty scalar fields from incoming only when
  the existing value is empty/None; keep existing id.
- Never overwrite/replace; never lose an existing project.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData, ProjectEntry  # noqa: E402
from applire.services.profile.merge import merge_profiles  # noqa: E402
from applire.services.profile.reconciliation import (  # noqa: E402
    compute_merge_reconciliation,
)


def _profile(*projects: ProjectEntry) -> MasterProfileData:
    return MasterProfileData(projects=list(projects))


class TestMergeProjectsAdditive:
    def test_new_project_is_appended_existing_preserved(self):
        """An incoming project with a new name is appended; existing project is kept."""
        existing = _profile(
            ProjectEntry(
                name="Alpha",
                achievements=["a1"],
                technologies=["X"],
            )
        )
        incoming = _profile(
            ProjectEntry(
                name="Alpha",
                achievements=["a2"],
                technologies=["X", "Y"],
            ),
            ProjectEntry(name="Beta"),
        )

        result = merge_profiles(existing, incoming, source="cv_upload")
        projects = result.merged_profile.projects

        # Exactly 2 projects — no duplication, no loss
        assert len(projects) == 2, f"Expected 2 projects, got {len(projects)}: {[p.name for p in projects]}"

        alpha = next(p for p in projects if p.name.casefold() == "alpha")
        beta = next(p for p in projects if p.name.casefold() == "beta")

        # achievements: union of ["a1"] and ["a2"]
        assert sorted(alpha.achievements) == ["a1", "a2"], (
            f"Expected achievements union, got {alpha.achievements}"
        )
        # technologies: ["X", "Y"] — X deduped, Y added
        assert set(alpha.technologies) == {"X", "Y"}, (
            f"Expected technologies union {{X, Y}}, got {alpha.technologies}"
        )
        # "X" should not appear twice
        assert alpha.technologies.count("X") == 1, "X must not be duplicated"

        assert beta is not None, "New project 'Beta' must be present"

    def test_existing_non_empty_scalar_is_not_overwritten(self):
        """A filled scalar field on an existing project must not be overwritten by incoming."""
        existing_alpha = ProjectEntry(
            name="Alpha",
            description="Original description",
            url="https://original.example.com",
            start_date="2022-01",
            end_date="2022-12",
            role="Lead",
            associated_experience="job-123",
        )
        existing = _profile(existing_alpha)
        incoming = _profile(
            ProjectEntry(
                name="Alpha",
                description="Different description",
                url="https://different.example.com",
                start_date="2023-06",
                end_date="2023-12",
                role="Contributor",
                associated_experience="job-456",
            )
        )

        result = merge_profiles(existing, incoming, source="cv_upload")
        projects = result.merged_profile.projects

        assert len(projects) == 1
        alpha = projects[0]

        # Non-empty existing scalars must NOT be overwritten
        assert alpha.description == "Original description", (
            f"description should not be overwritten, got {alpha.description!r}"
        )
        assert alpha.url == "https://original.example.com", (
            f"url should not be overwritten, got {alpha.url!r}"
        )
        assert alpha.start_date == "2022-01", (
            f"start_date should not be overwritten, got {alpha.start_date!r}"
        )
        assert alpha.end_date == "2022-12", (
            f"end_date should not be overwritten, got {alpha.end_date!r}"
        )
        assert alpha.role == "Lead", (
            f"role should not be overwritten, got {alpha.role!r}"
        )
        assert alpha.associated_experience == "job-123", (
            f"associated_experience should not be overwritten, got {alpha.associated_experience!r}"
        )

    def test_empty_scalar_on_existing_is_filled_from_incoming(self):
        """An empty scalar on the existing project is gap-filled from incoming."""
        existing = _profile(
            ProjectEntry(
                name="Alpha",
                description=None,
                url=None,
                start_date=None,
            )
        )
        incoming = _profile(
            ProjectEntry(
                name="Alpha",
                description="Filled description",
                url="https://filled.example.com",
                start_date="2021-03",
            )
        )

        result = merge_profiles(existing, incoming, source="cv_upload")
        projects = result.merged_profile.projects

        assert len(projects) == 1
        alpha = projects[0]

        assert alpha.description == "Filled description"
        assert alpha.url == "https://filled.example.com"
        assert alpha.start_date == "2021-03"

    def test_existing_project_id_is_preserved(self):
        """The existing project id must never be replaced by the incoming id."""
        original_id = "stable-uuid-1234"
        existing = _profile(ProjectEntry(id=original_id, name="Alpha"))
        incoming = _profile(ProjectEntry(id="other-uuid-9999", name="Alpha"))

        result = merge_profiles(existing, incoming, source="cv_upload")
        projects = result.merged_profile.projects

        assert len(projects) == 1
        assert projects[0].id == original_id, (
            f"Existing id must be kept, got {projects[0].id!r}"
        )

    def test_name_matching_is_case_insensitive(self):
        """'ALPHA' and 'alpha' refer to the same project — must merge, not append."""
        existing = _profile(ProjectEntry(name="Alpha", achievements=["a1"]))
        incoming = _profile(ProjectEntry(name="ALPHA", achievements=["a2"]))

        result = merge_profiles(existing, incoming, source="cv_upload")
        projects = result.merged_profile.projects

        assert len(projects) == 1, (
            f"Case-insensitive match should produce 1 project, got {len(projects)}"
        )
        assert set(projects[0].achievements) == {"a1", "a2"}

    def test_list_accumulation_deduplicated(self):
        """Duplicate entries in responsibilities, achievements, technologies are not repeated."""
        existing = _profile(
            ProjectEntry(
                name="Alpha",
                responsibilities=["r1"],
                achievements=["a1"],
                technologies=["T1"],
            )
        )
        incoming = _profile(
            ProjectEntry(
                name="Alpha",
                responsibilities=["r1", "r2"],  # r1 is a duplicate
                achievements=["a1"],             # full duplicate
                technologies=["T1", "T2"],       # T1 is a duplicate
            )
        )

        result = merge_profiles(existing, incoming, source="cv_upload")
        alpha = result.merged_profile.projects[0]

        assert sorted(alpha.responsibilities) == ["r1", "r2"]
        assert alpha.achievements == ["a1"]
        assert set(alpha.technologies) == {"T1", "T2"}
        assert alpha.technologies.count("T1") == 1

    def test_intra_incoming_duplicates_are_folded(self):
        """Two same-named projects within the INCOMING list fold into one (no loss)."""
        existing = _profile()
        incoming = _profile(
            ProjectEntry(name="Alpha", responsibilities=["r1"]),
            ProjectEntry(name="Alpha", responsibilities=["r2"]),
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        projects = result.merged_profile.projects
        assert len(projects) == 1, f"intra-incoming dups must fold; got {[p.name for p in projects]}"
        assert set(projects[0].responsibilities) == {"r1", "r2"}

    def test_blank_name_projects_are_not_folded_together(self):
        """Blank-name projects have no usable identity — they must NOT collapse into one
        (that would silently drop content). Each is kept."""
        existing = _profile()
        incoming = _profile(
            ProjectEntry(name="", achievements=["did A"]),
            ProjectEntry(name="", achievements=["did B"]),
        )
        result = merge_profiles(existing, incoming, source="cv_upload")
        projects = result.merged_profile.projects
        assert len(projects) == 2, "blank-name projects must not be folded together"


class TestProjectsReconciliation:
    def test_projects_reported_in_reconciliation(self):
        """compute_merge_reconciliation includes a 'projects' entity after US172."""
        incoming = _profile(
            ProjectEntry(name="Alpha"),
            ProjectEntry(name="Beta"),
        )
        merged = _profile(
            ProjectEntry(name="Alpha"),
            ProjectEntry(name="Beta"),
        )

        rec = compute_merge_reconciliation(incoming, merged)

        assert "projects" in rec, (
            "reconciliation must include a 'projects' key after US172"
        )
        assert rec["projects"]["extracted"] == 2
        assert rec["projects"]["stored"] == 2
        assert rec["projects"]["delta"] == 0

    def test_dropped_project_flagged_as_data_loss(self):
        """A project in incoming but absent from merged produces a positive delta."""
        incoming = _profile(
            ProjectEntry(name="Alpha"),
            ProjectEntry(name="Beta"),
        )
        # Simulates a buggy merge that dropped Beta
        merged = _profile(ProjectEntry(name="Alpha"))

        rec = compute_merge_reconciliation(incoming, merged)

        assert rec["projects"]["extracted"] == 2
        assert rec["projects"]["stored"] == 1
        assert rec["projects"]["delta"] == 1

    def test_merge_profiles_reconciliation_includes_projects(self):
        """merge_profiles result.reconciliation includes projects after full merge."""
        existing = _profile(ProjectEntry(name="Alpha", achievements=["a1"]))
        incoming = _profile(
            ProjectEntry(name="Alpha", achievements=["a2"]),
            ProjectEntry(name="Beta"),
        )

        result = merge_profiles(existing, incoming, source="cv_upload")

        assert "projects" in result.reconciliation, (
            "merge_profiles.reconciliation must include 'projects'"
        )
        # incoming had 2 distinct project names; both must be stored
        assert result.reconciliation["projects"]["extracted"] == 2
        assert result.reconciliation["projects"]["stored"] == 2
        assert result.reconciliation["projects"]["delta"] == 0
