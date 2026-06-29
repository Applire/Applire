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
# Used by: services/interview_graph.py → ResponseParser node (Mode C)
#          wrapped with reviewer.review_and_refine
# Added in retry-refinement work: RESPONSE_PARSER_REFINEMENT_PROMPT — refinement-mode
#          system prompt used on review-loop retries (patch the previous extraction,
#          no answer-text re-read).

import json

RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT = """\
You are a quality reviewer for structured profile data extracted from a conversational answer.
Your job is to verify that extracted data faithfully represents what the user actually said.
You must not approve data that was hallucinated or inferred beyond what was stated.
Respond with JSON only: {"approved": bool, "issues": list[str], "feedback": str}
- approved: true only if all extracted fields are accurate and grounded in the answer
- issues: list of specific problems found (empty list if approved)
- feedback: one concise sentence telling the generator how to fix the draft (empty string if approved)

Keep `feedback` concise and *referential*: name what is wrong (field, value) — do NOT quote or
paste the answer text. The corrector re-reads the source answer itself (ADR-021 amended 2026-06-29).
"""


def build_response_parser_review_prompt(
    gap: str,
    question: str,
    answer: str,
    draft: dict,
) -> str:
    return f"""\
Review the following extracted profile data.

Gap being addressed: {gap}
Question asked: {question}
User's answer: {answer}

Extracted draft:
{json.dumps(draft, indent=2, ensure_ascii=False)}

Check all of the following:
1. Are extracted achievements specific and concrete — not paraphrased into vague generalities?
2. Do numeric values (team_size, budget_managed) exactly match what the user stated?
3. Is every field in the draft actually grounded in the user's answer — no hallucination?
4. If the user said they had no budget responsibility, is budget_managed absent or null (not fabricated)?

Respond with JSON only:
{{"approved": bool, "issues": list[str], "feedback": str}}
"""


RESPONSE_PARSER_REFINEMENT_PROMPT = """\
You are an answer parser corrector. You receive (1) a previously-extracted profile fragment
JSON (from a user's interview answer) and (2) a quality reviewer's critique listing specific
issues (hallucinated values, vague paraphrasing, fabricated numerics). Patch the JSON to
address every issue.

Rules:
- The previous extraction is your working draft. Modify it to resolve the reviewer's issues.
- Do not invent new content. If the reviewer's feedback quotes the user's answer, use those
  exact phrases as factual basis. Otherwise restrict changes to deletions or nullifications.
- Output ONLY the corrected JSON in the same schema as the input — no markdown, no commentary.
"""
