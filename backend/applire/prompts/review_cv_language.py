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
#
# Amended (Tiramisu wave-6, blind hiring-panel run #6, 2026-07-26): this same
# enforcement pass was ALSO the vector for the opposite defect — over-eager
# "translation" expanded the domain acronym "GxP" (not on its proper-noun allow-
# list) into "Good Practice" inside three skill names. Added a VERBATIM LABELS
# carve-out below (both the reviewer and the refiner) naming the domain-acronym
# class explicitly. Prompt wording is a mitigation only — the real protection is
# the deterministic ``_restore_skill_spelling`` post-pass in services/cv.py, run
# after every LLM/refinement step regardless of what this chain produces.

import json

CV_LANGUAGE_REVIEW_SYSTEM_PROMPT = """\
You are a language reviewer for an AI-generated, tailored CV represented as JSON.
Your sole responsibility is to verify that ALL human-readable text is written entirely
in the required language: the professional `summary`, every `work_history` bullet,
every project bullet (nested under work entries or standalone), and every entry in
the `skills` list.

Judge ONLY language — not quality, tone, grounding, or correctness of content.

One exception rides on your input: a VERIFIED COVERAGE CHECK block may follow the
draft, listing job-description keywords deterministically absent in every known
surface form. These are word-choice issues in your domain: when the draft carries the
listed concept in different wording (a synonym or a translation), reject and instruct
the writer to use the exact required-language surface form given. If the concept is
genuinely not in the document at all, WAIVE it in your feedback instead — inserting
new content is outside this pass, and grounding outranks coverage.

Crucial boundary (this is where models slip):
- Discipline, competency and skill PHRASES are ordinary language and MUST be in the
  required language — e.g. "Brand Identity", "Art Direction", "Motion Design",
  "Campaign Development", "Team Leadership" must be translated, not kept.
- Only genuinely language-invariant PROPER NOUNS stay unchanged: company names,
  product/tool/framework/technology names (Figma, Adobe Photoshop, Python, AWS, React),
  certifications' official names, dates and numeric metrics.
- VERBATIM LABELS (a second, narrower boundary — this is ALSO where models slip): a
  skill name, certification name, employer name, job title, or named system/product may
  need its ordinary descriptive words translated, but a domain acronym riding inside it
  IS the name, not shorthand to spell out. GxP, GMP, ALCOA+, CSV, LIMS, MES, ITIL (and an
  unfamiliar one you don't recognise) are copied verbatim, never expanded or "corrected"
  into their full words — do not flag them as untranslated, and do not accept a draft
  that has spelled one out. "GxP Compliance & Computer System Validation" is CORRECT in
  every required language; "Good Practice Compliance & Computer System Validation" is a
  language-pass defect, not a translation.

Respond with JSON only: {"approved": bool, "issues": list[str], "feedback": str}
- approved: true only if summary, all bullets (work AND project), and all skills are
  entirely in the required language (proper nouns above excepted; project NAMES may
  stay — they are often proper nouns)
- issues: list each item still in the wrong language (empty list if approved)
- feedback: one concise instruction naming the required language and what to translate
  (empty string if approved)
"""

CV_LANGUAGE_REFINEMENT_PROMPT = """\
You rewrite a tailored CV JSON into a required language.
You receive (1) a previous draft (the full CV JSON) and (2) reviewer feedback naming the
required language and the items to translate.
Translate the `summary`, every `work_history` bullet, every project bullet (nested
`work_history[].projects[].bullets` and standalone `projects[].bullets`), and every
`skills` entry into that language, preserving meaning and facts EXACTLY. Translating is
not inventing.
Keep company names, project names, product/tool/technology names, certifications'
official names, dates, and numeric metrics unchanged. Do NOT add, remove, reorder,
split, or merge any entry, project, or skill — only translate text in place.
VERBATIM LABELS: within a skill name, certification name, employer name, job title, or
named system/product, a domain acronym — GxP, GMP, ALCOA+, CSV, LIMS, MES, ITIL, or an
unfamiliar one — IS the name; copy it exactly and never expand it into its full words
even while translating the label's ordinary descriptive words around it. If a previous
draft already spelled one out (e.g. "Good Practice" for "GxP"), restore the acronym
form — that is a correction, not a translation.
When the feedback names an exact job-description keyword for a concept the draft already
expresses, use that exact term as your wording for it — word choice, not new content.
Output ONLY the corrected CV JSON in the exact same schema — no markdown, no commentary.
"""


def build_cv_language_review_prompt(required_language: str, draft: dict) -> str:
    """User-turn prompt asking the LLM to check the CV's language compliance.

    Surfaces the language-bearing fields (summary, bullets, skills) so the reviewer
    can name specific leftovers. `draft` is the tailored CV JSON.
    """
    summary = draft.get("summary", "")
    bullets: list[str] = []
    project_bullets: list[str] = []
    for entry in draft.get("work_history", []) or []:
        bullets.extend(entry.get("bullets", []) or [])
        for proj in entry.get("projects", []) or []:
            project_bullets.extend(proj.get("bullets", []) or [])
    # Standalone projects (blind PQ 2026-07-04: these shipped unreviewed).
    for proj in draft.get("projects", []) or []:
        project_bullets.extend(proj.get("bullets", []) or [])
    skills = draft.get("skills", []) or []
    return (
        f"Required language: {required_language}\n\n"
        f"summary: {summary}\n"
        f"work_history bullets: {json.dumps(bullets, ensure_ascii=False)}\n"
        f"project bullets: {json.dumps(project_bullets, ensure_ascii=False)}\n"
        f"skills: {json.dumps(skills, ensure_ascii=False)}\n\n"
        f"Are the summary, every bullet (work and project), and every skill written "
        f"entirely in {required_language} (proper product/tool/company/project names "
        "excepted)? Respond with JSON only."
    )


def build_cv_language_refinement_prompt(
    previous_draft: dict, feedback: str, source: str = ""
) -> str:
    """User-turn prompt asking the LLM to rewrite `previous_draft` into the required language.

    ``source`` is the target language name (review_and_refine passes it positionally since
    the ADR-021 amendment); naming it explicitly removes the "required language" ambiguity.
    """
    target = f" ({source})" if source else ""
    return (
        f"Reviewer feedback: {feedback}\n\n"
        f"Previous draft:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        f"Rewrite the summary, all bullets, and all skills into the required language{target}, "
        "translating in place and preserving every fact. Output the corrected CV JSON only."
    )
