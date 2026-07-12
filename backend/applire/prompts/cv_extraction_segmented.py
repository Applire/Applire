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

# Prompt version: v1 (US195 / E036 — segmented CV extraction, ADR-047)
# Used by: services/profile/extract_segmented.py → LLMProvider.aparse_json
#
# Three system prompts, each producing a SMALL bounded slice so no single extraction
# call needs a large output (the cap that truncates a dense CV mid-JSON):
#   EXTRACTION_OUTLINE_SYSTEM_PROMPT  — position headers only (no bullets)
#   EXTRACTION_DETAIL_SYSTEM_PROMPT   — ONE position's responsibilities/achievements/tech
#   EXTRACTION_CORE_SYSTEM_PROMPT     — everything except work_experience details
#
# This is the extraction analogue of the segmented CV *generation* (cv_segmented.py /
# US189): outline-then-expand. The orchestrator (extract_segmented.py) assembles the
# slices into the full MasterProfileData schema (cv_extraction.py defines that schema).

import json
from typing import Any

# Shared hygiene rules that apply to any section emitting technologies/skills, lifted
# verbatim-in-spirit from cv_extraction.py so the segmented path extracts identically.
_TECH_VS_PRACTICES = (
    "TECHNOLOGIES vs PRACTICES: a technologies list holds ONLY concrete tools, languages, "
    "frameworks, libraries, platforms and products (Python, React, Docker, AWS, SAP). "
    "Practices/standards/methodologies (Agile, Scrum, ISO 9001, V-Model, GxP, code review, "
    "TDD) are NOT technologies — route a genuine such competency to skills (category "
    '"domain" or "soft") or omit it.'
)

# ---------------------------------------------------------------------------
# 1. Outline — position headers only (no responsibilities/achievements/technologies)
# ---------------------------------------------------------------------------

EXTRACTION_OUTLINE_SYSTEM_PROMPT = """\
You are a CV experience outliner. From the CV text, list ONLY the distinct work
positions as lightweight headers — do NOT extract responsibilities, achievements, or
technologies (a later pass handles those). Respond ONLY with valid JSON, no markdown.

Return:
{
  "work_experience": [
    {
      "company": "Employer name (never empty)",
      "role": "Primary/most senior job title",
      "role_aliases": ["Other titles for the same position, same employer & overlapping time"],
      "location": "Office city/country or null",
      "start_date": "e.g. '2020-01' or '2020' or null",
      "end_date": "e.g. '2023-06' or null for current",
      "is_current": "true when the source marks the role as ongoing ('present', 'heute', 'seit ...'); keep end_date null for such roles; false when it ended; null when unstated",
      "industry_context": "Industry/domain or null",
      "team_size": "Integer team size or null",
      "budget_managed": "Budget amount as string or null"
    }
  ]
}

Rules:
- COUNT CHECK: count the distinct employer positions in the source; output exactly that
  many entries — no shell entries, no duplicates. Sub-roles/titles within one position go
  in role_aliases, never as a new entry.
- Every entry MUST have a non-empty company. Do not invent positions.
- Praktikum/Werkstudent are work positions. Ausbildung is education — exclude it here.
"""


def build_extraction_outline_prompt(raw_text: str) -> str:
    return (
        "List every distinct work position in the following CV as a header (no bullets). "
        "Return the JSON:\n\n" + raw_text
    )


# ---------------------------------------------------------------------------
# 2. Detail — ONE position's responsibilities / achievements / technologies
# ---------------------------------------------------------------------------

EXTRACTION_DETAIL_SYSTEM_PROMPT = """\
You are a CV experience detail extractor. You are given the full CV text and ONE work
position (company + role + dates). Extract ONLY that position's details from the CV text.
Respond ONLY with valid JSON, no markdown.

Return:
{
  "responsibilities": ["Day-to-day duty bullet points for THIS position"],
  "achievements": ["Outcome bullets with metrics where stated, for THIS position"],
  "technologies": ["Concrete tools/languages/frameworks used in THIS position"]
}

Rules:
- Extract only what the source states for THIS position; do not invent, and do not pull in
  another position's bullets. Empty lists are fine if the source gives none.
- Separate duties (responsibilities) from outcomes/metrics (achievements).
- """ + _TECH_VS_PRACTICES + """
- Preserve German umlauts and special characters exactly.
"""


def build_extraction_detail_prompt(raw_text: str, position: dict[str, Any]) -> str:
    header = json.dumps(
        {k: position.get(k) for k in ("company", "role", "start_date", "end_date")},
        ensure_ascii=False,
    )
    return (
        f"POSITION (extract details for this one only):\n{header}\n\n"
        f"FULL CV TEXT:\n{raw_text}\n\n"
        "Return this position's responsibilities, achievements, and technologies as JSON."
    )


# ---------------------------------------------------------------------------
# 3. Core — everything EXCEPT work_experience details
# ---------------------------------------------------------------------------

EXTRACTION_CORE_SYSTEM_PROMPT = """\
You are a CV core profile extractor. Extract every profile section EXCEPT work_experience
(a separate pass handles work positions). Respond ONLY with valid JSON, no markdown.

Return a JSON object with these keys (use [] / null when absent — never omit a key):
{
  "personal_info": {"name","email","phone","location","address","nationality",
                    "date_of_birth","linkedin_url","xing_url","website_url"},
  "professional_summary": {"de": "German summary or null", "en": "English summary or null"},
  "education": [{"institution","degree","field","start_date","end_date","grade",
                 "thesis_title","relevant_coursework"}],
  "certifications": [{"name","issuing_organization","date_obtained","expiry_date",
                      "credential_id","credential_url"}],
  "skills": [{"name","category":"technical|soft|language|domain",
              "proficiency":"basic|intermediate|advanced|expert","years_experience","last_used"}],
  "languages": [{"language","level"}],
  "publications": [{"title","type":"publication|patent","co_authors","venue",
                    "published_date","doi","url","patent_number"}],
  "volunteer_activities": [{"role","organization","location","start_date","end_date",
                            "description","cause","responsibilities","achievements","technologies"}],
  "projects": [{"name","description","role","start_date","end_date","responsibilities",
                "achievements","technologies","url","associated_experience"}]
}

Rules:
- Extract everything stated or clearly implied; do not invent data. Ausbildung → education.
- PROFICIENCY: every skill proficiency MUST be exactly basic|intermediate|advanced|expert.
  Map a numeric/graphical scale by filled fraction (full→expert, ~80%→advanced, ~50-60%→
  intermediate, ≤40%→basic); word scales: beginner→basic, professional working→intermediate,
  proficient/fluent/senior→advanced, native/expert→expert. Equal scale positions → equal level.
- """ + _TECH_VS_PRACTICES + """
- PROJECTS: items from a "Projects" section go here, NOT folded into work. Set
  associated_experience to the company/org name for project-within-a-job, else null. If a
  project's dates are absent from the source, set them null — NEVER infer.
- Preserve German umlauts exactly. Use null for missing optional fields.
"""


def build_extraction_core_prompt(raw_text: str) -> str:
    return (
        "Extract every non-work-experience profile section from the following CV. "
        "Return the JSON:\n\n" + raw_text
    )
