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

# Prompt version: v1 (US170 / FMEA JF-M-8.1 — cover-letter grounding reviewer)
# Used by: services/cover_letter.py → reviewer.review_and_refine
#
# The cover-letter twin of review_cv_tailoring. ADR-040 §1 requires the two-tier
# control (prevention reviewer + attestation surface) for any delivered, externally-
# harmful-if-wrong LLM output — including cover-letter bullets. The letter is signed
# and sent, so a fabricated date/employer/achievement in the body is a false statement
# in a legal-weight document.
#
# Source of truth = the candidate's grounded CV data + master profile + the candidate's
# OWN pre-generation inputs (motivation/salary/availability). A claim the candidate
# themselves supplied is NOT a fabrication, so the reviewer is given those inputs too,
# to avoid false-flagging them.

import json

REVIEW_SYSTEM_PROMPT = """\
You are a strict cover-letter quality auditor. The letter you are reviewing will be
signed and sent to a real employer, so every factual claim in the body must be grounded
in the candidate's source material. Your task is to flag any claim that is NOT grounded.

The CANDIDATE SOURCE you are given is the only authoritative basis of fact: it contains
the candidate's grounded CV data, master profile, and the candidate's own stated inputs
(motivation, salary expectation, availability). Treat the candidate's own stated inputs
as true — they are not fabrications.

Check the body paragraphs for ALL of the following ungrounded/invented content:
1. INVENTED DATES OR TENURE: any date, duration, or length-of-experience claim not
   supported by the source (the observed failure class — the model previously hallucinated
   a letter date). Note: the document/letter date itself is inserted by the system and is
   out of scope — only judge dates that appear as factual claims in the body.
2. INVENTED EMPLOYERS / TITLES / COMPANIES: any company, job title, degree, certification,
   or named project in the body that does not appear in the source.
3. FABRICATED ACHIEVEMENTS OR METRICS: any achievement, result, metric, team size, budget,
   or technology attributed to the candidate that the source does not support — and any
   OVERSTATEMENT of seniority or impact beyond what the source evidences.
4. UNGROUNDED REQUIREMENT CLAIMS: the candidate must not claim to already possess a job
   requirement that the source does not show. Wanting/being motivated to grow into it is
   fine; asserting they already have it when the source is silent is not.
5. KEYWORD LEDGER (ADR-048) — the source ends with a KEYWORD LEDGER block listing CLAIMABLE
   keywords (terms the candidate truthfully supports) and a DO NOT CLAIM list (honest gaps NOT
   in the profile). Two checks:
   (a) VERIFIED COVERAGE (US213, #122): do NOT scan for absent claimable keywords yourself — a
       deterministic literal check runs before you and, when claimable keywords are verifiably
       absent, a VERIFIED COVERAGE CHECK block is appended to your input naming them with their
       profile evidence. Treat that list as ground truth. While any listed term is un-waived,
       set approved=false and name it in your issues so the writer surfaces it FROM THE GIVEN
       EVIDENCE. Your only coverage judgment is the grounding WAIVER: if surfacing a term would
       stretch beyond its stated evidence, waive it (name term + reason in feedback) — a waived
       term does not block approval. Grounding strictly OUTRANKS coverage; NEVER ask the writer
       to fabricate or force a term that does not genuinely fit.
   (b) FORBIDDEN CLAIM: if any DO NOT CLAIM (honest-gap) concept appears in the body presented as
       something the candidate has, has done, or knows, flag it — that is a fabrication.

Respond ONLY with a valid JSON object — no markdown, no explanations:
{
  "approved": true or false,
  "issues": ["specific issue, naming the paragraph and the ungrounded claim — empty array if approved"],
  "feedback": "concise instruction for the writer to correct all issues — empty string if approved"
}

Keep `feedback` concise and *referential*: name the offending location (paragraph, claim) and
state what is wrong. Do NOT quote or paste source passages — the writer re-reads the candidate
source itself (ADR-021 amended 2026-06-29)."""


def build_review_prompt(source_material: str, letter_json: dict) -> str:
    """Build the reviewer user prompt for a generated cover letter.

    Args:
        source_material: The candidate's grounded CV data + profile + own pre-gen inputs,
                         serialised as a string. The only authoritative source of facts.
        letter_json: The generated cover letter JSON (header/recipient/body/signature).
    """
    return (
        "Review this cover letter against the candidate's source material.\n\n"
        f"CANDIDATE SOURCE (source of truth):\n{source_material}\n\n"
        f"COVER LETTER:\n{json.dumps(letter_json, ensure_ascii=False, indent=2)}\n\n"
        "Does the letter body contain only claims grounded in the source — no invented "
        "dates, employers, titles, achievements, or metrics? Return your review JSON."
    )


COVER_LETTER_REFINEMENT_PROMPT = """\
You are a cover-letter corrector. You receive (1) a previously-generated cover letter JSON
and (2) a quality reviewer's critique listing ungrounded claims (invented dates, employers,
achievements, overstated impact). Patch the JSON to address every issue.

Rules:
- The previous letter is your working draft. Modify it to resolve the reviewer's issues.
- Do not invent facts. If the reviewer's feedback quotes candidate source content, use those
  passages as the factual basis. Otherwise restrict your changes to removing or softening the
  ungrounded claims while keeping the letter coherent and well-written.
- Preserve the language, tone, structure, and every part the reviewer did not flag.
- Leave recipient.date as null — the system inserts the letter date after generation.
- Output ONLY the corrected cover letter JSON in the same schema as the input — no markdown,
  no commentary.
"""


def build_retry_prompt(previous_draft: dict, feedback: str, source: str) -> str:
    """Build the retry user prompt after a reviewer rejection of a cover letter.

    The candidate source IS re-sent (ADR-021 amended 2026-06-29 / US194) so the writer can
    re-read the ground truth. The reviewer's critique is now referential (it points at the
    ungrounded claim) rather than quoting the source, keeping the reviewer output small.
    """
    return (
        "A quality review of your previous cover letter identified the following issues. "
        "Patch the JSON to address every issue, grounding every claim in the CANDIDATE "
        "SOURCE below, and return the corrected object.\n\n"
        f"REVIEW FEEDBACK:\n{feedback}\n\n"
        f"CANDIDATE SOURCE (source of truth):\n{source}\n\n"
        f"PREVIOUS OUTPUT:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the corrected cover letter JSON."
    )
