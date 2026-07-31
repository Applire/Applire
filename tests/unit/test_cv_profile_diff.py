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
US147 / ADR-067 clause 9 (E049 49.7) — the pre-download diff is skills-only.

Employer/role/date detection is retired: those fields are vault-joined
transcription since ADR-067 (`assemble_tailored_cv`, fail-closed ids) and can
no longer diverge, so detection code would be a control that can never fire.
The structural guarantee is pinned here as an ABSENCE assertion: mutated
work-history fields in the input must produce NO diff items — the surface
states the guarantee instead (frontend `WhatChangedReview`).

The retained skills half is grounded against BOTH `skills[].name` and the
vault's literal narrative corpus (#395): a narrative-backed skill is truthful
content and must not be flagged.
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
         "start_date": "2020-01", "end_date": "2022-12",
         "achievements": ["Kostenrechnung für drei Werke aufgebaut."]}
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


class TestCVProfileDiff:
    def test_clean_cv_has_no_diff(self):
        assert compute_cv_profile_diff(_cv(), _PROFILE) == []

    def test_ungrounded_skill_flagged(self):
        cv = _cv(skills=["Python", "Kubernetes"])
        diff = compute_cv_profile_diff(cv, _PROFILE)
        flagged = [c.new_value for c in diff if c.section == "skills"]
        assert "Kubernetes" in flagged and "Python" not in flagged

    def test_narrative_backed_skill_is_not_flagged(self):
        """#395: `Kostenrechnung` is evidenced in a work achievement but absent
        from skills[].name — it is grounded content, and flagging it accused
        the candidate of inventing a skill they demonstrably hold."""
        cv = _cv(skills=["Python", "Kostenrechnung"])
        diff = compute_cv_profile_diff(cv, _PROFILE)
        assert [c.new_value for c in diff] == []

    def test_work_history_mutations_produce_no_diff_items(self):
        """ADR-067 clause 9: employer/role/date detection is RETIRED — those
        fields are vault-joined and structurally cannot diverge in a real
        generation. This absence assertion pins the retirement: even a
        hand-mutated input yields no work_experience items, because the
        detection code is gone, not merely quiet."""
        cv = _cv(work_history=[{"company": "Globex Corp", "role": "VP",
                                "start_date": "1999-01", "end_date": "2001-01",
                                "bullets": []}])
        diff = compute_cv_profile_diff(cv, _PROFILE)
        assert [c for c in diff if c.section == "work_experience"] == []

    def test_every_diff_item_has_rationale(self):
        cv = _cv(skills=["Rust"])
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
