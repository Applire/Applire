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
US148 fix (JF-M-5.2) — the interview must record what it added to the profile as a
structured change list, so the "what we added from your answers" surface has data.
PQ found the interview enriched profile content but wrote no interview trail.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.interview_graph import interview_field_changes  # noqa: E402


def _p(skills=None, work=None, certs=None) -> dict:
    return {
        "skills": skills or [],
        "work_experience": work or [],
        "certifications": certs or [],
    }


class TestInterviewFieldChanges:
    def test_new_skill_recorded_as_added(self):
        before = _p(skills=[{"name": "Python"}])
        after = _p(skills=[{"name": "Python"}, "synchronous REST"])
        changes = interview_field_changes(before, after)
        skills = [c for c in changes if c.section == "skills"]
        assert any(str(c.new_value) == "synchronous REST" and c.action == "added" for c in skills)
        assert all(str(c.new_value) != "Python" for c in skills)  # pre-existing not recorded

    def test_every_change_has_rationale_and_no_noop(self):
        before = _p(skills=[{"name": "Python"}])
        after = _p(skills=[{"name": "Python"}])  # nothing added
        assert interview_field_changes(before, after) == []

    def test_new_work_entry_recorded(self):
        before = _p()
        after = _p(work=[{"company": "Roche", "role": "QA", "achievements": []}])
        changes = interview_field_changes(before, after)
        assert any(c.section == "work_experience" and c.action == "added" for c in changes)

    def test_achievement_growth_on_existing_entry_recorded_as_merged(self):
        before = _p(work=[{"company": "Acme", "role": "Dev", "achievements": ["A"]}])
        after = _p(work=[{"company": "Acme", "role": "Dev", "achievements": ["A", "B"]}])
        changes = interview_field_changes(before, after)
        assert any(c.section == "work_experience" and c.action == "merged" for c in changes)

    def test_new_certification_recorded(self):
        before = _p()
        after = _p(certs=[{"name": "AWS SAA"}])
        changes = interview_field_changes(before, after)
        assert any(c.section == "certifications" and str(c.new_value) == "AWS SAA" for c in changes)

    def test_all_changes_carry_rationale(self):
        before = _p()
        after = _p(skills=["Go"], certs=[{"name": "CKA"}], work=[{"company": "X", "role": "Y", "achievements": []}])
        changes = interview_field_changes(before, after)
        assert changes and all(c.rationale for c in changes)
