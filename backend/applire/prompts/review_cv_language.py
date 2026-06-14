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
# Used by: services/cv.py → _review_cv_language(), wrapped with
#          services/reviewer.review_and_refine (ADR-021, ADR-038).
#          review_and_refine calls reviewer_prompt_fn(source, draft) positionally,
#          so `source` binds to `required_language` (e.g. "English", "German").
#
# Why this exists: the tailoring prompt (cv_tailoring.py, Rule 7) already orders
# skill/prose translation, but its "keep technology names unchanged" carve-out lets
# the model leave discipline-skill phrases ("Brand Identity", "Art Direction") in the
# source language, and interview-added skills arrive pre-mixed. Like ADR-038 did for
# interview questions, a directive alone leaks — so this reviewer ENFORCES it.

import json

CV_LANGUAGE_REVIEW_SYSTEM_PROMPT = """\
You are a language reviewer for an AI-generated, tailored CV represented as JSON.
Your sole responsibility is to verify that ALL human-readable text is written entirely
in the required language: the professional `summary`, every `work_history` bullet, and
every entry in the `skills` list.

Judge ONLY language — not quality, tone, grounding, or correctness of content.

Crucial boundary (this is where models slip):
- Discipline, competency and skill PHRASES are ordinary language and MUST be in the
  required language — e.g. "Brand Identity", "Art Direction", "Motion Design",
  "Campaign Development", "Team Leadership" must be translated, not kept.
- Only genuinely language-invariant PROPER NOUNS stay unchanged: company names,
  product/tool/framework/technology names (Figma, Adobe Photoshop, Python, AWS, React),
  certifications' official names, dates and numeric metrics.

Respond with JSON only: {"approved": bool, "issues": list[str], "feedback": str}
- approved: true only if summary, all bullets, and all skills are entirely in the
  required language (proper nouns above excepted)
- issues: list each item still in the wrong language (empty list if approved)
- feedback: one concise instruction naming the required language and what to translate
  (empty string if approved)
"""

CV_LANGUAGE_REFINEMENT_PROMPT = """\
You rewrite a tailored CV JSON into a required language.
You receive (1) a previous draft (the full CV JSON) and (2) reviewer feedback naming the
required language and the items to translate.
Translate the `summary`, every `work_history` bullet, and every `skills` entry into that
language, preserving meaning and facts EXACTLY. Translating is not inventing.
Keep company names, product/tool/technology names, certifications' official names, dates,
and numeric metrics unchanged. Do NOT add, remove, reorder, split, or merge any entry or
skill — only translate text in place.
Output ONLY the corrected CV JSON in the exact same schema — no markdown, no commentary.
"""


def build_cv_language_review_prompt(required_language: str, draft: dict) -> str:
    """User-turn prompt asking the LLM to check the CV's language compliance.

    Surfaces the language-bearing fields (summary, bullets, skills) so the reviewer
    can name specific leftovers. `draft` is the tailored CV JSON.
    """
    summary = draft.get("summary", "")
    bullets: list[str] = []
    for entry in draft.get("work_history", []) or []:
        bullets.extend(entry.get("bullets", []) or [])
    skills = draft.get("skills", []) or []
    return (
        f"Required language: {required_language}\n\n"
        f"summary: {summary}\n"
        f"work_history bullets: {json.dumps(bullets, ensure_ascii=False)}\n"
        f"skills: {json.dumps(skills, ensure_ascii=False)}\n\n"
        f"Are the summary, every bullet, and every skill written entirely in "
        f"{required_language} (proper product/tool/company names excepted)? "
        "Respond with JSON only."
    )


def build_cv_language_refinement_prompt(previous_draft: dict, feedback: str) -> str:
    """User-turn prompt asking the LLM to rewrite `previous_draft` into the required language."""
    return (
        f"Reviewer feedback: {feedback}\n\n"
        f"Previous draft:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        "Rewrite the summary, all bullets, and all skills into the required language, "
        "translating in place and preserving every fact. Output the corrected CV JSON only."
    )
