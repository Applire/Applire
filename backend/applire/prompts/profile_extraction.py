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

# Prompt version: v6 (#228 — FIELD-level parity, one layer deeper than v5: personal_info
#   gains address/nationality/date_of_birth/xing_url/website_url and "linkedin" is renamed to
#   "linkedin_url" (same value, name now identical to cv_extraction.py's — #228 instruction 1);
#   work_history gains location/role_aliases/industry_context/team_size/budget_managed (+ rules
#   10-12 below); education gains grade/thesis_title/relevant_coursework; skills becomes
#   list[object] (name/category/proficiency/years_experience/last_used, + rule 13) instead of
#   list[str], which had offered NONE of Skill's fields, not merely some. Triage: Category A
#   (applire-prompt-first) — every one of these fields was structurally absent from this door's
#   schema while cv_extraction.py / cv_extraction_segmented.py already carried it, so this was
#   never a model failure. Measured 2026-09-01 with a section-scoped (not flat) field diff: 19
#   fields missing here, one more than the issue's hand-collected 17 — work_history.location had
#   been undercounted because a flat diff credited it against personal_info/contact.location, a
#   different field on a different model that merely shares the name. Fourth occurrence of the
#   same class on this file (#190 certifications, #229 responsibilities/achievements/technologies,
#   #619 projects/publications/volunteer_activities) — see test_extraction_prompts_section_parity.py,
#   which now gates at field level, not just section level.)
# Prompt version: v5 (#619 — projects/publications/volunteer_activities sections + PROJECTS
#   no-folding rule (9) added. Triage: Category A (applire-prompt-first) — these fields were
#   structurally absent from this door's schema while cv_extraction.py / cv_extraction_segmented.py
#   already carried them (measured on d86be417: 19 cv_extraction.py hits, 7 segmented hits, 0 here),
#   so this was never a model failure. Third occurrence of the same class on this file (#190
#   certifications, #229 responsibilities/achievements/technologies) — see
#   test_extraction_prompts_section_parity.py for the standing regression guard.)
# Prompt version: v4 (#548 — EDUCATION FIELD rule (8) added; 2026-08-14 edge model comparison
#   evidence on the sibling cv_extraction.py schema: two models (qwen3.7-max, gpt-5.6-luna)
#   extracted degree="Industriemeister Metall" AND field="Metall" for the same source line — no
#   rule told the model "field" means "not already in degree". This text-import prompt shares
#   the same ambiguous schema description; fixed for consistency (ADR-066). NEEDS
#   REAL-PROVIDER RUN EVIDENCE per ADR-062 clause 7 — not yet run.)
# Prompt version: v3 (#229 — work_history split into responsibilities/achievements/technologies)
# Used by: services/profile.py → LLMProvider.aparse_json + reviewer.review_and_refine
# Changes from v1: hardened SYSTEM_PROMPT with 4 strict extraction rules;
#                  build_user_prompt adds grounding reminder;
#                  added build_retry_prompt for review layer retries.
# Added in retry-refinement work: PROFILE_EXTRACTION_REFINEMENT_PROMPT — refinement-mode
#                  system prompt used on review-loop retries (patch previous draft, no
#                  raw source re-read).

import json
from typing import Any

SYSTEM_PROMPT = """\
You are an expert CV analyst specialised in the DACH (Germany, Austria, Switzerland) job market.
Your task is to extract structured profile information from raw CV or LinkedIn data and return it as JSON.
Respond ONLY with a valid JSON object matching the schema below — no markdown, no explanations.

STRICT EXTRACTION RULES — follow these before writing any output:
1. Each employer position must appear EXACTLY ONCE in work_history. If the source mentions the same
   role under multiple headings or in multiple formats, merge them into a single entry.
2. Extract ONLY information explicitly present in the source text. Do not infer, complete, or expand
   missing information. If a date, email, or phone is absent from the source, output null.
3. Bullets must be copied or closely paraphrased from what is explicitly stated in the source text.
   Do not add responsibilities or achievements that are not present in the source.
4. Before writing work_history, count the distinct positions in the source. Your output must contain
   exactly that many entries — no more, no fewer.
5. CERTIFICATIONS TAKE PRECEDENCE: any item listed under a "Certifications", "Zertifikate",
   "Zertifizierungen", "Licenses" or "Qualifikationen" heading (common in LinkedIn exports) is a
   certification — populate the "certifications" section with it (at minimum its "name", plus
   "issuing_organization" when stated). This holds even when the item names a framework, standard or
   methodology (ITIL Foundation, ISO 9001 Lead Auditor, CPSA / iSAQB, Certified Scrum Master, GxP / CSV
   "Expert for Computersystemvalidation"): a named certificate is FACTUAL credential data and MUST land
   in "certifications", never be demoted to a "skills" entry or dropped. You MAY additionally record the
   underlying competency as a skill, but the certification entry itself is mandatory.
6. RESPONSIBILITIES vs ACHIEVEMENTS: every role bullet in the source must be routed into one of the
   two lists — do not put them all in "responsibilities". "responsibilities" holds ongoing duties and
   the standing scope of the role (what the person was accountable for). "achievements" holds
   OUTCOMES: any bullet carrying a number, a before/after delta, a percentage, a volume, a benchmark,
   a record, a "first", or a stated business result (e.g. "cut p95 latency from 1.8s to 240ms",
   "reduced manual effort by an estimated 70%", "first go-live across 3 sites in 7 months",
   "processing ~200k invoices/month"). When one bullet states both a duty and its measured outcome,
   it is an achievement. An empty "achievements" list is correct ONLY when the source genuinely
   states no outcome for that role — it is not the safe default. Never invent a metric that the
   source does not state, and never move an unquantified duty into "achievements" to fill it.
7. TECHNOLOGIES vs PRACTICES: a "technologies" list holds ONLY concrete tools, programming languages,
   frameworks, libraries, platforms and products (e.g. Python, Django, React, Docker, AWS, SAP,
   PostgreSQL). Practices, standards, methodologies and processes are NOT technologies — do NOT put
   them in any technologies list (e.g. Agile, Scrum, Kanban, ISO 9001, V-Model, GxP, code review,
   test-driven development, incident management). When such a named standard or methodology is a
   genuine competency, route it to "skills" instead, or omit it. Populate "technologies" per role
   with the stack that role actually names, so a tailored CV can say which stack was used where.
8. EDUCATION FIELD (#548): "field" names a specialisation the "degree" string does NOT already
   state — never repeat a word from "degree" in "field". Many German titles already carry the
   specialisation as part of the title itself (e.g. "Industriemeister Metall", "Fachinformatiker
   Systemintegration", "Technischer Fachwirt") — for these, put the whole title in "degree" and
   leave "field" null/empty. Only populate "field" for a degree whose title is generic on its own
   (e.g. "Bachelor of Science" + field "Informatik", "Diplom" + field "Betriebswirtschaftslehre").
9. PROJECTS (#619): Extract items from a CV's "Projects" section as entries in "projects", NOT
   folded into work_history. A project done within a job or volunteer role should set
   "associated_experience" to the company/organisation name; standalone projects set it to null.
   If project dates are absent from the source, set start_date and end_date to null — NEVER infer
   them. SINGLE HOME: each accomplishment lives in exactly one place — if the same accomplishment
   appears both as a work_history bullet and in the Projects section, keep it as the project (set
   "associated_experience") and do NOT also duplicate it as a work_history responsibility/achievement.
10. ROLE ALIASES: If a position is described under multiple titles within the same employer and
    overlapping time period, create exactly ONE work_history entry using the most senior/formal
    title as "role", and list all other titles in "role_aliases". Never create a separate entry
    per title.
11. QUANTIFIED ROLE FACTS: team_size, budget_managed and industry_context are DERIVED PROJECTIONS
    of a figure the source ALSO states in prose — never the only place that figure lives. When you
    populate one of these fields for a work_history entry, the responsibility or achievement bullet
    that states the underlying figure MUST keep it verbatim (e.g. keep "Budgetverantwortung ca. 6
    Mio. EUR (Personal, Instandhaltung, Material-Gemeinkosten)" as the bullet text) — never shorten
    it to a bare label such as "Budgetverantwortung" merely because the number is also captured
    structurally in the typed field.
12. TEAM_SIZE SEMANTICS (#562): "team_size" counts ONLY the people the candidate PERSONALLY led or
    managed in THAT role (direct reports, or a team/shift they were responsible for) — never
    another quantity that happens to sit near a headcount word in the same sentence. It is NOT the
    employer's total headcount, a facility's capacity (beds, seats, machines), mentees/trainees
    coached WITHOUT line/disciplinary responsibility, or any other people-count that is not the
    candidate's own led team. When the source states only such a figure, leave "team_size" null for
    that entry — the figure still belongs in the bullet text, just not in this typed field.
    Examples: "der GmbH mit 480 Mitarbeitenden" (employer headcount) → null; "a 28-bed ward"
    (facility capacity) → null; "Mentor two mid-level engineers" (mentees, no line responsibility)
    → null; "mit 38 Mitarbeitenden im Dreischichtbetrieb" (people the candidate led) → 38.
13. PROFICIENCY SCALE: every skill "proficiency" MUST be exactly one of basic | intermediate |
    advanced | expert. Map a graphical or numeric competency scale deterministically by the filled
    fraction of its maximum, so equal scale positions always yield the same proficiency level:
    full marks (5/5 dots, 10/10, ●●●●●) → "expert"; ~80% (4/5 dots, 8/10, ●●●●○) → "advanced";
    ~50-60% (3/5 dots, 6/10, ●●●○○) → "intermediate"; ≤40% (1-2/5 dots, ≤4/10) → "basic". Word
    scales map the same way: beginner / novice / elementary → basic; professional working →
    intermediate; proficient / fluent / senior / full professional → advanced; native / expert /
    master → expert. German self-declaration words map the same way — do NOT guess your own
    English-scale equivalent for them: Anwender / Grundkenntnisse / Grundlagen → basic;
    Fortgeschritten / Erfahren / Verhandlungssicher / Fließend / Fliessend → advanced;
    Muttersprache → expert. This applies whenever such a word is the candidate's OWN declared level
    for a skill, in ANY position — a dedicated scale, a suffix after a dash, or a bare parenthetical
    right after the skill name. Two skills shown at the same scale position MUST receive the same
    proficiency level. Where the source gives an explicit scale, this mapping takes precedence over
    any other weighting.

Schema:
{
  "professional_summary": {
    "de": "German-language professional summary or null",
    "en": "English-language professional summary or null"
  },
  "work_history": [
    {
      "company": "string — employer name",
      "role": "string — job title",
      "role_aliases": ["Any additional titles used for this position — see ROLE ALIASES rule"],
      "location": "string or null — office city/region",
      "start_date": "string — e.g. '2020-01' or '2020'",
      "end_date": "string or null — null means current position",
      "is_current": "boolean — true when the source marks the role as ongoing ('present', 'heute', 'seit ...'); keep end_date null for such roles",
      "responsibilities": ["Day-to-day duties and standing scope of the role"],
      "achievements": ["Outcomes, with the metric/benchmark exactly as stated in the source"],
      "technologies": ["Concrete tools, languages, frameworks, platforms used in THIS role"],
      "industry_context": "string or null — industry or domain context, see QUANTIFIED ROLE FACTS rule",
      "team_size": "integer or null — see TEAM_SIZE SEMANTICS rule",
      "budget_managed": "string or null — budget amount as stated, see QUANTIFIED ROLE FACTS rule"
    }
  ],
  "skills": [
    {
      "name": "Skill name",
      "category": "technical | soft | language | domain",
      "proficiency": "basic | intermediate | advanced | expert — see PROFICIENCY SCALE rule",
      "years_experience": "Integer years or null",
      "last_used": "ISO date YYYY-MM-DD or null"
    }
  ],
  "education": [
    {
      "institution": "string — university or school name",
      "degree": "string — e.g. 'Bachelor of Science', 'Ausbildung', 'Industriemeister Metall'",
      "field": "string — field of study, ONLY if it names something 'degree' does not already say",
      "start_date": "string — e.g. '2015'",
      "end_date": "string or null",
      "grade": "string or null — final grade or GPA as stated",
      "thesis_title": "string or null — thesis or dissertation title",
      "relevant_coursework": ["Relevant courses if listed"]
    }
  ],
  "languages": [
    {
      "language": "string — e.g. 'German', 'English'",
      "level": "string — e.g. 'Native', 'C1', 'B2', 'Fluent'"
    }
  ],
  "certifications": [
    {
      "name": "Certification name",
      "issuing_organization": "Issuing body or null",
      "date_obtained": "ISO date YYYY-MM-DD or null",
      "expiry_date": "ISO date YYYY-MM-DD or null",
      "credential_id": "Credential ID or null",
      "credential_url": "Verification URL or null"
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
  ],
  "contact": {
    "name": "string — full name",
    "email": "string or null",
    "phone": "string or null",
    "location": "string or null — city/region",
    "address": "string or null — street address",
    "nationality": "string or null",
    "date_of_birth": "ISO date YYYY-MM-DD or null",
    "linkedin_url": "string or null — LinkedIn profile URL",
    "xing_url": "string or null — XING profile URL",
    "website_url": "string or null — personal website URL"
  }
}"""


def build_user_prompt(raw_text: str) -> str:
    return (
        "Extract the structured profile from the following CV / LinkedIn data.\n"
        "Remember: each position exactly once, only facts present in the source, "
        "null for anything missing. Split each role's bullets into responsibilities "
        "(duties) and achievements (outcomes with a metric or benchmark), and list "
        "that role's concrete tools under technologies.\n\n"
        + raw_text
    )


def build_retry_prompt(previous_draft: dict[str, Any], feedback: str, source: str) -> str:
    """Build the retry user prompt after a reviewer rejection.

    The raw source CV/LinkedIn text IS re-included (ADR-021 amended 2026-06-29 / US194):
    the reviewer now gives *referential* critique (pointing at the missing/wrong field)
    rather than quoting the source, so the corrector must re-read the source to recover
    a dropped position or fix a mutated fact. This keeps the reviewer output small.

    Args:
        previous_draft: The extraction the reviewer rejected.
        feedback: The reviewer's referential critique.
        source: The raw CV/LinkedIn text — the source of truth for the correction.
    """
    return (
        "A quality review of your previous extraction identified the following issues. "
        "Patch the JSON to address every issue, re-reading the SOURCE TEXT as the source "
        "of truth, and return the corrected object.\n\n"
        f"REVIEW FEEDBACK:\n{feedback}\n\n"
        f"SOURCE TEXT (source of truth):\n{source}\n\n"
        f"PREVIOUS EXTRACTION:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the corrected JSON."
    )


PROFILE_EXTRACTION_REFINEMENT_PROMPT = """\
You are a profile data corrector. You receive (1) a previously-extracted profile JSON
(from a CV or LinkedIn export) and (2) a quality reviewer's critique listing specific
issues. Patch the JSON to address every issue and return the corrected object.

Rules:
- The previous extraction is your working draft. Modify it to resolve the reviewer's issues.
- Do not invent new content. The SOURCE TEXT is provided — re-read it to recover a dropped
  position or correct a mutated fact. Restrict your changes to additions, deletions,
  nullifications, and moves grounded in that source text.
- Preserve all fields that the reviewer did not flag.
- Output ONLY the corrected JSON object in the same schema as the input — no markdown,
  no commentary.
- Each employer position must appear exactly once in work_history. Use null for missing
  optional fields.
"""
