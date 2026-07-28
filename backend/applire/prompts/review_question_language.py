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
# Used by: services/interview_graph.py → _review_question_language()
#          wrapped with services/reviewer.review_and_refine (ADR-021, ADR-038)
#          review_and_refine calls reviewer_prompt_fn(source, draft) positionally,
#          so `source` binds to `required_language` (e.g. "English", "German").

import json

from applire.prompts.review_severity import review_output_schema

QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT = """\
You are a language reviewer for AI-generated conversational interview questions.
Your sole responsibility is to verify that a drafted question and every answer choice \
are written entirely in the required language.
Judge ONLY language — not quality, tone, or correctness of content.

WHAT IS BLOCKING IN THIS PASS: any text still in the wrong language. In this pass every
genuine language mismatch is blocking — that is the one thing you are here to catch. Use
"minor" for a preference BETWEEN TWO CORRECT-LANGUAGE WORDINGS, and for nothing else.

""" + review_output_schema(
    issue_hint="specific language problem — empty array if nothing found",
    feedback_hint="one concise instruction naming the required language and what to fix — empty string if there is nothing blocking",
) + """
"""

QUESTION_LANGUAGE_REFINEMENT_PROMPT = """\
You rewrite a conversational interview question into a required language.
You receive (1) a previous draft JSON {"question", "choices"} and (2) reviewer feedback \
naming the required language.
Rewrite the question and every choice into that language, preserving meaning, intent, \
and structure exactly.
Do not add, remove, or reorder choices.
Output ONLY the corrected JSON in the same schema {"question": str, "choices": list[str] | null} \
— no markdown, no commentary.
"""


def build_question_language_review_prompt(required_language: str, draft: dict) -> str:
    """Return a user-turn prompt asking the LLM to check language compliance.

    ``draft`` shape: ``{"question": str, "choices": list[str] | None}``
    """
    choices = draft.get("choices") or []
    return (
        f"Required language: {required_language}\n\n"
        f"Question: {draft.get('question', '')}\n"
        f"Choices: {json.dumps(choices, ensure_ascii=False)}\n\n"
        f"Are the question and every choice written entirely in {required_language}? "
        "Respond with JSON only."
    )


def build_question_language_refinement_prompt(
    previous_draft: dict, feedback: str, source: str = ""
) -> str:
    """Return a user-turn prompt asking the LLM to rewrite ``previous_draft`` per reviewer feedback.

    ``previous_draft`` shape: ``{"question": str, "choices": list[str] | None}``
    ``source`` is the target language name (passed positionally by review_and_refine since
    the ADR-021 amendment).
    """
    target = f" ({source})" if source else ""
    return (
        f"Reviewer feedback: {feedback}\n\n"
        f"Previous draft:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        f"Rewrite into the required language{target}. Output the corrected JSON only."
    )
