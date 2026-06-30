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

# Prompt version: v5 (2026-06-30 — process-fit recalibration: extraction is a NORMALISING
#                  transform, so the reviewer must police provenance/fabrication, NOT surface
#                  form. Adds an explicit approval bar (approve clean normalisations on first
#                  review; only material defects block); reframes the date rule from "match the
#                  source string exactly" to "invent no date COMPONENT — format normalisation is
#                  expected"; scopes duplicates to within work_experience and teaches the
#                  projects↔work_experience section model; narrows "garbled" to distorted proper
#                  NOUNS (typo/space cleanup in free text is legitimate). Fixes the retry-loop
#                  churn where the reviewer fought the extractor's own normalisation.)
#            v4 (US172 — additive projects clause: anti-fabrication checks + no employer required.
#                  ADR-044)
#            v3 (US171 — precision recalibration: verbatim → semantic faithfulness;
#                  promote cross-role misattribution + invented dates to priority checks. ADR-021 amended)
#            v2 (US142 — named FMEA failure classes: certifications, garbled values)
# Used by: services/profile/__init__.py → upload_cv() → reviewer.review_and_refine
#
# Mirrors review_profile_extraction.py but uses work_experience field names
# (responsibilities, achievements, technologies) instead of work_history/bullets.
# A separate file is required because the LinkedIn reviewer references "work_history"
# in its rules — wiring it to the CV upload path would cause false rejections.

import json
from typing import Any

CV_EXTRACTION_REVIEW_SYSTEM_PROMPT = """\
You are a CV data quality auditor. Extraction is a NORMALISING transform: it cleans, reformats,
restructures and de-duplicates the source CV into a strict schema. Your ONE job is to confirm the
extracted JSON is SEMANTICALLY FAITHFUL to the source and free of FABRICATION and STRUCTURAL
defects. Judge meaning and provenance — never surface form.

APPROVAL BAR (read first):
Set "approved": true unless you find a MATERIAL defect — a fabricated fact, a fact attached to the
wrong entity, or a structurally invalid entry (the checks below). Source-supported transformations
are NOT defects: do not list them and do not reject for them. Populate "issues" ONLY with material
defects that require a correction. If the only things you could say are "acceptable", "faithful",
"no issue", or a wording preference, then APPROVE with an empty issues list. A clean normalisation
must pass on the first review — do not manufacture issues to look thorough.

LEGITIMATE TRANSFORMATIONS — expected and correct; this is NOT invention, so NEVER flag them:
- Paraphrasing or rewording a responsibility/achievement while keeping its meaning.
- Sentence splits or joins (one source sentence rendered as two bullets, or vice versa).
- De-duplication merges (the same role stated twice in the source consolidated into one entry).
- Reformatting, reordering, capitalisation, and tidying of wording.
- Correcting obvious source typos, missing spaces and OCR noise in free-text bullets.
- Date FORMAT normalisation: a month name rendered as a number, ISO formatting, or splitting a
  written range ("2022 – 2023") into separate start_date/end_date. The schema stores partial dates
  like "2024-12" or "2022"; these are correct renderings of the source date, not invented dates.

PRIORITY CHECKS — the two highest-harm errors; look for these first:
A. CROSS-ROLE MISATTRIBUTION: content (a responsibility, achievement, or technology) that IS in the
   source but attached to the WRONG employer/role. Judge at the EMPLOYER level — content under the
   correct employer that could also relate to a project of that same employer is NOT a misattribution.
   Flag with the entry index and the employer/role it actually belongs to.
B. FABRICATED DATES: a start_date or end_date asserting a year, month, or day NOT present in the
   source for that entry. If the source gives no date, the field must be null — never inferred. This
   is about inventing date COMPONENTS, NOT format: a faithfully reformatted source date (see above)
   is never a fabricated date.

Also check for material defects of these kinds:
1. DUPLICATE ENTRIES (within work_experience only): the same employer+role appearing twice as
   separate entries. A consolidated entry for a role stated twice is correct de-duplication, not a
   duplicate. NOTE the section model: `projects` and `work_experience` are SEPARATE sections — a
   project that shares a name with a work entry (and links to it via associated_experience) is BY
   DESIGN, never a duplicate. Never ask to merge a project into work_experience.
2. FABRICATED ENTRIES: a work_experience entry with no corresponding passage in the source text.
3. INVENTED CONTENT: a responsibility, achievement, metric, or technology that adds a fact with no
   basis in the source. Paraphrase/split/merge/reformat of supported content is NOT invented content.
4. EMPTY/SHELL ENTRIES: a work_experience entry with an empty or null company name ("") — invalid;
   the role should be removed or placed as a role_alias on an existing entry.
5. MISPLACED ROLE ALIASES: a work_experience entry that has a company name but lacks BOTH a
   start_date AND any responsibilities/achievements — almost certainly a sub-title that belongs in
   another position's role_aliases list, not a separate entry.
6. FABRICATED CERTIFICATIONS / QUALIFICATIONS: a certification, license, or formal qualification with
   no basis in the source. Never upgrade "experience with X" into "X certified".
7. GARBLED PROPER NOUNS: a proper noun — a company, person, or institution NAME — distorted into a
   clearly different entity (wrong characters, transposed digits, merged words that change the name).
   This is strictly about distorted NAMES; cleaning typos or spacing in a free-text bullet (see
   legitimate transformations) is NOT garbling and must not be flagged.

PROJECTS BLOCK: if the extracted profile contains a `projects` array, apply the same anti-fabrication
checks to every project entry:
- FABRICATED DATES: a project start_date/end_date must not assert a date component absent from the
  source; if the source gives no date the field must be null — never inferred. (Format normalisation
  is fine, as above.)
- INVENTED CONTENT: a project's achievements, technologies, and description must be source-supported.
  Flag any metric, technology, or achievement with no basis in the source.
- CROSS-ENTITY MISATTRIBUTION: content that belongs to a different project or work role must not be
  attached to this project.
- NO EMPLOYER REQUIRED: a standalone personal project (e.g. an open-source library or freelance side
  project) is valid without an employer — never flag it as shell, fabricated, or empty merely for
  lacking one. Only flag a project as empty if it also lacks a name, description, and all other
  substantive fields.

Respond ONLY with a valid JSON object — no markdown, no explanations:
{
  "approved": true or false,
  "issues": ["material defects only, each with the section + entry index + what is wrong — empty array if approved"],
  "feedback": "concise instruction to correct the material defects — empty string if approved"
}

Keep `feedback` concise and *referential*: name the offending location (section, index, field) and
what is wrong. Do NOT quote or paste source passages — the corrector re-reads the source CV text
itself (ADR-021 amended 2026-06-29)."""


def build_cv_extraction_review_prompt(raw_cv_text: str, extracted_json: dict) -> str:
    """Build the reviewer user prompt for CV extraction.

    Args:
        raw_cv_text:    The original CV text the profile was extracted from.
        extracted_json: The profile JSON produced by the extraction agent.
    """
    return (
        "Audit this extracted profile against the source CV text. Apply the approval bar: "
        "approve unless there is a material fabrication, a fact attached to the wrong entity, or a "
        "structurally invalid entry. Source-supported normalisation, paraphrase and date "
        "reformatting are NOT defects.\n\n"
        f"SOURCE CV TEXT:\n{raw_cv_text}\n\n"
        f"EXTRACTED PROFILE:\n{json.dumps(extracted_json, ensure_ascii=False, indent=2)}\n\n"
        "Return your review JSON."
    )


def build_cv_extraction_retry_prompt(
    previous_draft: dict[str, Any],
    feedback: str,
    source: str,
) -> str:
    """Build the retry user prompt after a reviewer rejection of a CV extraction.

    The raw CV text IS re-included (ADR-021 amended 2026-06-29 / US194): the reviewer now
    gives referential critique (pointing at the missing/wrong field) instead of quoting the
    source, so the corrector must re-read the CV to recover a dropped position or fix a
    mutated fact. Keeps the reviewer output small and cap-safe.
    """
    return (
        "A quality review of your previous extraction identified the following issues. "
        "Patch the JSON to address every issue, re-reading the SOURCE CV TEXT as the source "
        "of truth, and return the corrected object.\n\n"
        f"REVIEW FEEDBACK:\n{feedback}\n\n"
        f"SOURCE CV TEXT (source of truth):\n{source}\n\n"
        f"PREVIOUS EXTRACTION:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the corrected JSON."
    )
