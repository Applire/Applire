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

# Prompt version: v2
# Used by: services/interview_graph.py → _review_question_language()
#          wrapped with services/reviewer.review_and_refine (ADR-021, ADR-038)
#          review_and_refine calls reviewer_prompt_fn(source, draft) positionally,
#          so `source` binds to `required_language` (e.g. "English", "German").
#
# v3 changes vs v2 (#451 / ADR-064 amended 2026-08-06):
#   - Choices also carry "denied_terms": a list of concept names the choice
#     explicitly denies (services/choice_grounding.py reads it as a fact,
#     exactly like "level"). Concept names are NOT translatable content —
#     both prompts preserve the list verbatim, the review prompt never flags
#     it as a language mismatch, and the refinement schema names the field so
#     the rewrite cannot silently strip it (the exact drift that cost "level"
#     its F1 bug; interview_graph._carry_levels_by_index is the deterministic
#     backstop for both fields).
#
# v2 changes vs v1 (ADR-062 fix, 2026-07-29):
#   - "choices" is now a list of {"text": str, "level": str} objects, not bare
#     strings (services/choice_grounding.py reads "level" as a fact instead of
#     guessing it from wording — see prompts/interview.py's changelog). Both
#     prompts here now say explicitly: translate only "text"; "level" is a
#     fixed English enum (direct/partial/denial), not translatable content,
#     and must be preserved byte-for-byte. The review prompt also tells the
#     reviewer not to flag "level" as a language mismatch.

import json

from applire.prompts.review_severity import review_output_schema

QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT = """\
You are a language reviewer for AI-generated conversational interview questions.
Your sole responsibility is to verify that a drafted question and every answer choice's "text" \
are written entirely in the required language.
Judge ONLY language — not quality, tone, or correctness of content.

Each choice may carry a "level" field ("direct", "partial", or "denial") and a "denied_terms" \
list alongside its "text". "level" is a FIXED ENGLISH ENUM and "denied_terms" holds verbatim \
concept names — neither is translatable content; never flag either as a language mismatch and \
never suggest changing them. Judge only each choice's "text".

WHAT IS BLOCKING IN THIS PASS: any "text" still in the wrong language. In this pass every
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
Rewrite the question and every choice's "text" into that language, preserving meaning, intent, \
and structure exactly.
Do not add, remove, or reorder choices. Each choice's "level" field ("direct", "partial", or \
"denial") is a fixed English enum and its "denied_terms" list holds verbatim concept names — \
neither is translatable content. Copy BOTH through UNCHANGED, byte-for-byte, for every choice. \
Never translate, omit, or invent a "level" or a "denied_terms" entry.
Output ONLY the corrected JSON in the same schema \
{"question": str, "choices": [{"text": str, "level": str, "denied_terms": [str]}] | null} — no \
markdown, no commentary.
"""


def build_question_language_review_prompt(required_language: str, draft: dict) -> str:
    """Return a user-turn prompt asking the LLM to check language compliance.

    ``draft`` shape: ``{"question": str, "choices": list[dict | str] | None}`` —
    each choice is ``{"text": str, "level": str}`` (new shape) or a bare
    string (backward-compatible fallback); dumped as-is so the reviewer sees
    exactly what will be rewritten, with "level" called out as non-content.
    """
    choices = draft.get("choices") or []
    return (
        f"Required language: {required_language}\n\n"
        f"Question: {draft.get('question', '')}\n"
        f"Choices: {json.dumps(choices, ensure_ascii=False)}\n\n"
        f"Are the question and every choice's \"text\" written entirely in {required_language}? "
        "Ignore any \"level\" or \"denied_terms\" field — fixed metadata, not translatable content. "
        "Respond with JSON only."
    )


def build_question_language_refinement_prompt(
    previous_draft: dict, feedback: str, source: str = ""
) -> str:
    """Return a user-turn prompt asking the LLM to rewrite ``previous_draft`` per reviewer feedback.

    ``previous_draft`` shape: ``{"question": str, "choices": list[dict | str] | None}`` —
    each choice is ``{"text": str, "level": str}`` (new shape) or a bare string.
    ``source`` is the target language name (passed positionally by review_and_refine since
    the ADR-021 amendment).
    """
    target = f" ({source})" if source else ""
    return (
        f"Reviewer feedback: {feedback}\n\n"
        f"Previous draft:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        f"Rewrite into the required language{target}. Preserve every choice's \"level\" and "
        "\"denied_terms\" fields unchanged. Output the corrected JSON only."
    )
