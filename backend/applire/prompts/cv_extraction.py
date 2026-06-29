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

# Prompt version: v2 (US175 proficiency-scale mapping + US176 technologies-vs-practices hygiene)
# Used by: services/profile/__init__.py → upload_cv() → LLMProvider.aparse_json
#          and reviewer.review_and_refine retry path
#
# Three constants (ADR 014):
#   GENERIC_CV_EXTRACTION_PROMPT      — no JD context; general-purpose profile extraction
#   JD_AWARE_CV_EXTRACTION_PROMPT     — injects JobAnalysis context for relevance-weighted extraction
#   CV_EXTRACTION_REFINEMENT_PROMPT   — refinement-mode prompt used on review-loop retries
#                                       (patch the previous draft, no raw source re-read)
#
# All three target the full MasterProfileData schema (iter 11).
# The model_validator on MasterProfileData handles backwards-compat with older field names.

_SCHEMA_DESCRIPTION = """\
{
  "personal_info": {
    "name": "Full name",
    "email": "Email address or null",
    "phone": "Phone number or null",
    "location": "City / region or null",
    "address": "Street address or null",
    "nationality": "Nationality or null",
    "date_of_birth": "ISO date YYYY-MM-DD or null",
    "linkedin_url": "LinkedIn profile URL or null",
    "xing_url": "XING profile URL or null",
    "website_url": "Personal website URL or null"
  },
  "professional_summary": {
    "de": "German-language professional summary or null",
    "en": "English-language professional summary or null"
  },
  "work_experience": [
    {
      "company": "Employer name",
      "role": "Primary job title",
      "role_aliases": ["Any additional titles used for this position"],
      "location": "Office city/country or null",
      "start_date": "e.g. '2020-01' or '2020' — partial dates accepted",
      "end_date": "e.g. '2023-06' or null for current position",
      "responsibilities": ["Responsibility bullet points"],
      "achievements": ["Achievement bullet points with metrics where stated"],
      "technologies": ["Tools, languages, frameworks mentioned"],
      "industry_context": "Industry or domain context or null",
      "team_size": "Integer team size or null",
      "budget_managed": "Budget amount as string or null"
    }
  ],
  "education": [
    {
      "institution": "University or school name",
      "degree": "Degree type e.g. 'Bachelor of Science', 'Master', 'Ausbildung'",
      "field": "Field of study or specialisation",
      "start_date": "e.g. '2015' or null",
      "end_date": "e.g. '2018' or null",
      "grade": "Final grade or GPA as string or null",
      "thesis_title": "Thesis or dissertation title or null",
      "relevant_coursework": ["Relevant courses if listed"]
    }
  ],
  "certifications": [
    {
      "name": "Certification name",
      "issuing_organization": "Issuing body",
      "date_obtained": "ISO date YYYY-MM-DD or null",
      "expiry_date": "ISO date YYYY-MM-DD or null",
      "credential_id": "Credential ID or null",
      "credential_url": "Verification URL or null"
    }
  ],
  "skills": [
    {
      "name": "Skill name",
      "category": "technical | soft | language | domain",
      "proficiency": "basic | intermediate | advanced | expert",
      "years_experience": "Integer years or null",
      "last_used": "ISO date YYYY-MM-DD or null"
    }
  ],
  "languages": [
    {
      "language": "Language name e.g. 'German', 'English'",
      "level": "Proficiency e.g. 'Native', 'C1', 'B2', 'Fluent', 'Business fluent' or null if not stated"
    }
  ],
  "publications": [
    {
      "title": "Publication or patent title",
      "type": "publication | patent",
      "co_authors": ["Co-author names"],
      "venue": "Journal, conference, or patent office or null",
      "published_date": "ISO date YYYY-MM-DD or null",
      "doi": "DOI string or null",
      "url": "URL or null",
      "patent_number": "Patent number or null"
    }
  ],
  "volunteer_activities": [
    {
      "role": "Volunteer role title",
      "organization": "Organisation name",
      "location": "City/country or null",
      "start_date": "e.g. '2022-03' or null",
      "end_date": "e.g. '2022-09' or null",
      "description": "Activity description or null",
      "cause": "Cause area e.g. 'Education', 'Environment' or null",
      "responsibilities": ["Day-to-day duties in this volunteer role"],
      "achievements": ["Measurable outcomes or impacts achieved"],
      "technologies": ["Tools or platforms used in this volunteer role"]
    }
  ],
  "projects": [
    {
      "name": "Project name",
      "description": "Short project description or null",
      "role": "Role on the project e.g. 'Lead Developer', 'Contributor'",
      "start_date": "e.g. '2022-03' or null — NEVER infer dates absent from the source",
      "end_date": "e.g. '2022-09' or null — NEVER infer dates absent from the source",
      "responsibilities": ["What this person did on the project"],
      "achievements": ["Quantified outcomes e.g. 'Reduced load time by 30%'"],
      "technologies": ["Languages, frameworks, tools used"],
      "url": "Project URL, repo, or demo link or null",
      "associated_experience": "Name of the work or volunteer entry this project was done under, or null for standalone projects"
    }
  ]
}"""

_SYSTEM_BASE = """\
You are an expert CV analyst specialised in the DACH (Germany, Austria, Switzerland) job market.
Extract structured profile information from the provided CV text and return it as a single JSON object.
Respond ONLY with valid JSON — no markdown fences, no commentary, no explanations.

Rules:
- Extract everything stated or clearly implied; do not invent data.
- Separate responsibilities (day-to-day duties) from achievements (outcomes with metrics) into their respective lists.
- For technology stacks, extract individual tools/languages/frameworks into the technologies list.
- TECHNOLOGIES vs PRACTICES: a "technologies" list holds ONLY concrete tools, programming
  languages, frameworks, libraries, platforms and products (e.g. Python, React, Docker, AWS, SAP,
  PostgreSQL). Practices, standards, methodologies and processes are NOT technologies — do NOT put
  them in any technologies list (e.g. Agile, Scrum, Kanban, ISO 25010, ISO 9001, V-Model, GxP,
  audit-trail processes, code review, test-driven development). When such a named standard or
  methodology is a genuine competency, route it to skills with category "domain" (or "soft" for a
  pure way-of-working), or omit it. This applies to the technologies list in work_experience,
  projects AND volunteer_activities.
- PROFICIENCY SCALE: every skill "proficiency" MUST be exactly one of basic | intermediate |
  advanced | expert. Map a graphical or numeric competency scale deterministically by the filled
  fraction of its maximum, so equal scale positions always yield the same proficiency level:
    • full marks (5/5 dots, 10/10, ●●●●●) → "expert"
    • ~80% (4/5 dots, 8/10, ●●●●○) → "advanced"
    • ~50-60% (3/5 dots, 6/10, ●●●○○) → "intermediate"
    • ≤40% (1-2/5 dots, ≤4/10) → "basic"
  Word scales map the same way: beginner / novice / elementary → basic; professional working →
  intermediate; proficient / fluent / senior / full professional → advanced; native / expert /
  master → expert. Two skills shown at the same scale position MUST receive the same proficiency
  level. Where a CV gives an explicit scale, this mapping takes precedence over any other weighting.
- Preserve German umlauts and special characters exactly as written.
- Use null for missing optional fields — never omit required fields.
- For DACH CVs: Ausbildung maps to education; Praktikum/Werkstudent map to work_experience entries.
- ROLE ALIASES: If a position is described under multiple titles within the same employer and overlapping
  time period, create exactly ONE work_experience entry using the most senior/formal title as "role",
  and list all other titles in "role_aliases". Never create a separate entry per title.
- VALID ENTRIES ONLY: Every work_experience entry MUST have a non-empty "company" name AND at least one
  of: start_date, responsibilities, or achievements. Never emit an entry with an empty company ("" or null).
  Role titles mentioned within bullet points or as sub-roles belong in role_aliases, not as new entries.
- COUNT CHECK: Before emitting work_experience, count the distinct employer positions in the source.
  Your output must contain exactly that many entries — no shell entries, no duplicates.
- PROJECTS: Extract items from a CV's "Projects" section as entries in `projects`, NOT folded into
  work_experience. A project done within a job or volunteer role should set `associated_experience`
  to the company/organisation name. Standalone projects set `associated_experience` to null.
  If project dates are absent from the source, set start_date and end_date to null — NEVER infer them.
  SINGLE HOME: each accomplishment lives in exactly one place. If the same accomplishment appears both
  as a work_experience bullet and in the Projects section, keep it as the project (set
  `associated_experience`) and do NOT also duplicate it as a work_experience responsibility/achievement.

Output schema:
""" + _SCHEMA_DESCRIPTION

GENERIC_CV_EXTRACTION_PROMPT = _SYSTEM_BASE

JD_AWARE_CV_EXTRACTION_PROMPT = (
    _SYSTEM_BASE
    + """

JD-aware extraction instructions:
You have been provided with a job description analysis below. Use it to:
- Prioritise extracting technologies and skills that are relevant to the target role.
- Write responsibilities and achievements using language that maps naturally to the JD requirements.
- Assign higher proficiency values to skills that directly match the JD's required or preferred skills.
- Do NOT invent experience that is absent from the CV — only re-emphasise what is present.
"""
)


def build_generic_prompt(raw_text: str) -> str:
    """Return the user message string for generic CV extraction.

    Pass to LLMProvider.aparse_json(prompt, system=GENERIC_CV_EXTRACTION_PROMPT).
    """
    return (
        "Extract the structured profile from the following CV text and return the JSON:\n\n"
        + raw_text
    )


def build_jd_aware_prompt(raw_text: str, job_analysis: dict) -> str:
    """Return the user message string for JD-context-aware CV extraction.

    Pass to LLMProvider.aparse_json(prompt, system=JD_AWARE_CV_EXTRACTION_PROMPT).

    *job_analysis* is the serialisable dict from a JobAnalysis DB record.
    The LLM is instructed not to invent data absent from the CV.
    """
    import json

    jd_context = json.dumps(job_analysis, ensure_ascii=False, indent=2)
    return (
        "Job Description Analysis (for context only — do not invent data):\n"
        + jd_context
        + "\n\nExtract the structured profile from the following CV text and return the JSON:\n\n"
        + raw_text
    )


CV_EXTRACTION_REFINEMENT_PROMPT = """\
You are a CV profile corrector. You receive (1) a previously-extracted CV profile JSON and
(2) a quality reviewer's critique listing specific issues. Patch the JSON to address every
issue in the reviewer's feedback, then return the corrected object.

Rules:
- The previous extraction is your working draft. Modify it to resolve the reviewer's issues.
- Do not invent new content. The SOURCE CV TEXT is provided — re-read it to recover a dropped
  position or correct a mutated fact. Restrict your changes to additions, deletions,
  nullifications, and moves grounded in that source text.
- Preserve all fields that the reviewer did not flag.
- Output ONLY the corrected JSON object in the same schema as the input. No markdown
  fences, no commentary, no explanations.
- Preserve German umlauts and special characters exactly.
- Use null for missing optional fields — never omit required fields.
"""
