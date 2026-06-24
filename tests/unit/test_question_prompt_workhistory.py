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

"""Regression tests for build_question_prompt / build_follow_up_question_prompt.

Verifies that work_experience entries (the current schema field) are included in
the profile summary sent to the LLM — root cause #4 of US179/#66.
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.interview import build_question_prompt, build_follow_up_question_prompt


def test_work_experience_reaches_prompt():
    profile = {"skills": [], "work_experience": [{"company": "Acme", "role": "Team Lead"}]}
    out = build_question_prompt({"label": "Leadership", "gaps": []}, profile, [])
    assert "Acme" in out
    assert "Team Lead" in out


def test_legacy_work_history_key_not_required():
    # a profile using the new field must work even with no 'work_history' key present
    profile = {"skills": ["Python"], "work_experience": [{"company": "Beta", "role": "Engineer"}]}
    out = build_question_prompt({"label": "X", "gaps": []}, profile, [])
    assert "Beta" in out


def test_follow_up_work_experience_reaches_prompt():
    """build_follow_up_question_prompt has the same bug — both must be fixed."""
    profile = {"skills": [], "work_experience": [{"company": "Gamma", "role": "CTO"}]}
    out = build_follow_up_question_prompt(
        gap="Leadership",
        follow_up_hint="team management",
        profile=profile,
        recent_messages=[],
    )
    assert "Gamma" in out
    assert "CTO" in out
