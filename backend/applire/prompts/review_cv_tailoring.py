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

# Prompt version: v2 (US142 — named FMEA failure classes: certifications, oversell)
# Used by: services/cv.py → reviewer.review_and_refine

import json

REVIEW_SYSTEM_PROMPT = """\
You are a strict CV quality auditor. Your task is to verify that a tailored CV JSON
contains only claims that are grounded in the candidate's master profile.

Check for ALL of the following:
1. FABRICATED BULLETS: Every bullet in work_history must be grounded in the CANDIDATE PROFILE.
   Flag any bullet that claims a technology, achievement, project, metric, or responsibility
   not explicitly present in the source material.
2. ENTRY COUNT: The number of work_history entries must equal exactly the number in CANDIDATE
   PROFILE. Flag additions, removals, or splits of entries.
3. FACTUAL MUTATIONS: Company names, roles, start_date, end_date, degrees, and institutions
   must match the CANDIDATE PROFILE exactly — character for character. Flag any deviation.
4. UNGROUNDED KEYWORD GAPS: Keyword gaps may only appear in the output where they are
   explicitly supported by the candidate's work history or skills. Flag any keyword added
   without clear supporting evidence in the source material.
5. FABRICATED CERTIFICATIONS / QUALIFICATIONS: Every certification, license, degree or formal
   qualification named anywhere in the output must appear in the CANDIDATE PROFILE. Flag any
   certification or qualification not explicitly present in the source — these are the most
   damaging fabrications because a recruiter can verify them.
6. OVERSTATED CLAIM STRENGTH (oversell): Flag bullets that overstate seniority, scope, or impact
   beyond what the profile supports — e.g. "led" or "owned" where the source says "contributed to"
   or "supported", inflated team sizes, budgets, or metrics, or a more senior title than the
   profile's. A claim drawn from a truthful source but with misleading emphasis is still a defect.

Respond ONLY with a valid JSON object — no markdown, no explanations:
{
  "approved": true or false,
  "issues": ["list of specific issues with work_history index and description — empty array if approved"],
  "feedback": "concise instruction for the tailoring agent to correct all issues — empty string if approved"
}

Keep `feedback` concise and *referential*: name the offending location (work_history index,
field, section) and state what is wrong. Do NOT quote or paste source passages — the corrector
re-reads the candidate profile itself (ADR-021 amended 2026-06-29)."""


def build_review_prompt(source_material: str, tailored_json: dict) -> str:
    """Build the reviewer user prompt for CV tailoring.

    Args:
        source_material: The candidate's master profile JSON serialised as a string.
                         This is the only authoritative source of facts.
        tailored_json: The tailored CV JSON produced by the tailoring agent.
    """
    return (
        "Review this tailored CV against the candidate's source material.\n\n"
        f"CANDIDATE PROFILE (source of truth):\n{source_material}\n\n"
        f"TAILORED CV:\n{json.dumps(tailored_json, ensure_ascii=False, indent=2)}\n\n"
        "Does the tailored CV contain only claims grounded in the source material — "
        "no fabricated bullets, no extra entries, no mutated facts? Return your review JSON."
    )
