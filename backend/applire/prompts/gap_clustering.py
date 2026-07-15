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

# Prompt version: v1
# Used by: services/gap.py (cluster_gaps)

import json

CLUSTERING_SYSTEM_PROMPT = """\
You are an expert career analyst. Your task is to group semantically related skill gaps into \
named clusters for a DACH job applicant.

Input: lists of Category B and Category C gaps from a gap analysis, plus the job's required and \
nice-to-have skills.

Output: a JSON object with a single key "clusters" whose value is an array of cluster objects. \
Each cluster absorbs multiple gaps that share the same semantic domain.

Schema for each cluster object:
{
  "id": "cluster-<label-kebab-lowercase>",
  "label": "Human-readable cluster name (concise, 2-5 words)",
  "category": "C or B (C if any member gap is Category C, else B)",
  "gaps": ["exact gap strings absorbed into this cluster"],
  "jd_skills": ["matching entries from required_skills or nice_to_have_skills"],
  "jd_context": "One sentence, first-person role perspective, written in the OUTPUT LANGUAGE stated in the user message, explaining why this cluster matters for the role."
}

Rules:
- Merge gaps that share the same semantic domain (not just keywords)
- category = "C" if any constituent gap is Category C, else "B"
- jd_skills: only include skills that directly motivate this cluster (may be empty)
- jd_context: one sentence, written entirely in the OUTPUT LANGUAGE stated in the user message (the candidate's UI language), regardless of the language of the gaps or the job description
- Target 5-12 clusters; never more clusters than input gaps
- Every input gap must appear in exactly one cluster
- Respond ONLY with a valid JSON object of the form {"clusters": [ ... ]} - no markdown, no explanations"""


def build_clustering_prompt(
    category_b: list[str],
    category_c: list[str],
    required_skills: list[str],
    nice_to_have_skills: list[str],
    lang: str = "de",
) -> str:
    # #3 (ADR-038): the gaps page is conversational — jd_context follows the candidate's
    # UI language, not the JD's. An explicit directive is more reliable than "the JD's language".
    language_name = "GERMAN" if lang == "de" else "ENGLISH"
    return (
        f"Category C gaps (missing evidence):\n{json.dumps(category_c, ensure_ascii=False)}\n\n"
        f"Category B gaps (likely but unstated):\n{json.dumps(category_b, ensure_ascii=False)}\n\n"
        f"Required skills from JD:\n{json.dumps(required_skills, ensure_ascii=False)}\n\n"
        f"Nice-to-have skills from JD:\n{json.dumps(nice_to_have_skills, ensure_ascii=False)}\n\n"
        f"OUTPUT LANGUAGE: write every cluster's jd_context entirely in {language_name}.\n\n"
        'Group the gaps into semantic clusters. Return a JSON object: {"clusters": [ ... ]}.'
    )
