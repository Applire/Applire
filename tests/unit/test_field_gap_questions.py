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

"""US179 / issue #66 — Mode C (profile_enrich) questions must target the
specific missing field on the specific position, NOT fall through to the
exploratory JD-cluster (Category C) path with open-ended framing.

Tests use a CapturingProvider stub so we can assert on the EXACT prompt
the model receives, not just the question output.
"""

import pytest
from applire.services.interview_graph import question_generator_with_profile


class CapturingProvider:
    def __init__(self):
        self.prompt = None
        self.system = None

    async def acomplete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096, disable_thinking=None):
        self.prompt, self.system = prompt, system
        return "How many engineers reported to you as Team Lead at Acme?"

    async def aparse_json(self, prompt, *, system=None, temperature=0.1, max_tokens=4096, disable_thinking=None):
        # language reviewer passes through
        return {"approved": True, "issues": [], "feedback": ""}


@pytest.mark.asyncio
async def test_mode_c_question_targets_field_and_position():
    """Mode C must send a prompt that names the role, company and missing field —
    not the generic 'uncover any relevant experience' Category-C framing."""
    state = {
        "mode": "profile_enrich",
        "critical_gaps": ["achievements: Team Lead @ Acme"],
        "current_gap_index": 0,
        "messages": [],
    }
    profile = {
        "work_experience": [
            {
                "role": "Team Lead",
                "company": "Acme",
                "responsibilities": ["Ran sprint planning"],
            }
        ]
    }
    prov = CapturingProvider()
    out = await question_generator_with_profile(state, profile, prov, lang="en")

    assert "Team Lead" in prov.prompt and "Acme" in prov.prompt
    assert "achievements" in prov.prompt.lower()
    assert "uncover any relevant experience" not in prov.prompt.lower()  # not Category-C framing
    assert out["question"]
    assert out["choices"] is None


@pytest.mark.asyncio
async def test_mode_c_unmatched_label_does_not_crash():
    """Mode C with an unmatched label (entry not in profile) must fall through
    gracefully — no exception, still returns a question dict."""
    state = {
        "mode": "profile_enrich",
        "critical_gaps": ["team_size: Ghost @ Nowhere"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {},
    }
    profile = {"work_experience": []}
    prov = CapturingProvider()
    out = await question_generator_with_profile(state, profile, prov, lang="en")
    assert "question" in out  # falls through gracefully (no exception)


@pytest.mark.asyncio
async def test_mode_c_professional_summary_falls_through():
    """professional_summary gaps have no matching work entry — they must fall
    through to the existing path rather than hitting the field-aware branch."""
    state = {
        "mode": "profile_enrich",
        "critical_gaps": ["professional_summary"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {"professional_summary": {
            "id": "professional_summary",
            "label": "professional_summary",
            "gaps": [],
            "jd_skills": [],
            "jd_context": "",
        }},
    }
    profile = {"work_experience": []}
    prov = CapturingProvider()
    out = await question_generator_with_profile(state, profile, prov, lang="en")
    assert "question" in out  # falls through gracefully


@pytest.mark.asyncio
async def test_mode_c_history_included_in_prompt():
    """Recent messages are included in the field-gap prompt (last-4-messages idiom)."""
    state = {
        "mode": "profile_enrich",
        "critical_gaps": ["team_size: Senior Dev @ TechCorp"],
        "current_gap_index": 0,
        "messages": [
            {"role": "assistant", "content": "Tell me about your team."},
            {"role": "user", "content": "I led a small team."},
        ],
    }
    profile = {
        "work_experience": [
            {
                "role": "Senior Dev",
                "company": "TechCorp",
                "responsibilities": ["Backend development"],
            }
        ]
    }
    prov = CapturingProvider()
    await question_generator_with_profile(state, profile, prov, lang="en")

    assert "I led a small team" in prov.prompt
    assert "Senior Dev" in prov.prompt and "TechCorp" in prov.prompt
