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
# Used by: services/gap.py (cluster_gaps)
#
# v3 (Hazelnut build 3 / WP-G, 2026-10-22): jd_context is a third-person sentence about the
# POSTING. v2 asked for a "first-person role perspective" and the model wrote it as the
# candidate speaking ("Ich stelle sichere Arbeitsbedingungen ... sicher", "I need German
# professional recognition") — 87 of 88 clusters over the 12-run baseline matrix. The gaps
# page shows it under "Was die Stelle erwartet:" and the interview prompt reads it as
# "JD context:", so a sentence in the candidate's voice put words in their mouth on both.
#
# v2 (#675 line 60, 2026-09-18): the cluster's B/C category is no longer solicited.
# It was stated here as a rule the model could only restate - "C if any constituent
# gap is Category C, else B" - and it is not a judgement at all: which input list a
# gap came from is a FACT the caller already holds (ADR-062 clause 1). Measured over
# every captured clustering record (63 records, 198 clusters, logs/llm + backend/
# logs/llm): 53 clusters carried a category that contradicted their own members,
# all 53 understating severity. cluster_gaps() now derives it.

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
  "gaps": ["exact gap strings absorbed into this cluster"],
  "jd_skills": ["matching entries from required_skills or nice_to_have_skills"],
  "jd_context": "One sentence about the POSTING, in the third person, written in the OUTPUT LANGUAGE stated in the user message: what the role expects in this area and why it matters for the role's work."
}

Rules:
- Merge gaps that share the same semantic domain (not just keywords)
- Copy each gap into "gaps" EXACTLY as it is written in the input list - same \
wording, same language, same spelling. Do not rephrase, expand or translate a gap, \
and never add one that is not in the two input lists.
- jd_skills: only include skills that directly motivate this cluster (may be empty)
- jd_context: one sentence, written entirely in the OUTPUT LANGUAGE stated in the user message \
(the candidate's UI language), regardless of the language of the gaps or the job description. \
It describes the POSTING - what the role expects in this area and why the work needs it - in the \
third person, e.g. "Die Stelle erwartet Erfahrung mit MES und Maschinendatenerfassung, weil die \
Fertigung digitalisiert wird." / "The role expects hands-on Kafka experience because payment \
events are processed as streams." It is shown to the candidate as what the role expects, so it \
never speaks as or to anyone: no first or second person (no ich/wir/du/Sie, no I/we/you), and it \
never says whether the candidate has or lacks anything.
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
    # ADR-084 embedding point 22 (Form A). Everything above the OUTPUT LANGUAGE
    # line is the posting's derivatives, and this call's output — the cluster
    # labels and `jd_context` — is what the candidate is SHOWN on the gaps page
    # and asked about in the interview (points 25a-c).
    from applire.services.untrusted_text import fence

    return (
        fence(
            "Category C gaps (missing evidence):\n"
            + json.dumps(category_c, ensure_ascii=False)
            + "\n\nCategory B gaps (likely but unstated):\n"
            + json.dumps(category_b, ensure_ascii=False)
            + "\n\nRequired skills from JD:\n"
            + json.dumps(required_skills, ensure_ascii=False)
            + "\n\nNice-to-have skills from JD:\n"
            + json.dumps(nice_to_have_skills, ensure_ascii=False),
            header="GAPS AND JD SKILLS TO CLUSTER",
        )
        + "\n\n"
        f"OUTPUT LANGUAGE: write every cluster's jd_context entirely in {language_name}.\n\n"
        'Group the gaps into semantic clusters. Return a JSON object: {"clusters": [ ... ]}.'
    )
