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

Schema:
{
  "work_history": [
    {
      "company": "string — employer name",
      "role": "string — job title",
      "start_date": "string — e.g. '2020-01' or '2020'",
      "end_date": "string or null — null means current position",
      "is_current": "boolean — true when the source marks the role as ongoing ('present', 'heute', 'seit ...'); keep end_date null for such roles",
      "responsibilities": ["Day-to-day duties and standing scope of the role"],
      "achievements": ["Outcomes, with the metric/benchmark exactly as stated in the source"],
      "technologies": ["Concrete tools, languages, frameworks, platforms used in THIS role"]
    }
  ],
  "skills": ["list of technical and soft skills"],
  "education": [
    {
      "institution": "string — university or school name",
      "degree": "string — e.g. 'Bachelor of Science', 'Ausbildung', 'Industriemeister Metall'",
      "field": "string — field of study, ONLY if it names something 'degree' does not already say",
      "start_date": "string — e.g. '2015'",
      "end_date": "string or null"
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
  "contact": {
    "name": "string — full name",
    "email": "string or null",
    "phone": "string or null",
    "location": "string or null — city/region",
    "linkedin": "string or null — LinkedIn profile URL or username"
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
