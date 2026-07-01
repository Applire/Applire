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
US147 — deterministic pre-download diff (JF-M-6.1). Flags discrete, verifiable
divergences between the generated CV and the Master Profile: fabricated employers,
mutated dates, changed/inflated titles, and ungrounded skills. Semantic bullet
grounding stays the ADR-021 LLM reviewer's job — this is the no-LLM complement.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv_diff import compute_cv_profile_diff  # noqa: E402

_PROFILE = {
    "work_experience": [
        {"company": "Acme GmbH", "role": "Software Developer",
         "role_aliases": ["Backend Developer"], "start_date": "2020-01", "end_date": "2022-12"}
    ],
    "skills": [{"name": "Python"}, {"name": "PostgreSQL"}],
}


def _cv(**over) -> dict:
    base = {
        "work_history": [
            {"company": "Acme GmbH", "role": "Software Developer",
             "start_date": "2020-01", "end_date": "2022-12", "bullets": ["Built APIs"]}
        ],
        "skills": ["Python", "PostgreSQL"],
    }
    base.update(over)
    return base


def _fields(diff, section=None):
    return [(c.section, c.field, c.action) for c in diff if section is None or c.section == section]


class TestCVProfileDiff:
    def test_clean_cv_has_no_diff(self):
        assert compute_cv_profile_diff(_cv(), _PROFILE) == []

    def test_title_in_role_aliases_is_not_flagged(self):
        cv = _cv(work_history=[{"company": "Acme GmbH", "role": "Backend Developer",
                                "start_date": "2020-01", "end_date": "2022-12", "bullets": []}])
        assert _fields(compute_cv_profile_diff(cv, _PROFILE), "work_experience") == []

    def test_fabricated_employer_flagged(self):
        cv = _cv(work_history=[{"company": "Globex Corp", "role": "VP", "start_date": "2019-01",
                                "end_date": "2020-01", "bullets": []}])
        diff = compute_cv_profile_diff(cv, _PROFILE)
        assert any(c.section == "work_experience" and c.field == "company" for c in diff)

    def test_mutated_start_date_flagged(self):
        cv = _cv(work_history=[{"company": "Acme GmbH", "role": "Software Developer",
                                "start_date": "2017-01", "end_date": "2022-12", "bullets": []}])
        diff = compute_cv_profile_diff(cv, _PROFILE)
        assert any(c.field == "start_date" and c.action == "updated" for c in diff)

    def test_changed_title_is_not_flagged(self):
        # ADR-040 amendment (2026-07-01): a tailored title differing from the stored
        # role is expected tailoring, not a red flag — the ADR-021 reviewer covers
        # genuine inflation. The pre-download notice must not accuse on rewording.
        cv = _cv(work_history=[{"company": "Acme GmbH", "role": "Lead Architect",
                                "start_date": "2020-01", "end_date": "2022-12", "bullets": []}])
        diff = compute_cv_profile_diff(cv, _PROFILE)
        assert not any(c.field == "role" for c in diff)

    def test_ungrounded_skill_flagged(self):
        cv = _cv(skills=["Python", "Kubernetes"])
        diff = compute_cv_profile_diff(cv, _PROFILE)
        flagged = [c.new_value for c in diff if c.section == "skills"]
        assert "Kubernetes" in flagged and "Python" not in flagged

    def test_every_diff_item_has_rationale(self):
        cv = _cv(work_history=[{"company": "Globex Corp", "role": "VP", "start_date": "2019-01",
                                "end_date": "2020-01", "bullets": []}], skills=["Rust"])
        diff = compute_cv_profile_diff(cv, _PROFILE)
        assert diff and all(c.rationale for c in diff)


class TestGetCVProfileDiffService:
    @pytest.mark.asyncio
    async def test_loads_cv_and_profile_then_diffs(self):
        from unittest.mock import AsyncMock, MagicMock
        from applire.services.cv_diff import get_cv_profile_diff

        cv = MagicMock()
        cv.tailored_data = _cv(skills=["Python", "Kubernetes"])
        cv.profile_id = "p-1"
        profile = MagicMock()
        profile.profile_json = _PROFILE

        db = AsyncMock()
        db.get.side_effect = [cv, profile]  # GeneratedCV, then MasterProfile

        result = await get_cv_profile_diff("cv-1", db)
        assert result.grounded is False
        assert any(c.section == "skills" and c.new_value == "Kubernetes" for c in result.items)

    @pytest.mark.asyncio
    async def test_clean_cv_is_grounded(self):
        from unittest.mock import AsyncMock, MagicMock
        from applire.services.cv_diff import get_cv_profile_diff

        cv = MagicMock(); cv.tailored_data = _cv(); cv.profile_id = "p-1"
        profile = MagicMock(); profile.profile_json = _PROFILE
        db = AsyncMock(); db.get.side_effect = [cv, profile]

        result = await get_cv_profile_diff("cv-1", db)
        assert result.grounded is True and result.items == []

    @pytest.mark.asyncio
    async def test_unknown_cv_raises(self):
        from unittest.mock import AsyncMock
        import pytest as _pytest
        from applire.services.cv_diff import get_cv_profile_diff

        db = AsyncMock(); db.get.side_effect = [None]
        with _pytest.raises(ValueError):
            await get_cv_profile_diff("nope", db)
