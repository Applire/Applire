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

# Prompt version: v3 (#539 / ADR-076 clause 3, 2026-08-16 — the TERMINAL round
#   variant is added: TERMINAL_REVIEW_SYSTEM_PROMPT + build_terminal_review_prompt
#   review the COMPOSED letter (the delivered artifact — date stamped, sign-off
#   normalized, sender backfilled, salutation split, recipient overlaid, figure/
#   outcome guards applied), with the real render measure attached as context.
#   The authority model, the subject test, the five checks and the minor channel
#   are BYTE-IDENTICAL to v2 and shared as module constants — one definition,
#   two shape doors (ADR-066); only the SHAPE NOTE differs. The drafting-round
#   REVIEW_SYSTEM_PROMPT is byte-for-byte unchanged. The terminal SHAPE NOTE
#   declares the guard-applied chrome ground-truth-by-construction (the #385
#   lesson: a check aimed at fields a mechanism owns can only fail falsely and
#   exhausts the loop), and the render-measure block is context-only and
#   explicitly forbids length findings (#525's exhaustion fuel).
#   ADR-062 clause 7: prompt effect; CI pins the wording only.)
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
#   * Literal string matching by the model. Presence facts have a deterministic producer
#     (`verified_missing_load_bearing`) whose block is ground truth; asking the LLM to
#     re-derive presence is what produced the run-7 self-refutations. Forbidden outright.
#
# ADR-062 (2026-07-28) moved SF-WRITE.4 the other way. Cross-document contradiction USED
# to arrive as a block, from `find_cross_document_conflicts` over a negation-proximity
# matcher. That matcher answered "does this clause deny this concept?" — a question about
# meaning — with a 6-word-token distance, and it read contrastive transfer arguments
# ("X nicht, doch Y") as denials: three of four, measured. In run #8 it labelled a
# sentence AFFIRMING a concept as a bare denial of it, marked the label GROUND TRUTH, and
# the loop ran ten rounds without approving. The block is gone. The reviewer already
# holds both documents and the Keyword Ledger, so the rule is stated once, in check 5.
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

# Shared building blocks (v3): ONE definition of the grounding role, the
# authority model, the subject test, the checks and the minor channel; two
# shape doors (drafting round / terminal composed round).
_GROUNDING_INTRO = """\
You are the grounding check on a cover letter that will be signed and sent to a real
employer. You are not its editor. You judge one thing: whether it tells the truth about
the candidate, and whether it delivers the content it was required to deliver.

"""

_SHAPE_NOTE_TERMINAL = """\
SHAPE NOTE — TERMINAL ROUND (ADR-076 clause 3): the letter you receive is the COMPOSED
artifact, exactly as it will be delivered. After the writer, a fixed sequence of
deterministic guards ran over it by code: the letter DATE was stamped by the system; the
sign-off closing was normalized to the language-routed label and the sender name
backfilled from the candidate's own profile; the salutation was split into its own
paragraph; the recipient fields were overlaid with the user's own typed inputs; an
unanchored borrowed figure may have been stripped, and a bare target/projection may have
been re-framed to its measured outcome from the profile. These effects are ground truth
BY CONSTRUCTION — never flag the date, the sign-off wording, the salutation placement,
the recipient identity, or the sender name as an issue of any kind, and never ask for a
guard-stripped or guard-re-framed sentence to be restored. Your checks apply to the
authored content of the letter body.

A RENDER MEASURE block accompanies the letter as context only. Length is enforced by a
bounded condense mechanism that has already run — NEVER raise a length, word-count,
page-count, or "too long / too short" finding.

"""

_AUTHORITY_AND_CHECKS = """\
AUTHORITY — three sources, each authoritative for one kind of claim, and never for
another:
- CANDIDATE SOURCE (grounded CV data, master profile, the candidate's own stated inputs
  on motivation/salary/availability) — the only basis for a claim about the CANDIDATE.
  The candidate's own stated inputs are true by definition; never flag them.
- ``job_description`` (inside the source) — the only basis for a claim about the
  EMPLOYER: their product, market, domain, or achievements.
- Any DETERMINISTIC BLOCK appended below, each titled in CAPS (VERIFIED COVERAGE CHECK,
  FIGURE OWNERSHIP, DO-NOT-CLAIM PRESENCE, RECORDED JOB TITLES, and others) — computed
  before you ran, and GROUND TRUTH. Do not re-derive, second-guess, or extend one. Each
  states a FACT: whether a string is present, who owns a figure, what status the ledger
  holds. None tells you what a sentence MEANS — that is yours.

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
block says so, or you are guessing. (A prior round of this reviewer called a term absent
from the job_description while quoting a phrase containing it — its own evidence refuted
it, because the prompt had asked a model to do a string operation.)

BLOCKING CHECKS — these five, and nothing else. Each is a way this letter can be untrue
or incomplete; anything you notice outside them is `minor` by definition.

1. UNGROUNDED CANDIDATE CLAIM. Any date, tenure, employer, title, degree, certification,
   named project, achievement, metric, team size, budget, technology, or claimed
   possession of a job requirement that the CANDIDATE SOURCE does not support — including
   overstatement of seniority or impact beyond what it evidences. Wanting to grow into a
   requirement is fine; asserting it is already held, when the source is silent, is not.
   - The letter DATE is inserted by the system. Out of scope.
   - FIGURES: grounded when a numerically-equivalent form appears anywhere in the source
     (profile, a claimable ledger entry's ``evidence``, a positioning ``testimony``, or
     a ``pinned_facts`` quote — a pinned quote is the candidate's vault text verbatim).
     Words and digits are the same figure — "seven months" and "7 months", "€4bn" and its
     digit form. Never flag a word-vs-digit or spacing difference, and never ask the
     writer to convert one grounded form into another. "Multiple", "several", "various"
     are not figures at all. In your own feedback, NEVER ask for a number that is not
     already in the source; where the evidence has no figure, ask for a qualitative
     statement instead.
   - AN INVENTED LIMIT IS AN UNGROUNDED CLAIM, in the direction that costs the candidate
     most. "I have no direct experience with X" is a statement about the candidate, and it
     is grounded ONLY by one of their own STATED LIMITS. If the letter disclaims something
     no stated limit disclaims — especially something the Keyword Ledger marks claimable —
     flag it here and say which evidence it throws away. Note that a stated limit names
     the candidate's adjacent STRENGTHS ("no IFS/BRC experience, but ten years of ISO-9001
     audit practice"); those named strengths are grounded, not limited.
2. WRONG OR MISSING OWNER. An achievement, responsibility, or figure that belongs to one
   specific employer must name that employer in the SAME sentence — not rely on an
   earlier sentence to imply it. Flag both misattribution to the wrong employer and an
   unanchored position-owned claim, especially when the letter names more than one
   employer. The two halves reach you with different evidence:
   2a. A FIGURE. The FIGURE OWNERSHIP block settles anchor PRESENCE: where it names the
       employer a figure's own sentence names, you may NOT report that figure as
       unanchored. Whether that is the RIGHT employer, against the same line's owner
       list, stays yours. Where it states no anchor, nothing is settled — judge as 2b.
   2b. NO FIGURE — a tenure, a title, a scope of duty, an outcome stated without a
       number. No block covers this half, and 2a being answered says nothing about it;
       ask the same question of the sentence itself.
   The remedy is always to ADD the anchor in place. Never instruct the writer
   to delete the achievement or figure to make the problem go away — a figure dropped for
   lack of an anchor is a worse letter, not a safer one.
3. INVENTED EMPLOYER FACT. A claim about the target employer's product, market, domain,
   or achievements that is not in ``job_description``. Treat it exactly like an invented
   candidate fact.
4. REQUIRED CONTENT NOT DELIVERED. The source's ``positioning_requested`` block names
   content the writer was required to produce — ``company_domain_engagement``,
   ``gap_transfer_argument``, ``availability``, ``scope_positioning``,
   ``pinned_facts``, ``closing``. Each
   entry carries its own grounding and instruction. A required entry the body does not
   deliver is an issue: name it BY KEY (e.g. ``company_domain_engagement``) and point
   the writer to ONLY that entry's own grounding — NEVER copy ``job_description`` wording
   into your demand; a pasted phrase carrying a DO-NOT-CLAIM term comes back as a
   candidate claim a later round must then flag. ``closing`` is always required: a
   genuine paragraph (interest + call to action, availability folded in), never a bare
   terminal line.
   ``scope_positioning`` (ADR-070) is the candidate's own attested scale evidence:
   delivering it with the candidate's values is not overclaiming; stating the posting's
   own figure as the candidate's is.
   ``pinned_facts`` (ADR-077) are quotes the CANDIDATE pinned from their own vault:
   each must appear in the body, woven naturally, never extended beyond what the
   quote states. A missing pinned fact is an issue — name it as ``pinned_facts``
   and quote ONLY the missing pin's own text.
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
   - CROSS-DOCUMENT CONSISTENCY. You hold the already-generated CV as well as this
     letter. They must not disagree about a concept the KEYWORD LEDGER marks CLAIMABLE.
     Such a concept is never a DO-NOT-CLAIM term and must never be named as an absence
     in either document — if the CV asserts it and the letter disclaims it, the LETTER
     is what is wrong, because the ledger says the vault supports the claim. A concept
     is an honest gap only when the ledger marks it so or a STATED LIMIT disclaims it in
     the candidate's own words. Judge this by reading the two documents; there is no
     block listing conflicts for you, because the rule below is what the block used to
     approximate and got wrong.
     A sentence that admits one thing and affirms another — "no IFS/BRC experience, but
     ten years of ISO 9001 audit practice" — is an honest transfer argument, not a
     denial of the second half. It is the correct shape for a gap. Never flag it, and
     never ask the writer to remove, soften, or split it.
   - KEYWORD LEDGER — DO NOT CLAIM. A ledger honest-gap concept may never stand as
     something the candidate HAS, has done, or knows. The SUBJECT TEST decides whose fact
     the sentence states; inside a sentence about the CANDIDATE the line is grammatical
     role — the term may appear as an ASPIRATION, never as a POSSESSION. Presence is not
     yours: the DO-NOT-CLAIM PRESENCE block lists the terms this draft carries, and an
     issue about any other term must QUOTE the draft words carrying it.

NEVER REVERSE YOURSELF ACROSS ROUNDS. If an earlier round asked for a change, do not now
flag the result of that change and ask for the original back. That is oscillation, not a
finding.

WHAT IS `minor` HERE. Everything not in checks 1-5: repetition of a name or phrase,
paragraph order, sentence length, a weak opening, tone, word choice, a grammatical slip
(e.g. wrong German gender agreement: "Mein Budgetverantwortung" for "Meine
Budgetverantwortung") — and soft filler
that asserts nothing checkable about the candidate ("Regulated industries share the same
discipline..."). Filler is real, and worth recording, but nothing false is stated, so it
never justifies regenerating the letter. Record it as `minor` and move on. Never use it
to soften, narrow, or cut an honest gap or a scoped limit — trimming padding must never
become trimming honesty.

"""

_SCHEMA_AND_CLOSER = review_output_schema(
    issue_hint="the paragraph plus what is untrue or missing — empty array if nothing found",
    feedback_hint="concise instruction for the writer to correct the BLOCKING issues — empty string if there are none",
) + """

Keep `feedback` concise and *referential*: name the offending location (paragraph, claim) and
state what is wrong. Do NOT quote or paste source passages — the writer re-reads the candidate
source itself (ADR-021 amended 2026-06-29)."""

REVIEW_SYSTEM_PROMPT = _GROUNDING_INTRO + _AUTHORITY_AND_CHECKS + _SCHEMA_AND_CLOSER

TERMINAL_REVIEW_SYSTEM_PROMPT = (
    _GROUNDING_INTRO + _SHAPE_NOTE_TERMINAL + _AUTHORITY_AND_CHECKS + _SCHEMA_AND_CLOSER
)


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
        "company/domain, gap/transfer-argument, availability, or scope-positioning "
        "instruction actually delivered in the body (check 4) — and, where a "
        "DO-NOT-CLAIM term is used there, is it used honestly (naming an employer fact "
        "or the candidate's own absence of it) rather than as a candidate competence "
        "claim (check 5, the KEYWORD LEDGER — DO NOT CLAIM bullet)? Return your review "
        "JSON."
    )


def build_terminal_review_prompt(
    source_material: str,
    composed_json: dict,
    *,
    page_count: int | None,
    letter_pages: int,
    word_count: int,
    word_budget: int,
    condense_exhausted: bool,
) -> str:
    """Build the TERMINAL-round reviewer user prompt (#539, ADR-076 clause 3).

    Args:
        source_material: The same grounding source the drafting rounds review
                         against (cv_data + profile + candidate inputs + JD +
                         positioning, plus the folded ledger/title blocks).
        composed_json: The COMPOSED letter — ``letter_data`` after the single
                       composition site (the seven deterministic guards), i.e.
                       the delivered artifact.
        page_count: The real render measure (pages), or ``None`` when the
                    render/measure failed (the round still runs; the verdict
                    simply lacks the page measure).
        letter_pages: The region's letter page norm (ADR-051 §1 — always the
                    norm value, never a literal).
        word_count: The deterministic ``body_word_count`` of the composed letter.
        word_budget: The region's body word budget (ADR-051 §1).
        condense_exhausted: True when the bounded condense rewrite is spent (or
                    unavailable) and the letter still exceeds the page norm —
                    stated for honest context, never as a mandate.
    """
    if page_count is not None:
        measure = (
            f"measured pages: {page_count}, page norm: {letter_pages}; "
            f"body words: {word_count}, word budget: {word_budget}"
        )
    else:
        measure = (
            f"page measure unavailable for this round (page norm: {letter_pages}); "
            f"body words: {word_count}, word budget: {word_budget}"
        )
    if condense_exhausted:
        measure += (
            " — condense exhausted: the bounded condense rewrite could not reach "
            "the page norm"
        )
    return (
        "Terminal review: the letter below is the COMPOSED artifact exactly as it "
        "will be delivered (see SHAPE NOTE — TERMINAL ROUND).\n\n"
        f"CANDIDATE SOURCE (source of truth):\n{source_material}\n\n"
        f"COMPOSED COVER LETTER (the delivered letter):\n"
        f"{json.dumps(composed_json, ensure_ascii=False, indent=2)}\n\n"
        f"RENDER MEASURE (context only): {measure}. Length is enforced by a "
        "bounded condense mechanism that has already run — NEVER raise a "
        "length, word-count or page-count finding.\n\n"
        "Does the letter body contain only claims grounded in the source — no invented "
        "dates, employers, titles, achievements, or metrics? Also check any "
        "'positioning_requested' block in the CANDIDATE SOURCE: is every REQUIRED "
        "company/domain, gap/transfer-argument, availability, or scope-positioning "
        "instruction actually delivered in the body (check 4) — and, where a "
        "DO-NOT-CLAIM term is used there, is it used honestly (naming an employer fact "
        "or the candidate's own absence of it) rather than as a candidate competence "
        "claim (check 5, the KEYWORD LEDGER — DO NOT CLAIM bullet)? Return your review "
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
  honest gap/transfer argument, availability, ``scope_positioning`` and ``pinned_facts``
  paragraphs are REQUIRED content — do not strip or dilute them while fixing an
  unrelated issue. A ``pinned_facts`` quote (ADR-077) is the candidate's own vault
  text pinned by the candidate — keep each pinned fact stated (verbatim in
  substance) and never extend it beyond what the quote says. A
  ``scope_positioning`` statement (ADR-070) quotes the candidate's own attested scale
  values with their stated unit — it is grounded by that entry's own ``testimony``, never
  a fabrication; when patching it, keep the candidate's figures verbatim and never
  substitute the posting's own figure. A DO-NOT-CLAIM term used honestly
  there (naming an employer-domain fact from ``job_description``, or naming the candidate's
  OWN absence of it before pivoting to real, grounded experience) is NOT the fabrication the
  reviewer means — only remove/rewrite a term the reviewer actually flagged as a candidate
  competence claim; never delete an entire required paragraph to make a word disappear.
  When ADDING company/domain engagement, write it as its OWN sentence whose subject is
  the EMPLOYER ("Rheinwerk fertigt ... — genau dieses Umfeld reizt mich."), then state
  the candidate's motivation separately. Never embed the employer's domain phrase inside
  a sentence whose subject is the candidate's own know-how or experience ("mit
  fundiertem Know-how in ..." makes every term in the phrase a candidate claim, and a
  DO-NOT-CLAIM term in it will be flagged; #420). And if the reviewer flags a
  DO-NOT-CLAIM term you used as an employer fact, do not restore or argue — rewrite the
  domain engagement around the employer's NON-denied aspects (e.g. "Kunststoffverpackungen
  für Konsumgüter") so the letter converges instead of oscillating; everything else the
  paragraph delivers stays.
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
  content to satisfy a coverage issue. An unanchored claim is the one shape nothing
  downstream can check: in a letter naming more than one employer, the post-generation
  attribution guards FAIL OPEN on a sentence that names none (#299), so a borrowed claim
  left vague survives every check that runs after you, and the reviewer's check 2 flags it
  again next round. A vaguer letter is not a safer one — never let an achievement/figure
  ship unanchored and never quietly delete it to sidestep this rule; restore it WITH its
  correct anchor instead.
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
  guess. NEVER let this rule shrink, soften, or remove an honest gap disclosure or a stated
  limit while "cutting padding" — removing honesty is worse than leaving a padding sentence
  in place, so a doubtful case stays untouched.
- NEVER INVENT A LIMIT, AND NEVER DROP ONE. ``positioning_requested.stated_limits`` holds the
  candidate's own words about what they cannot claim, and they are the ONLY limits that
  exist. Keep every one the draft already states. Do NOT add a limit to any other claim —
  in particular, a concept named INSIDE one of those statements as something the candidate
  DOES have is a STRENGTH, not a limit (an honest denial names the adjacent strengths that
  transfer). Anything the Keyword Ledger marks claimable stays fully claimable unless a
  stated limit denies it, and is never moved into the honest-gap paragraph. Writing "I have
  no direct experience with X" when no stated limit says so is an UNGROUNDED CLAIM about the
  candidate — it costs them their own best evidence, and it is as untrue as an inflation.
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
