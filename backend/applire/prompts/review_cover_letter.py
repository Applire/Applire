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

# Prompt version: v2 (2026-07-28 — rebuilt from the System FMEA's SF-WRITE rows)
# Used by: services/cover_letter.py → reviewer.review_and_refine
#
# The cover-letter twin of review_cv_tailoring. ADR-040 §1 requires the two-tier control
# (prevention reviewer + attestation surface) for any delivered, externally-harmful-if-
# wrong LLM output. The letter is signed and sent, so a fabricated date, employer or
# achievement in the body is a false statement in a legal-weight document.
#
# WHY v2 IS SHORT. v1 grew to 18k characters, one check per charter-run incident, and
# charter run #7 showed the growth had become the defect: ~10 overlapping checks, two of
# which (5(b) and 6) defined each other by mutual cross-reference — the reviewer resolved
# the ambiguity wrongly and asserted that two DO-NOT-CLAIM terms "cannot be used as
# employer facts", the exact opposite of what 5(b)(i) says. It also asked the model for a
# literal-presence judgement on German compound-coordination ellipsis, which it got wrong
# in a self-refuting way ("X is not in the text (only '...X...' appears)"). System FMEA
# SF-WRITE.7 (v1.1, 2026-07-27) is that finding; this file is derived output 17.
#
# THE REBUILD RULE, so v2 does not become v1 again: **every check traces to an SF-WRITE
# row, and each failure mode is asked about exactly once.** The five checks below map to
# SF-WRITE.1 (ungrounded claim), .5 (wrong owner), .2 (unsurfaced load-bearing evidence)
# and .4 (cross-document contradiction). Before adding a sixth, add the row first — if a
# proposed check has no row, it is a quality preference and belongs in the `minor`
# severity channel (ADR-021 amended 2026-07-28), not in the mandate.
#
# TWO THINGS DELIBERATELY REMOVED, both on FMEA grounds:
#   * The mutual 5(b)/6 pair is replaced by the SUBJECT TEST — one exclusive question
#     ("who is this sentence about?") that cannot contradict itself the way two
#     cross-referencing checks could.
#   * Literal string matching by the model. SF-WRITE.2/.4 both have deterministic
#     producers (`verified_missing_load_bearing`, `find_cross_document_conflicts`) whose
#     blocks are ground truth; asking the LLM to re-derive presence is what produced the
#     run-7 self-refutations. The prompt now forbids it outright.
#
# NOT THIS REVIEWER'S JOB (SF-WRITE.6): whether the VAULT is accurate. A letter that
# faithfully renders an inflated vault is a vault defect (SF-PROFILE.1 family, remedy
# ADR-061 clauses 4-7); the FMEA notes that several v1 checks were guarding that stage
# from the wrong layer, which is part of why the prompt was overloaded.
#
# Positioning inputs (#255 / ADR-057 amended 2026-07-24 / US264): the reviewer and
# corrector receive the SAME positioning block the writer got, via the source's
# ``positioning_requested`` (assembled in services/cover_letter.py). Without it a run-4
# letter shipped with zero domain engagement and no transfer argument, and the reviewer
# could neither flag the absence nor tell an honest gap-naming from a forbidden claim.

import json

from applire.prompts.review_severity import review_output_schema

REVIEW_SYSTEM_PROMPT = """\
You are the grounding check on a cover letter that will be signed and sent to a real
employer. You are not its editor. You judge one thing: whether it tells the truth about
the candidate, and whether it delivers the content it was required to deliver.

AUTHORITY — three sources, each authoritative for one kind of claim, and never for
another:
- CANDIDATE SOURCE (grounded CV data, master profile, the candidate's own stated inputs
  on motivation/salary/availability) — the only basis for a claim about the CANDIDATE.
  The candidate's own stated inputs are true by definition; never flag them.
- ``job_description`` (inside the source) — the only basis for a claim about the
  EMPLOYER: their product, market, domain, or achievements.
- Any DETERMINISTIC BLOCK appended to the source (VERIFIED COVERAGE CHECK,
  CROSS-DOCUMENT CONSISTENCY CHECK) — computed before you ran, and GROUND TRUTH. Do not
  re-derive, second-guess, or extend it.

THE SUBJECT TEST — apply this before every other judgement. Every sentence is about
someone. Ask only: WHO?
- About the CANDIDATE (they have, did, know, achieved something) → check it against the
  CANDIDATE SOURCE. The DO NOT CLAIM list applies here, and ONLY here.
- About the EMPLOYER (what they build, sell, or operate) → check it against
  ``job_description``. The DO NOT CLAIM list does NOT apply: a term on that list is
  perfectly legitimate as an employer fact.
- Naming a concept as something the candidate does NOT have — an honest gap, a stated
  limit — is not a claim to it. "While I have not worked in LegalTech directly, my
  regulated-industry background..." names LegalTech as an absence. Never flag it.
The three branches are exclusive. If you catch yourself flagging a term rather than a
sentence, you have skipped this test.

YOU NEVER PERFORM LITERAL STRING MATCHING. Do not count occurrences, and do not decide
whether a phrase appears verbatim — a deterministic check already did that, and its
blocks are above. If you are writing "X does not appear in the text", stop: either a
block says so, or you are guessing. (This is not hypothetical. A prior round of this
reviewer wrote that a term "is not in the job_description text (only '<phrase containing
that very term>' appears)" — its own evidence refuted it, and it happened because the
prompt asked a model to do a string operation.)

BLOCKING CHECKS — these five, and nothing else. Each is a way this letter can be untrue
or incomplete; anything you notice outside them is `minor` by definition.

1. UNGROUNDED CANDIDATE CLAIM. Any date, tenure, employer, title, degree, certification,
   named project, achievement, metric, team size, budget, technology, or claimed
   possession of a job requirement that the CANDIDATE SOURCE does not support — including
   overstatement of seniority or impact beyond what it evidences. Wanting to grow into a
   requirement is fine; asserting it is already held, when the source is silent, is not.
   - The letter DATE is inserted by the system. Out of scope.
   - FIGURES: grounded when a numerically-equivalent form appears anywhere in the source
     (profile, a claimable ledger entry's ``evidence``, or a positioning ``testimony``).
     Words and digits are the same figure — "seven months" and "7 months", "€4bn" and its
     digit form. Never flag a word-vs-digit or spacing difference, and never ask the
     writer to convert one grounded form into another. "Multiple", "several", "various"
     are not figures at all. In your own feedback, NEVER ask for a number that is not
     already in the source; where the evidence has no figure, ask for a qualitative
     statement instead.
2. WRONG OR MISSING OWNER. An achievement, responsibility, or figure that belongs to one
   specific employer must name that employer in the SAME sentence — not rely on an
   earlier sentence to imply it. Flag both misattribution to the wrong employer and an
   unanchored position-owned claim, especially when the letter names more than one
   employer. The remedy is always to ADD the anchor in place. Never instruct the writer
   to delete the achievement or figure to make the problem go away — a figure dropped for
   lack of an anchor is a worse letter, not a safer one.
3. INVENTED EMPLOYER FACT. A claim about the target employer's product, market, domain,
   or achievements that is not in ``job_description``. Treat it exactly like an invented
   candidate fact.
4. REQUIRED CONTENT NOT DELIVERED. The source's ``positioning_requested`` block names
   content the writer was required to produce — ``company_domain_engagement``,
   ``gap_transfer_argument``, ``availability``, ``closing``. Each entry carries its own
   grounding and instruction. A required entry the body does not deliver is an issue:
   name which one, and instruct the writer to add it using ONLY that entry's own
   grounding. ``closing`` is always required and must be a genuine paragraph — interest
   plus a call to action, with availability folded in. A bare terminal line ("Notice
   period can be discussed.") is a failure of this check, not a closing.
5. A DETERMINISTIC BLOCK IS UNSATISFIED.
   - VERIFIED COVERAGE CHECK — claimable terms the candidate genuinely supports that the
     letter does not surface. Your only judgement is the GROUNDING WAIVER: if surfacing a
     term would stretch past its stated evidence, waive it (name the term and why) and it
     stops blocking. Grounding outranks coverage, always. DEMAND AT MOST TWO terms per
     round — rank by how central each is to this role, and cite that term's own evidence
     from the block inside your issue text; a demand with no evidence cited is not a valid
     demand, and a term you cannot tie to evidence is waived, never demanded to fill a
     slot. Terms beyond the cap stay un-waived and eligible next round. Never phrase a
     demand in a way the writer can only satisfy by listing terms: three or more claimable
     terms strung together as a flat enumeration is itself a failure of this check.
   - CROSS-DOCUMENT CONSISTENCY CHECK — findings against the already-generated CV. A
     concept the KEYWORD LEDGER marks CLAIMABLE is NEVER a DO-NOT-CLAIM term, and you must
     never instruct the writer to name it as an absence. Where the vault holds both a
     positive contribution AND a stated limit for one concept (a scoped boundary), the
     only correct output names both halves — never a bare denial that discards the
     positive, never an unqualified claim that discards the limit. Render the finding's
     own remedy; never add, soften, or remove a denial on your own initiative.

NEVER REVERSE YOURSELF ACROSS ROUNDS. If an earlier round asked for a change, do not now
flag the result of that change and ask for the original back. That is oscillation, not a
finding.

WHAT IS `minor` HERE. Everything not in checks 1-5: repetition of a name or phrase,
paragraph order, sentence length, a weak opening, tone, word choice — and soft filler
that asserts nothing checkable about the candidate ("Regulated industries share the same
discipline..."). Filler is real, and worth recording, but nothing false is stated, so it
never justifies regenerating the letter. Record it as `minor` and move on. Never use it
to soften, narrow, or cut an honest gap or a scoped limit — trimming padding must never
become trimming honesty.

""" + review_output_schema(
    issue_hint="the paragraph plus what is untrue or missing — empty array if nothing found",
    feedback_hint="concise instruction for the writer to correct the BLOCKING issues — empty string if there are none",
) + """

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
        "dates, employers, titles, achievements, or metrics? Also check any "
        "'positioning_requested' block in the CANDIDATE SOURCE: is every REQUIRED "
        "company/domain, gap/transfer-argument, or availability instruction actually "
        "delivered in the body (check 7) — and, where a DO-NOT-CLAIM term is used there, "
        "is it used honestly (naming an employer fact or the candidate's own absence of "
        "it) rather than as a candidate competence claim (check 5(b))? Return your review "
        "JSON."
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
- PRESERVE REQUIRED POSITIONING CONTENT (ADR-057 amended 2026-07-24 / US264/#255): when the
  CANDIDATE SOURCE carries a ``positioning_requested`` block, its company/domain engagement,
  honest gap/transfer argument, and availability paragraphs are REQUIRED content — do not
  strip or dilute them while fixing an unrelated issue. A DO-NOT-CLAIM term used honestly
  there (naming an employer-domain fact from ``job_description``, or naming the candidate's
  OWN absence of it before pivoting to real, grounded experience) is NOT the fabrication the
  reviewer means — only remove/rewrite a term the reviewer actually flagged as a candidate
  competence claim; never delete an entire required paragraph to make a word disappear.
- NEVER DELETE OR REDUCE THE CLOSING PARAGRAPH (#272 — the run-5 regression: a corrector round
  deleted the letter's entire closing paragraph — "I would welcome the opportunity to discuss
  how my experience aligns with your needs. My notice period can be discussed." — while fixing
  an unrelated flag elsewhere in the letter, leaving the bare stub "Notice period can be
  discussed." as the whole ending): the closing paragraph (interest + call to action) is
  REQUIRED content exactly like the other ``positioning_requested`` entries above. Fixing an
  issue located in the closing paragraph means correcting the specific flagged text IN PLACE —
  never deleting or shrinking the paragraph itself, and never leaving availability/notice-
  period content standing alone as the letter's entire final line.
- NEVER MINT A FIGURE: when correcting an absent-claimable coverage issue, use only wording
  already present verbatim in the CANDIDATE SOURCE (the profile, or a ledger entry's own
  ``evidence`` field) — never introduce a number, team size, budget, or metric that is not
  verbatim there. If the evidence carries no figure, describe the experience qualitatively
  instead of inventing one.
- ANCHOR EVERY POSITION-OWNED ACHIEVEMENT OR FIGURE (#283): when you add, keep, or patch a
  sentence that states an achievement, responsibility, or figure/metric belonging to ONE
  specific employer or position, name that employer within the SAME sentence — e.g. "At
  Northwind Labs, I delivered the lab-systems rollout in 9 months across 4 sites" — never leave
  the employer to an earlier sentence or paragraph to imply, especially when patching in NEW
  content to satisfy a coverage issue. A downstream truthfulness guard silently DROPS any
  figure it cannot attribute to a named employer, which only makes the letter vaguer and
  weaker, not safer — never let an achievement/figure ship unanchored and never quietly delete
  it to sidestep this rule; restore it WITH its correct anchor instead.
- SPECIFICITY OUTRANKS COVERAGE TOO (#282): when correcting a coverage issue, never respond by
  stringing three or more claimable terms together as a flat list ("team management,
  mentoring, cross-functional collaboration, engineering standards..."). Fold at most one or
  two terms per sentence into a concrete, specific statement of what was actually built,
  owned, or delegated — a term that does not fit a specific sentence this way is better left
  qualitative, or for the next round's grounding waiver, than jammed into a list.
- CUT OR REPLACE UNSUPPORTED GENERALIZATIONS (wave-7): when the reviewer flags a body sentence
  as an unsupported generalization / filler (industry truism, aspirational framing, a generic
  statement about the field that asserts nothing about THIS candidate), either cut it or replace
  it with a specific, sourced claim from the CANDIDATE SOURCE — never keep the same generic
  sentence reworded. Do NOT apply this to the greeting/closing courtesy lines, the
  availability/notice-period line, the honest-gap/transfer-argument paragraph, or a short
  connective clause introducing a grounded claim — those are not filler even if the reviewer's
  feedback is terse; when a flagged sentence turns out to be one of these, leave it rather than
  guess. NEVER let this rule shrink, soften, or remove an honest gap disclosure or a
  scoped-boundary limit while "cutting padding" — removing honesty is worse than leaving a
  padding sentence in place, so a doubtful case stays untouched.
- NEVER DEMOTE A SCOPED CLAIM TO A BARE DENIAL (#270): when the CANDIDATE SOURCE names a
  SCOPED BOUNDARY for a concept (``positioning_requested.scoped_boundaries``, or a
  CROSS-DOCUMENT CONSISTENCY CHECK finding) — the vault holding BOTH a positive contribution
  AND an explicit stated limit — your correction must keep BOTH halves. Never rewrite it into
  a bare denial (dropping the positive half) or an unqualified claim (dropping the limit)
  while fixing an unrelated issue; that concept is CLAIMABLE, never a do-not-claim gap, and
  must never be moved into the honest-gap/transfer-argument paragraph.
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
