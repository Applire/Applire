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

"""E042 / US237 — the computed bullet budget must reach both LLM generation paths
(ADR-051 §3): the single-call user prompt, the segmented outline prompt, and the
segmented per-role work-section prompt. String-level assertions only; no LLM.
"""

from datetime import date

from applire.prompts.cv_segmented import (
    OUTLINE_SYSTEM_PROMPT,
    WORK_SECTION_SYSTEM_PROMPT,
    build_outline_prompt,
    build_work_section_prompt,
)
from applire.prompts.cv_tailoring import SYSTEM_PROMPT, build_user_prompt
from applire.services.cv_budget import compute_bullet_budgets

_JOB = {"role_title": "Engineer", "required_skills": [], "keywords": []}
_PROFILE = {"work_experience": []}
_ENTRIES = [
    {"id": "w1", "company": "Acme", "role": "Senior Engineer", "start_date": "2024-01",
     "end_date": None, "is_current": True, "bullets": []},
]
_BUDGET = compute_bullet_budgets(_ENTRIES, None, target_pages=2, today=date(2026, 7, 15))


def test_single_call_system_prompt_states_the_budget_rule():
    assert "ROLE BULLET BUDGETS" in SYSTEM_PROMPT or "bullet-count ceiling" in SYSTEM_PROMPT


def test_single_call_system_prompt_keeps_existing_rule_numbering():
    # Rule 6 (entry-count contract) is load-bearing — must not be renumbered.
    assert "6. The number of work_history entries in your output must equal exactly" in SYSTEM_PROMPT


def test_build_user_prompt_carries_the_budget_block():
    prompt = build_user_prompt(_JOB, _PROFILE, [], [], budget=_BUDGET)
    assert "ROLE BULLET BUDGETS" in prompt
    assert "[w1]" in prompt
    assert "Acme" in prompt and "Senior Engineer" in prompt
    assert f"max {_BUDGET.roles['w1'].max_bullets} bullet" in prompt


def test_build_user_prompt_without_budget_omits_the_block():
    prompt = build_user_prompt(_JOB, _PROFILE, [], [])
    assert "ROLE BULLET BUDGETS" not in prompt


def test_outline_system_prompt_mentions_the_budget():
    assert "ROLE BULLET BUDGETS" in OUTLINE_SYSTEM_PROMPT or "bullet-count ceiling" in OUTLINE_SYSTEM_PROMPT


def test_build_outline_prompt_carries_the_budget_table():
    prompt = build_outline_prompt(_JOB, _PROFILE, "en", budget=_BUDGET)
    assert "ROLE BULLET BUDGETS" in prompt
    assert "[w1]" in prompt


def test_build_outline_prompt_without_budget_omits_the_block():
    prompt = build_outline_prompt(_JOB, _PROFILE, "en")
    assert "ROLE BULLET BUDGETS" not in prompt


def test_work_section_system_prompt_mentions_max_bullets_constraint():
    assert "MAX BULLETS FOR THIS ENTRY" in WORK_SECTION_SYSTEM_PROMPT


def test_build_work_section_prompt_carries_this_roles_max_bullets():
    entry = {"id": "w1", "company": "Acme", "role": "Senior Engineer"}
    prompt = build_work_section_prompt(entry, {}, _JOB, [], "en", budget=_BUDGET)
    assert "MAX BULLETS FOR THIS ENTRY" in prompt
    assert str(_BUDGET.roles["w1"].max_bullets) in prompt


def test_build_work_section_prompt_without_budget_omits_the_constraint():
    entry = {"id": "w1", "company": "Acme", "role": "Senior Engineer"}
    prompt = build_work_section_prompt(entry, {}, _JOB, [], "en")
    assert "MAX BULLETS FOR THIS ENTRY" not in prompt


def test_build_work_section_prompt_unknown_role_id_omits_the_constraint():
    entry = {"id": "unknown-role", "company": "Acme", "role": "Senior Engineer"}
    prompt = build_work_section_prompt(entry, {}, _JOB, [], "en", budget=_BUDGET)
    assert "MAX BULLETS FOR THIS ENTRY" not in prompt
