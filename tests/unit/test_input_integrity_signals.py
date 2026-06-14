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

"""Pure upload-time input-plausibility signals (Input Integrity sprint):
name-mismatch (US155/2.4), document-type (US154/2.3), completeness (US157/2.7)."""
from applire.schemas.profile import MasterProfileData
from applire.services.profile import (
    _looks_like_cv,
    _names_clearly_differ,
    _undated_positions,
)


def _profile(name: str = "", work=None, education=None, skills=None) -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": name},
            "work_experience": work or [],
            "education": education or [],
            "skills": skills or [],
        }
    )


class TestNameMismatch:
    def test_disjoint_names_warn(self):
        assert _names_clearly_differ("Milan Novak", "Anna Schmidt") is True

    def test_shared_surname_does_not_warn(self):
        assert _names_clearly_differ("Milan Novak", "Petra Novak") is False

    def test_accent_and_casing_variants_do_not_warn(self):
        assert _names_clearly_differ("Milan Novak", "milan novák") is False

    def test_empty_existing_name_does_not_warn(self):
        # First upload: no existing name to compare against.
        assert _names_clearly_differ("", "Anna Schmidt") is False


class TestLooksLikeCv:
    def test_work_history_is_a_cv(self):
        assert _looks_like_cv(_profile(work=[{"role": "Developer"}])) is True

    def test_name_plus_skills_is_a_cv(self):
        assert _looks_like_cv(_profile(name="Milan", skills=[{"name": "Python"}])) is True

    def test_empty_extraction_is_not_a_cv(self):
        # A JD / cover letter / slide deck extracts to ~nothing CV-like.
        assert _looks_like_cv(_profile()) is False


class TestNamelessExtraction:
    def test_personal_info_name_coerces_none_to_empty(self):
        # The LLM returns null name for a nameless document (a JD uploaded as a
        # CV). Extraction must not crash — coerce to "" so US154's looks_like_cv
        # warning can run instead of a 422 Pydantic error. (PQ find, 2026-06-14.)
        p = MasterProfileData.model_validate({"personal_info": {"name": None}})
        assert p.personal_info.name == ""
        assert _looks_like_cv(p) is False


class TestUndatedPositions:
    def test_counts_positions_missing_a_start_date(self):
        p = _profile(
            work=[
                {"role": "A", "start_date": "2020"},
                {"role": "B"},
                {"role": "C", "start_date": None},
            ]
        )
        assert _undated_positions(p) == 2

    def test_all_dated_returns_zero(self):
        p = _profile(work=[{"role": "A", "start_date": "2020-01"}])
        assert _undated_positions(p) == 0
