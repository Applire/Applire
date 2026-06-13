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

# Prompt version: v2 (US142 — named FMEA failure classes: certifications, garbled values)
# Used by: services/profile/__init__.py → upload_cv() → reviewer.review_and_refine
#
# Mirrors review_profile_extraction.py but uses work_experience field names
# (responsibilities, achievements, technologies) instead of work_history/bullets.
# A separate file is required because the LinkedIn reviewer references "work_history"
# in its rules — wiring it to the CV upload path would cause false rejections.

import json
from typing import Any

CV_EXTRACTION_REVIEW_SYSTEM_PROMPT = """\
You are a strict CV data quality auditor. Your task is to verify that an extracted
profile JSON faithfully represents the source CV text — nothing more, nothing less.

Check for ALL of the following:
1. DUPLICATE ENTRIES: Each employer and role must appear exactly once in work_experience.
   Flag any entry that is a duplicate or variant of another entry (same company/role,
   different or missing dates).
2. FABRICATED ENTRIES: Every work_experience entry must have a clear corresponding passage
   in the source text. Flag any entry with no basis in the source.
3. INVENTED DATES: start_date and end_date must match exactly what is stated in the source.
   If a date is absent from the source, the field must be null — never inferred or invented.
4. INVENTED CONTENT: responsibilities, achievements, and technologies must reflect what is
   explicitly stated in the source text. Flag any item that adds content not present in the source.
5. EMPTY/SHELL ENTRIES: Flag any work_experience entry with an empty or null company name ("").
   These are invalid — the role should either be removed or placed as a role_alias on an existing entry.
6. MISPLACED ROLE ALIASES: Flag any work_experience entry that has a company name but is missing
   BOTH start_date AND responsibilities/achievements. These are almost certainly role titles mentioned
   within another position and should appear in that position's role_aliases list, not as a separate entry.
7. FABRICATED CERTIFICATIONS / QUALIFICATIONS: Every certification, license, or formal qualification
   in the extracted profile must have a clear corresponding mention in the source text. Flag any
   certification or qualification with no basis in the source — never upgrade "experience with X"
   into "X certified".
8. GARBLED / MIS-TRANSCRIBED VALUES: Beyond wholly invented data, flag values that are garbled or
   mis-transcribed from the source — a company name, role title, date, or certification that is
   present in the source but distorted (wrong characters, merged words, transposed digits). The
   extracted value must faithfully reproduce the source, not approximate it.

Respond ONLY with a valid JSON object — no markdown, no explanations:
{
  "approved": true or false,
  "issues": ["list of specific issues with work_experience index and description — empty array if approved"],
  "feedback": "concise instruction for the extractor to correct all issues — empty string if approved"
}

If your correction requires content not present in the draft, quote the relevant source passages
verbatim in the `feedback` field. The corrector will not re-read the source."""


def build_cv_extraction_review_prompt(raw_cv_text: str, extracted_json: dict) -> str:
    """Build the reviewer user prompt for CV extraction.

    Args:
        raw_cv_text:    The original CV text the profile was extracted from.
        extracted_json: The profile JSON produced by the extraction agent.
    """
    return (
        "Review this extracted profile against the source CV text.\n\n"
        f"SOURCE CV TEXT:\n{raw_cv_text}\n\n"
        f"EXTRACTED PROFILE:\n{json.dumps(extracted_json, ensure_ascii=False, indent=2)}\n\n"
        "Does the extracted profile faithfully and completely represent the source — "
        "no duplicates, no fabrications, no invented dates, no invented content? "
        "Return your review JSON."
    )


def build_cv_extraction_retry_prompt(
    previous_draft: dict[str, Any],
    feedback: str,
) -> str:
    """Build the retry user prompt after a reviewer rejection of a CV extraction.

    The raw CV text is NOT included — the reviewer is expected to quote relevant
    source passages in `feedback` when a correction needs new content.
    """
    return (
        "A quality review of your previous extraction identified the following issues. "
        "Patch the JSON to address every issue and return the corrected object.\n\n"
        f"REVIEW FEEDBACK:\n{feedback}\n\n"
        f"PREVIOUS EXTRACTION:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the corrected JSON."
    )
