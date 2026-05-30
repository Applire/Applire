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


def test_calculate_stats_counts_real_data():
    stats = _profile().calculate_stats()

    assert isinstance(stats, ProfileStats)
    assert stats.positions == 2  # len(work_experience)
    assert stats.projects == 3  # Σ achievements (2 + 1)
    assert stats.certifications == 0  # Lea has none — never the persona's 3
    # data_points = positions(2) + responsibilities(4) + achievements(3)
    #   + technologies(2) + skills(3) + education(1) + coursework(2)
    #   + certifications(0) + languages(2) + publications(0) + volunteer(0)
    assert stats.data_points == 19


def test_calculate_stats_empty_profile_is_all_zero():
    stats = MasterProfileData().calculate_stats()
    assert (stats.positions, stats.projects, stats.certifications, stats.data_points) == (0, 0, 0, 0)
