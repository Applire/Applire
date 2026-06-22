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

"""Unit tests for MasterProfileData.calculate_stats() (bug 2 regression).

The gap page's "Master-Profil erstellt" tiles previously fell back to the
persona example numbers (5 / 12 / 3 / 47) because the API exposed no real
counts. calculate_stats() derives the tiles from the actual profile data.
"""
from applire.schemas.profile import MasterProfileData, ProfileStats


def _profile() -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "work_experience": [
                {
                    "company": "ACME",
                    "role": "Designer",
                    "responsibilities": ["r1", "r2", "r3"],
                    "achievements": ["a1", "a2"],
                    "technologies": ["KeyShot", "Figma"],
                },
                {
                    "company": "Globex",
                    "role": "Junior Designer",
                    "responsibilities": ["r1"],
                    "achievements": ["a1"],
                    "technologies": [],
                },
            ],
            "skills": [
                {"name": "KeyShot"},
                {"name": "Figma"},
                {"name": "SolidWorks"},
            ],
            "education": [
                {
                    "institution": "TU",
                    "degree": "MSc",
                    "relevant_coursework": ["c1", "c2"],
                }
            ],
            "certifications": [],
            "languages": [
                {"language": "Deutsch", "level": "native"},
                {"language": "Englisch", "level": "fluent"},
            ],
        }
    )


# ── NEW FAILING TEST (ST-D): write first, watch RED, then implement ────────────

def test_projects_counts_real_project_entries():
    """projects tile must reflect len(projects), NOT Σ work achievements (US172)."""
    profile = MasterProfileData.model_validate(
        {
            "work_experience": [
                {
                    "company": "ACME",
                    "role": "Designer",
                    "achievements": ["a1", "a2"],  # 2 work achievements — must NOT pollute projects
                },
            ],
            "projects": [
                {"role": "Lead", "achievements": ["p1"]},
                {"role": "Contributor", "achievements": []},
            ],
        }
    )
    stats = profile.calculate_stats()
    assert stats.projects == 2  # len(projects) — not Σ work achievements (2)


# ── Updated existing tests — new semantics ─────────────────────────────────────

def test_calculate_stats_counts_real_data():
    """Baseline profile with NO projects: projects tile == 0, data_points unchanged.

    Legacy data_points formula (work only):
      positions(2) + responsibilities(4) + work_achievements(3) + work_technologies(2)
      + skills(3) + education(1) + coursework(2) + certifications(0)
      + languages(2) + publications(0) + volunteer_count(0) = 19

    No projects/volunteer-achievements added, so total stays 19.
    """
    stats = _profile().calculate_stats()

    assert isinstance(stats, ProfileStats)
    assert stats.positions == 2       # len(work_experience)
    assert stats.projects == 0        # no ProjectEntry items → 0 (was Σ achievements = 3)
    assert stats.certifications == 0  # Lea has none
    # data_points preserves legacy value: 2+4+3+2+3+1+2+0+2+0+0 = 19
    assert stats.data_points == 19


def test_calculate_stats_empty_profile_is_all_zero():
    stats = MasterProfileData().calculate_stats()
    assert (stats.positions, stats.projects, stats.certifications, stats.data_points) == (0, 0, 0, 0)


def test_data_points_stable_without_projects():
    """A projects-free profile yields the same data_points as the old formula.

    Old formula: positions + Σresponsibilities + Σwork_achievements
                 + Σwork_technologies + skills + education + Σcoursework
                 + certifications + languages + publications + volunteer_count
    New formula replaces the misnomer 'projects' var with a direct term but must
    produce an identical sum when self.projects == [].
    """
    profile = MasterProfileData.model_validate(
        {
            "work_experience": [
                {
                    "company": "Widgets GmbH",
                    "role": "Engineer",
                    "responsibilities": ["r1", "r2"],
                    "achievements": ["a1", "a2", "a3"],
                    "technologies": ["Python", "FastAPI"],
                },
            ],
            "skills": [{"name": "Python"}, {"name": "SQL"}],
            "certifications": [{"name": "AWS SAA", "issuer": "Amazon", "year": 2023}],
            "languages": [{"language": "Deutsch", "level": "native"}],
        }
    )
    # Expected: positions(1)+responsibilities(2)+work_achievements(3)+work_tech(2)
    #           +skills(2)+education(0)+coursework(0)+certifications(1)
    #           +languages(1)+publications(0)+volunteer_count(0) = 12
    expected = 1 + 2 + 3 + 2 + 2 + 0 + 0 + 1 + 1 + 0 + 0
    stats = profile.calculate_stats()
    assert stats.data_points == expected
    assert stats.projects == 0  # no ProjectEntry → tile is 0


def test_data_points_increases_with_project_entries():
    """Adding a ProjectEntry with achievements/technologies raises data_points."""
    base = MasterProfileData.model_validate(
        {
            "work_experience": [
                {
                    "company": "ACME",
                    "role": "Dev",
                    "achievements": ["a1"],
                    "technologies": ["Go"],
                }
            ],
        }
    )
    with_project = MasterProfileData.model_validate(
        {
            "work_experience": [
                {
                    "company": "ACME",
                    "role": "Dev",
                    "achievements": ["a1"],
                    "technologies": ["Go"],
                }
            ],
            "projects": [
                {
                    "role": "Author",
                    "achievements": ["shipped MVP"],
                    "technologies": ["Rust", "WASM"],
                }
            ],
        }
    )
    base_dp = base.calculate_stats().data_points
    new_dp = with_project.calculate_stats().data_points
    # +1 project entry + 1 achievement + 2 technologies = +4 data_points
    assert new_dp == base_dp + 4
    assert with_project.calculate_stats().projects == 1
