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

# Prompt version: v2 (US172 / ADR-044)
# Used by: services/skill_enrichment.py → enrich_skills() → LLMProvider.aparse_json
#
# Single batch call: estimates years_experience for skills not found in any
# experience entry's technologies list. Receives all experience entries
# (jobs, projects, volunteering) for context.

import json

SKILL_ESTIMATION_SYSTEM_PROMPT = """\
You are a career analyst. Given a candidate's complete experience history (jobs, projects,
and volunteering) and a list of skill names, estimate how many years of experience the
candidate has with each skill based ONLY on the provided experience entries.

Rules:
- Base all estimates exclusively on the provided experience entries — do not fabricate or infer beyond what is stated.
- If a skill is mentioned implicitly by a role's responsibilities or industry context but no specific
  duration can be determined from the dates, use null.
- If there is genuinely no basis for estimating a skill's duration, use null.
- Return integer years only — no fractions, no ranges.
- Do not include skills not present in the input list.

Respond ONLY with a valid JSON object — no markdown, no explanations:
{"SkillName": integer_or_null, ...}"""


def build_skill_estimation_prompt(
    experience_entries: list[dict],
    skill_names: list[str],
) -> str:
    """Build the user message for the skill estimation LLM call.

    Args:
        experience_entries: List of experience entry dicts (WorkEntry, ProjectEntry,
                            VolunteerActivity — all serialised via model_dump).
                            Previously named ``work_experience``; renamed in v2 to
                            reflect that all experience kinds are now included (ADR-044).
        skill_names:        Skills to estimate — only names, no other metadata.
    """
    experience_json = json.dumps(experience_entries, ensure_ascii=False, indent=2)
    skills_json = json.dumps(skill_names, ensure_ascii=False)
    return (
        f"Experience history (jobs, projects, volunteering):\n{experience_json}\n\n"
        f"Estimate years of experience for each of the following skills:\n{skills_json}\n\n"
        'Return a JSON object: {"SkillName": integer_or_null, ...}'
    )
