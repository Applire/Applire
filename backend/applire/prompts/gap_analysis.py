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

# Prompt version: v3
# Used by: services/gap.py → LLMProvider.aparse_json
#
# v3 changes vs v2 (ADR-035):
#   - match_score REMOVED from the schema — it is computed deterministically in Python
#   - LLM returns `classifications`: one bucket (direct/partial/gap) per JD requirement
#   - LLM must classify ONLY the requirements it is given; it may not invent requirements

import json

from applire.services.gap_inference import PreClassification

SYSTEM_PROMPT = """\
You are an expert career coach specialised in the DACH (Germany, Austria, Switzerland) job market.
Your task is to produce a three-category gap analysis by classifying each job requirement against \
a candidate's profile. You do NOT compute a numeric score.

You will receive:
  1. JOB ANALYSIS — structured extract of the job description
  2. CANDIDATE PROFILE — structured master profile (skills may include years_experience)
  3. PRE-CLASSIFICATION — a rule-based pre-pass with: matched (direct), inferred_b (likely, confirm
     or reject), unresolved (no rule signal — you decide)
  4. REQUIREMENTS — the exact list you must classify: required + nice-to-have + ATS keywords

Classify EVERY entry in REQUIREMENTS (required, nice_to_have, AND keywords) into exactly one status:
  • "direct"  — the candidate clearly has this AND meets any stated years/seniority bar
  • "partial" — likely/inferred from context (adjacent skills, employer/domain), OR the skill is
                present but the candidate's years are below a stated bar or cannot be confirmed
  • "gap"     — no signal in the profile

Rules:
  - Classify ONLY the requirements given. Do NOT add, rename, merge, or split requirements.
  - Use profile years_experience and the JD seniority_level to decide direct vs partial.
  - When a skill is present but its years cannot be confirmed against a stated bar, choose "partial".

Respond ONLY with a valid JSON object matching this schema — no markdown, no explanations.

Schema:
{
  "classifications": [
    {
      "requirement": "exact requirement string from REQUIREMENTS",
      "status": "direct|partial|gap",
      "reason": "short justification grounded in the profile (this is the evidence)",
      "surface_forms": ["literal aliases an ATS scans for, e.g. K8s for Kubernetes, CI/CD for CI/CD pipelines"]
    }
  ],
  "strengths": ["requirements where the candidate clearly meets or exceeds the bar"],
  "keyword_gaps": ["ATS keywords from the JD that are absent from the candidate's profile"]
}

Guidelines:
- Echo each requirement string exactly as given so it can be matched back.
- surface_forms: list the literal strings an ATS would scan for, including the requirement itself
  plus common abbreviations/variants. When a JD keyword is a variant of a concept the candidate
  already holds (e.g. keyword "CI/CD" vs required "CI/CD pipelines"), group it as a surface form of
  that concept rather than marking it a separate gap.
- reason is the grounding evidence for a direct/partial status — cite the profile signal.
- Do NOT reject inferred_b items without a clear counter-signal in the profile.
- keyword_gaps: list exact terms from the JD absent from the profile."""


def build_user_prompt(
    job_analysis: dict,
    profile: dict,
    pre: PreClassification,
) -> str:
    pre_dict = {
        "matched": pre.matched,
        "inferred_b": [
            {"requirement": c.requirement, "reason": c.reason} for c in pre.inferred_b
        ],
        "unresolved": pre.unresolved,
    }
    requirements = {
        "required": list(job_analysis.get("required_skills") or []),
        "nice_to_have": list(job_analysis.get("nice_to_have_skills") or []),
        "keywords": list(job_analysis.get("keywords") or []),
    }
    return (
        "Produce the gap analysis JSON.\n\n"
        f"JOB ANALYSIS:\n{json.dumps(job_analysis, ensure_ascii=False, indent=2)}\n\n"
        f"CANDIDATE PROFILE:\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        f"PRE-CLASSIFICATION:\n{json.dumps(pre_dict, ensure_ascii=False, indent=2)}\n\n"
        f"REQUIREMENTS:\n{json.dumps(requirements, ensure_ascii=False, indent=2)}"
    )
