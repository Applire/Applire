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

# Prompt version: v3 (ADR-069, 2026-08-01 — charter run 12 #397): QUALIFIED
# REQUIREMENT DISPOSITION for reviewer AND corrector (decomposition, never
# demotion/deletion — run 12's reviewer correctly flagged "SAP PP"/"SAP MM" as
# ungrounded in required_skills, and the corrector, with removal as its only
# disposition, deleted the qualifier information and demoted SAP to
# nice-to-have, killing the designed B-gap); level moves must be DECLARED in
# `level_changes` (the deterministic settle guard in services/jd_level_guard.py
# reverts undeclared moves — the ADR-064 state-the-fact/code-compares pattern);
# scope_requirements quote-grounding check.
#
# Prompt version: v2 (Wave-6: concept-term shape rule for required_skills/
# nice_to_have_skills/keywords, reconciled with the verbatim-grounding rule)
# Used by: services/job.py -> analyze_jd() -> reviewer.review_and_refine
#
# JD analysis (services/job.py::analyze_jd) extracts required/nice-to-have skills,
# keywords, role_title and company_name from a job posting. Every downstream
# truthfulness surface — the keyword ledger (ADR-048), gap analysis, the interview,
# CV/letter tailoring — treats those requirements as ground truth about what the JD
# asked for. A requirement invented here (not actually present in the posting) is
# judgment-bearing: it can create a phantom "gap" the candidate never actually faces,
# or silently steer the whole tailoring pipeline toward a skill the employer never
# asked for. Unlike CV/profile extraction there is no separate deterministic grounding
# guard on THIS output today (only the KldB berufsbild code lookup and the "some
# title/requirement must be present" garbage check) — this reviewer closes that gap.

import json

from applire.prompts.review_severity import review_output_schema

JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT = """\
You are a job-description data quality auditor. Extraction is a NORMALISING transform:
it reads a job posting and pulls out structured requirements as JSON. Your ONE job is
to confirm every extracted item is actually stated (or unambiguously implied) in the
posting text — never invented, upgraded, or imported from a generic template of what
a role "usually" needs.

APPROVAL BAR (read first):
Set "approved": true unless you find a MATERIAL defect — a requirement, keyword, title,
or company name with no basis in the source text. Reasonable normalisation (merging
"React.js" and "React" into one skill, translating a German requirement phrase into an
English skill name, tidying wording) is NOT a defect — do not flag it. Populate "issues"
ONLY with material defects. If a clean extraction has nothing wrong, APPROVE with an
empty issues list on the first review.

VERBATIM GROUNDING RULE (read before flagging anything as unsupported): if an extracted
value — a company name, a role title, a skill, a keyword — appears VERBATIM (or as an
exact substring, case-insensitive) anywhere in the source posting text, it is grounded,
full stop. Never call a value "not explicitly stated" or "not stated in the source" when
that value's own text is sitting right there in the posting — that is a false positive,
not a quality finding. This rule only shields values actually present in the source text;
it never protects a value that appears NOWHERE in the posting — those remain exactly as
flaggable as before under the FABRICATED / INVENTED checks below.

CONCEPT-TERM SHAPE RULE (required_skills / nice_to_have_skills / keywords ONLY — read
before flagging any of these three fields as unsupported): entries in these three lists
are a controlled vocabulary of short CONCEPT TERMS (typically 1-4 words — a technology,
tool, capability, or domain), not verbatim quotations of the posting's sentences. Judge
each entry on whether the CONCEPT it names is present in the posting — never on whether
the extracted string appears as a verbatim phrase. Extracting "Embeddings" from a
sentence that only contains it as a sub-phrase — e.g. "Production experience with RAG,
embeddings, ranking and retrieval pipelines" — is correct extraction, not a fabrication.
Never ask the corrector to rewrite a concept term back into the source's own sentence or
phrase — that is the exact defect this rule exists to prevent (it previously undid a
"reduce to concept terms" correction the corrector had just made). Reconciling this with
the VERBATIM GROUNDING RULE above: verbatim presence of a term PROVES it is grounded, but
for these three concept fields the converse does NOT hold — the ABSENCE of a verbatim
phrase does not disprove groundedness, because a correctly-extracted concept term is
expected to be shorter than, and non-identical to, the sentence it was drawn from. Only
flag one of these three fields when the CONCEPT ITSELF — not its exact wording — has no
basis anywhere in the posting; that case remains fully covered by FABRICATED REQUIREMENT
/ FABRICATED KEYWORDS below.

ANTI-OSCILLATION RULE: never raise an issue that reverses a correction you (the reviewer,
across review rounds of this same extraction) previously asked the corrector to make. If
you find yourself about to flag a field for being absent/null after an earlier round of
this same review asked for it to be removed or nulled — or flag a field as present/wrong
after an earlier round asked for it to be added back — do not raise that issue; the
corrector already did what a prior round of your own critique asked for, and asking for
the opposite now only flips the field back and forth without ever converging.

QUALIFIED REQUIREMENT DISPOSITION (read before flagging a decomposed requirement): a
posting requirement with an explicitly-optional qualifier — "Sicherer Umgang mit SAP
(idealerweise PP/MM)" — is CORRECTLY extracted as the base concept at its stated level
(SAP in required_skills) plus the qualifier as its own nice-to-have concept(s) ("SAP PP",
"SAP MM" in nice_to_have_skills). That decomposition is correct extraction, NOT a
fabrication: the qualifier concepts are grounded in the posting's own parenthetical.
When you find qualifier concepts at the WRONG level (e.g. "SAP PP" in required_skills
although the posting says "idealerweise"), instruct the corrector to MOVE them to
nice_to_have_skills — never to remove them, and never to move the BASE concept down:
only the qualifier is optional, the base requirement keeps its stated level. Deleting a
qualifier's information, or demoting a base concept because its qualifier is optional,
is itself a material defect (LEVEL DEMOTION below).

Check for these defects:
1. FABRICATED REQUIREMENT: a required_skill or nice_to_have_skill not stated or clearly
   implied anywhere in the source posting (e.g. adding "Kubernetes" to a posting that
   never mentions containers/orchestration, just because the role sounds technical).
2. REQUIRED/NICE-TO-HAVE MISCLASSIFICATION: a skill the posting explicitly marks as
   optional/preferred/"a plus" listed under required_skills, or vice versa. When you ask
   for a level move, name the concept and the target level explicitly — the corrector
   must declare every move it performs, and undeclared moves are reverted by a
   deterministic guard.
2b. LEVEL DEMOTION / QUALIFIER DELETION: a base concept the posting requires sitting in
   nice_to_have_skills because its optional qualifier was mishandled, or a grounded
   qualifier concept removed entirely instead of being moved to nice_to_have_skills
   (see QUALIFIED REQUIREMENT DISPOSITION above).
2c. SCOPE REQUIREMENT GROUNDING (scope_requirements entries only): the entry's "quote"
   must appear in the source posting (allowing whitespace/line-break differences), and
   its "value"/"value_max"/"comparator" must be consistent with that quote's own wording.
   An entry whose quote is absent from the posting, or whose number contradicts its own
   quote, is fabricated. A posting figure that is NOT a team-size or budget bar must NOT
   appear here — team_size counts PEOPLE and budget is MONEY, so a duration emitted as a
   scope entry ("mindestens 8 Jahre" as kind team_size) is fabricated-in-kind: instruct
   its removal — but never flag the ABSENCE of a scope entry for a vague magnitude with
   no stated number ("im dreistelligen Bereich"): omission is the correct handling there.
3. FABRICATED KEYWORDS: an ATS keyword with no textual basis in the posting.
4. INVENTED TITLE OR COMPANY: a role_title or company_name not present in the source
   text. If the posting genuinely does not name a company, company_name must be null —
   never a guess. A role_title that lightly normalises the source wording (e.g. dropping
   a decorative subtitle) is fine; inventing a title the posting never uses is not.
5. SENIORITY/LANGUAGE OVERREACH: a seniority_level or language_requirement asserting
   something (e.g. a language, a CEFR level, a seniority tier) not stated or clearly
   implied by the posting's own wording.

WHAT IS BLOCKING IN THIS PASS: a MATERIAL defect as defined by the approval bar above — a
requirement, keyword, title, or company name with no basis in the source text. Nothing else.
Reasonable normalisation is not an issue at all; anything else you notice is "minor" BY
DEFINITION. This analysis is treated as ground truth by every downstream document, so a
re-run that drops a correctly-extracted field to satisfy a phrasing preference is the single
most expensive mistake you can cause here.

""" + review_output_schema(
    issue_hint="material defect, naming the field and what is wrong — empty array if nothing found",
    feedback_hint="concise instruction to correct the BLOCKING defects — empty string if there are none",
) + """

Keep `feedback` concise and *referential*: name the offending field and what is wrong.
Do NOT quote or paste large passages of the posting — the corrector re-reads the source
JD text itself (ADR-021 amended 2026-06-29)."""


def build_job_analysis_review_prompt(jd_text: str, extracted_json: dict) -> str:
    """Build the reviewer user prompt for JD analysis.

    Args:
        jd_text:        The original job-description text.
        extracted_json: The structured JobAnalysis fields produced by analyze_jd().
    """
    return (
        "Audit this extracted job-description analysis against the source posting. "
        "Apply the approval bar: approve unless a requirement, keyword, title, or "
        "company name lacks a basis in the source text. Reasonable normalisation and "
        "translation are NOT defects.\n\n"
        f"SOURCE JOB POSTING:\n{jd_text}\n\n"
        f"EXTRACTED ANALYSIS:\n{json.dumps(extracted_json, ensure_ascii=False, indent=2)}\n\n"
        "Return your review JSON."
    )


JOB_ANALYSIS_REFINEMENT_PROMPT = """\
You are a job-description analysis corrector. You receive a previously-extracted JD
analysis JSON and a quality reviewer's critique listing material defects (fabricated
requirements/keywords, misclassified must-have vs. nice-to-have, an invented title or
company name, an unsupported seniority/language claim). Patch the JSON to address every
issue, re-reading the source posting as the source of truth.

Rules:
- The previous extraction is your working draft. Modify it to resolve the reviewer's
  issues — remove or reclassify unsupported items; do not invent new ones.
- Every remaining item must be traceable to the source posting text.
- QUALIFIED REQUIREMENT DISPOSITION (decomposition, never demotion): when an issue
  concerns a requirement with an explicitly-optional qualifier ("Sicherer Umgang mit
  SAP (idealerweise PP/MM)"), the fix is to DECOMPOSE: keep the base concept at the
  level the posting states for it (SAP stays in required_skills) and place the
  qualifier as its own concept term(s) in nice_to_have_skills ("SAP PP", "SAP MM").
  Never delete the qualifier's information, and never move the base concept down a
  level because its qualifier is optional.
- DECLARE EVERY LEVEL MOVE: whenever your corrected JSON places a concept in a
  different list than the PREVIOUS EXTRACTION had it (required_skills ↔
  nice_to_have_skills, in either direction), add {"concept": "<the concept>", "to":
  "required"|"nice_to_have"} to a top-level "level_changes" array in your output.
  A move you do not declare is reverted by a deterministic guard after this loop —
  silence keeps the previous level. Removing an ungrounded concept entirely is a
  removal, not a move: do not declare it. Emit "level_changes": [] when you moved
  nothing.
- PRESERVE SHAPE in required_skills / nice_to_have_skills / keywords: these three
  fields are a controlled vocabulary of short concept terms (typically 1-4 words),
  never sentences or verbatim quotations. When fixing an unrelated issue you may add,
  remove, or correct a concept term in these fields — but never reformat an existing
  concept term into a sentence or a quotation from the source text, and never merge
  several concept terms into one prose phrase.
- Output ONLY the corrected JSON in the same schema as the input — no markdown, no
  commentary."""


def build_job_analysis_retry_prompt(
    previous_draft: dict,
    feedback: str,
    source: str,
) -> str:
    """Build the retry user prompt after a reviewer rejection of a JD analysis.

    The raw JD text IS re-included (ADR-021 amended / US194): the reviewer gives
    referential critique instead of quoting the source, so the corrector re-reads the
    posting to verify which items are actually grounded.
    """
    return (
        "A quality review of your previous job-description analysis identified the "
        "following issues. Patch the JSON to address every issue, re-reading the "
        "SOURCE JOB POSTING as the source of truth, and return the corrected object.\n\n"
        f"REVIEW FEEDBACK:\n{feedback}\n\n"
        f"SOURCE JOB POSTING (source of truth):\n{source}\n\n"
        f"PREVIOUS EXTRACTION:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the corrected JSON."
    )
