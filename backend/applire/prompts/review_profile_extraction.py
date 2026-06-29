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
# Used by: services/profile.py → reviewer.review_and_refine

import json

REVIEW_SYSTEM_PROMPT = """\
You are a strict CV data quality auditor. Your task is to verify that an extracted
profile JSON faithfully represents the source CV text — nothing more, nothing less.

Check for ALL of the following:
1. DUPLICATE ENTRIES: Each employer and role must appear exactly once in work_history.
   Flag any entry that is a duplicate or variant of another entry (same company/role,
   different or missing dates).
2. FABRICATED ENTRIES: Every work_history entry must have a clear corresponding passage
   in the source text. Flag any entry with no basis in the source.
3. INVENTED DATES: start_date and end_date must match exactly what is stated in the source.
   If a date is absent from the source, the field must be null — never inferred or invented.
4. INVENTED BULLETS: Bullets must reflect what is explicitly stated in the source text.
   Flag any bullet that adds responsibilities, achievements, or skills not present in the source.

Respond ONLY with a valid JSON object — no markdown, no explanations:
{
  "approved": true or false,
  "issues": ["list of specific issues with work_history index and description — empty array if approved"],
  "feedback": "concise instruction for the extractor to correct all issues — empty string if approved"
}

Keep `feedback` concise and *referential*: name the offending location (work_experience index,
field, section) and state what is wrong. Do NOT quote or paste source passages — the corrector
re-reads the source text itself (ADR-021 amended 2026-06-29)."""


def build_review_prompt(raw_cv_text: str, extracted_json: dict) -> str:
    """Build the reviewer user prompt for profile extraction.

    Args:
        raw_cv_text: The original CV text the profile was extracted from.
        extracted_json: The profile JSON produced by the extraction agent.
    """
    return (
        "Review this extracted profile against the source CV text.\n\n"
        f"SOURCE CV TEXT:\n{raw_cv_text}\n\n"
        f"EXTRACTED PROFILE:\n{json.dumps(extracted_json, ensure_ascii=False, indent=2)}\n\n"
        "Does the extracted profile faithfully and completely represent the source — "
        "no duplicates, no fabrications, no invented dates? Return your review JSON."
    )
