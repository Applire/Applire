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

# Prompt version: v9 (#580 / ADR-077 amended 2026-08-26 — check 7, PINNED FACT NOT
#   DELIVERED, shared by both shape doors. The captured 2026-08-25 run showed the
#   writer reproducing a pinned vault quote word-for-word and THIS reviewer's check
#   6(b) having the corrector strip it (the ledger listed 'microservices' as
#   DO-NOT-CLAIM). Check 7 therefore (a) reads its ground truth from a deterministic
#   PINNED FACTS CHECK block (the US213 pattern — `pin_reach.pinned_facts_reviewer_
#   prompt_fn` measures presence per round and demands each pin at most once per
#   loop), (b) is explicitly SUBORDINATE to checks 1/4/5/6(b) — truth outranks a pin,
#   a LEDGER CONFLICT entry is never demanded — and (c) keeps the feedback referential
#   (entry id + first words; the corrector re-reads the verbatim quote from the
#   PINNED FACTS block folded into its source) while the issue text carries the
#   quote for the compliance instrument. The mandate line is unchanged: a numbered
#   check is blocking by that sentence. Size ratchet added with it (letter precedent).
#   ADR-062 clause 7: prompt effect; CI pins the wording only.)
# Prompt version: v8 (#538 / ADR-076 clause 3, 2026-08-16 — the TERMINAL round
#   variant is added: TERMINAL_REVIEW_SYSTEM_PROMPT + build_terminal_review_prompt
#   review the COMPOSED document (the delivered artifact — vault joins,
#   certifications, role facts, nested projects, photo all present), with the
#   real render measure attached as context. The checks, the skills-list scope
#   and the blocking mandate are BYTE-IDENTICAL to v7 and shared as module
#   constants — one definition, two shape doors (ADR-066); only the SHAPE NOTE
#   differs. The prose-round REVIEW_SYSTEM_PROMPT is byte-for-byte unchanged.
#   The terminal SHAPE NOTE exists because of #385's lesson: a check aimed at
#   joined fields in a shape that cannot carry them (or carries them verbatim
#   by construction) can only fail falsely and exhausts the loop — so joined
#   fields are declared ground-truth-by-construction, never flaggable. The
#   render-measure block is context-only and explicitly forbids length
#   findings (#525's exhaustion fuel).
#   ADR-062 clause 7: prompt effect; CI pins the wording only.)
# Prompt version: v7 (#375, 2026-08-07 — check 5's SCOPE is corrected and its
#   reattached-figure case named, folded with v6's #289 clause into ONE instruction.
#   Both additions opened with the same stem — "find the profile statement it comes from
#   and read X" — which is one prompt saying one thing twice differently (ADR-062
#   clause 4). They are now a single stem, EVERY FIGURE IS READ AGAINST THE PROFILE
#   STATEMENT IT COMES FROM, with the two questions that stem answers (what is the number
#   a quantity OF; what relationship does the statement give the candidate) and v6's #283
#   boundary as the shared closer for both — this reviewer is the loop that can
#   over-correct either finding into a dropped figure.
#   No seventh check, deliberately: this reviewer's blocking surface is being de-escalated
#   (v5 demoted check 2 after run 17 exhausted the loop on 13 false findings), so #375
#   lands as a scope correction of an existing check, never a new mandate.
#   #375's miss was not a model failure. Across three charter runs (2026-07-29,
#   2026-08-02, 2026-08-06) this reviewer produced ~40 findings over 15 rounds and NOT ONE
#   named the summary — every finding addressed work[i].bullets[j] or skills. It could
#   not: check 5 read "Flag BULLETS that overstate…", and the WHAT IS BLOCKING paragraph
#   listed "summary phrasing" as minor BY DEFINITION. The vault sentence and the
#   strengthened summary were both in this prompt's input, verbatim, every round. So
#   check 5 now reads "ANY line of the draft — the SUMMARY sentence as much as a bullet",
#   and the minor entry is narrowed to "summary phrasing that does not change what is
#   claimed" (clause 4 again: one prompt may not contradict itself about one location).
#   WATCH ITEM for the charter run: check 5 is BLOCKING, and both #289 and #375 widen it.
#   If the loop starts exhausting on summary or framing findings, the disposition is v5's
#   — demote, do not tune.
#   ADR-062 clause 7: prompt effect; CI pins the wording only.)
# Prompt version: v6 (#289, 2026-08-07 — check 5 gains AGENCY vs PROXIMITY, the
#   reviewer-side half of the writer's new rule 6 clause. Charter run #5 Finding 7
#   ("supporting €19bn revenue"): correctly owned, correctly grounded, and read by a
#   blind hiring manager as unearned causation. NO new reviewer input is wired for it —
#   ``build_review_prompt`` already hands this reviewer the WHOLE profile JSON verbatim
#   as its source of truth, so the vault's own statement behind every figure is already
#   in its hands; the seam closes with an instruction to READ it for agency. "Does this
#   sentence overstate agency" is the textbook ADR-062 clause-1 judgement, so it is a
#   reviewer instruction and never a deterministic verb list. The final sentence is the
#   #283 boundary: this reviewer is the loop that could over-correct a proximity finding
#   into a dropped figure, which is the inverse defect. NEEDS CHARTER-RUN VERIFICATION
#   (ADR-062 clause 7).
# Prompt version: v3 (E049 / ADR-067 — checks 2 (ENTRY COUNT) and 3 (FACTUAL
#   MUTATIONS) DELETED, not reworded: the writer's response schema no longer
#   carries employers, roles, dates, education or entry structure — those are
#   joined deterministically from the vault at assembly, so a check on them can
#   never fail and, worse, rejects every draft for the absence of what it guards
#   (SF-WRITE.16: full retry exhaustion → the draft ships unreviewed, run 10
#   measured 5/5 exhaustion on exactly this churn, #385). See the SHAPE NOTE.)
# Prompt version: v5 (#452 / ADR-071 amended 2026-08-06 — check 2 demoted to
#   VISIBILITY-ONLY, replay-verified against the captured run-17 rounds before
#   shipping. Run 17: this reviewer raised 13 blocking findings across 5 rounds, ALL
#   false — profile-listed `confirmed` skills flagged as "fabricated keywords"; the MES
#   *project* id misread as a foreign employer although its `associated_experience`
#   names the very entry the draft rendered it under, with feedback ordering content
#   "moved" to that project id (obeyed — an invalid work entry was minted); ledger-
#   CLAIMABLE `Verpackungsindustrie` flagged as forbidden. Each regeneration destroyed
#   grounded content (the MES bullet, both OEE 61→73 figures); the loop exhausted; the
#   deterministic ADR-071 clause-3 audit then found ZERO misattribution. Ownership
#   enforcement is that audit alone; check 2 reports at "minor" for visibility. New
#   SKILLS-LIST SCOPE paragraph with two hard boundaries (denial text never grounds;
#   DO-NOT-CLAIM unchanged for skills — the adversarial pass's corrections). Replay:
#   attempts 1/2/4 re-review clean (5 samples, 0 findings); an injected IFS/BRC bullet
#   AND an injected IFS skills-list entry still block.)
# Prompt version: v4 (ADR-071 clause 2 — ROLE OWNERSHIP inserted as check 2, next
#   to FABRICATED BULLETS because it is that check's sibling: a misattributed
#   bullet is grounded and misplaced. #413/#349/#378 are one bullet — a
#   current-employer SAP fact rendered under an employer left in 2017 — that
#   survived five cv_tailoring rounds and one cv_language round untouched. The
#   word "misattributed" was already in this prompt, but only in the mandate
#   sentence and in review_severity's shared vocabulary: a LABEL for a finding
#   is not an instruction to look for one, and no numbered check named it. The
#   later checks are renumbered, not reworded.)
# v2 was US142 — named FMEA failure classes: certifications, oversell.
# Used by: services/cv.py → reviewer.review_and_refine

import json

from applire.prompts.review_severity import review_output_schema

# Shared building blocks (v8): ONE definition of the auditor role, the checks
# and the mandate; two shape doors (prose round / terminal composed round).
_AUDITOR_INTRO = """\
You are a strict CV quality auditor. Your task is to verify that a tailored CV draft
contains only claims that are grounded in the candidate's master profile.

"""

_SHAPE_NOTE_PROSE = """\
SHAPE NOTE (ADR-067): the draft contains ONLY prose — `summary`, `work` (each entry an
`id` plus `bullets`/`projects`), and `skills`. Employers, roles, dates, education,
certifications and contact details are joined deterministically from the profile and are
NOT in this draft. Their absence is correct and must never be raised as an issue.

"""

_SHAPE_NOTE_TERMINAL = """\
SHAPE NOTE — TERMINAL ROUND (ADR-076 clause 3): the subject is the COMPOSED document,
exactly as it will be delivered. Unlike the drafting rounds, employers, roles, dates,
education, languages, certifications, quantified role facts (team_size /
budget_managed / industry_context), nested projects and contact details ARE present:
they were joined verbatim from the candidate's profile by code, after the writer.
Their presence and their wording are ground truth BY CONSTRUCTION — never flag a
joined field as fabricated, missing, altered, or an issue of any kind. Your checks
apply to the authored content: the summary, work-entry bullets, project descriptions,
and the skills list.

POSITION NOTE for check 2 (role ownership): in THIS terminal round the deterministic,
id-anchored attribution audit has ALREADY run (it precedes composition); after this
round only advisory reports follow — no further enforcement round. Check 2's severity
instruction is unchanged (report ownership suspicions as "minor", never blocking —
you are reading prose and can misread entry ids), but do report them: here the
visibility itself is the last in-pipeline signal, not a preview of a later fix.

"""

_CHECKS = """\
Check for ALL of the following:
1. FABRICATED BULLETS: Every bullet in every work entry must be grounded in the CANDIDATE
   PROFILE. Flag any bullet that claims a technology, achievement, project, metric, or
   responsibility not explicitly present in the source material.
2. ROLE OWNERSHIP (misattribution) — VISIBILITY ONLY, NEVER BLOCKING: a bullet can be true of
   the candidate and still sit under the wrong employer. A deterministic, id-anchored attribution
   audit runs AFTER this loop and is the ENFORCEMENT for ownership — it resolves entry ids
   exactly; you are reading prose and can misread them. If you suspect a bullet sits under the
   wrong employer, report it with severity "minor" so it stays visible; NEVER raise it as
   "blocking", and never instruct the corrector to move, remove, or re-home content for
   ownership reasons. Facts about the profile's shape you must respect: an entry in the profile's
   `projects` section belongs to the work entry named in its `associated_experience` — a project
   id is NEVER an employer, and project content rendered under that owning work entry is NOT
   misattributed; a skill's `experience_refs` is optional metadata that may name PROJECT ids —
   it is never a grounding requirement.
3. UNGROUNDED KEYWORD GAPS: Keyword gaps may only appear in the output where they are
   explicitly supported by the candidate's work history or skills. Flag any keyword added
   without clear supporting evidence in the source material.
4. FABRICATED CERTIFICATIONS / QUALIFICATIONS: Every certification, license, degree or formal
   qualification named anywhere in the output must appear in the CANDIDATE PROFILE. Flag any
   certification or qualification not explicitly present in the source — these are the most
   damaging fabrications because a recruiter can verify them.
5. OVERSTATED CLAIM STRENGTH (oversell): Flag ANY line of the draft — the SUMMARY sentence as
   much as a bullet or a skills entry — that overstates seniority, scope, or impact beyond what
   the profile supports: e.g. "led" or "owned" where the source says "contributed to" or
   "supported", inflated team sizes, budgets, or metrics, or a more senior title than the
   profile's. A claim drawn from a truthful source but with misleading emphasis is still a defect.
   EVERY FIGURE IS READ AGAINST THE PROFILE STATEMENT IT COMES FROM. A number can be exactly
   right, AND correctly owned by the very employer and role the line sits under, and still be
   untrue as written — these are the cases every other control passes. So for each figure in the
   draft, find the profile statement behind it and ask that statement two questions:
   - A REATTACHED FIGURE (#375) — what is the number a quantity OF? Check the subject, not only
     the digits: the profile's "14 Jahren Erfahrung in der diskreten Fertigung" rendered as
     "14 Jahren Führungserfahrung" is an overstatement although "14" is correct. This is the
     hardest case in this check to see, precisely because the number verifies and only the noun
     moved, and it is likeliest in the summary, where a figure gets restated in the job's own
     vocabulary.
   - AGENCY vs PROXIMITY (#289) — what relationship does the statement actually give the
     candidate? This bites on an ORGANISATION-SCALE figure: the employer's or the unit's revenue,
     headcount, portfolio or user base, as opposed to a result the candidate's own work produced.
     Flag the line when it asserts more agency than its source does, or when it leaves the
     relationship implicit so that a figure the candidate merely stood next to reads as their own
     result.
   For both, the correction you ask for is a FRAMING one: write the profile's own subject, and
   state the relationship the profile states or name the figure as the scale of the operation the
   work happened in. NEVER ask for the figure to be removed, rounded away or made vaguer — a line
   vaguer than the truth is the opposite defect and is equally damaging.
6. KEYWORD LEDGER (ADR-048) — the source material ends with a KEYWORD LEDGER block listing
   CLAIMABLE keywords (terms the candidate truthfully supports) and a DO NOT CLAIM list (honest
   gaps NOT in the profile). Two checks:
   (a) VERIFIED COVERAGE (US213, #122): do NOT scan for absent claimable keywords yourself — a
       deterministic literal check runs before you and, when claimable keywords are verifiably
       absent, a VERIFIED COVERAGE CHECK block is appended to your input naming them with their
       profile evidence. Treat that list as ground truth. While any listed term is un-waived,
       set approved=false and name it in your issues so the writer surfaces it FROM THE GIVEN
       EVIDENCE. Your only coverage judgment is the grounding WAIVER: if surfacing a term would
       stretch beyond its stated evidence, waive it (name term + reason in feedback) — a waived
       term does not block approval. Grounding strictly OUTRANKS coverage; NEVER ask the writer
       to fabricate, stretch, or force a term that does not genuinely fit.
   (b) FORBIDDEN CLAIM: if any DO NOT CLAIM (honest-gap) concept appears in the CV presented as
       something the candidate has, has done, or knows, flag it — this is a fabrication.
7. PINNED FACT NOT DELIVERED (ADR-077): when a PINNED FACTS CHECK block is appended to your
   input, its DEMAND list is ground truth from a deterministic word-for-word scan — do not
   re-derive it. Each DEMAND entry is a BLOCKING issue: put the quote itself, in double
   quotes, in the issue text, and in `feedback` name the entry id and the quote's first
   words — the corrector re-reads the full quote from the PINNED FACTS block in its source
   and inserts it word-for-word; never ask for a paraphrase, a shortening or an extension
   of a pinned quote. PRECEDENCE — truth outranks a pin: a quote the block lists under
   LEDGER CONFLICT, or any pinned line that one of checks 1, 4, 5 or 6(b) flags, is NOT
   demanded; report it as a "minor" issue naming the pin and the check that outranks it.
   A pinned quote is the candidate's own vault text — its verbatim presence is never
   itself a finding.

SKILLS-LIST SCOPE (applies to every check above): the skills list draws on the WHOLE profile
by design — per-position ownership governs work-entry bullets only. A skill is grounded when the
profile states it AS SOMETHING THE CANDIDATE HAS OR DID: in its skills section, an entry's
responsibilities/achievements/technologies, a project, or a signature story. Never flag such a
skill as fabricated, ungrounded, or a certification, and never demand per-position evidence or
`experience_refs` for it. Two hard boundaries: text inside STATED LIMITS or any denial record
NEVER grounds anything — a concept is never affirmed by its own denial — and check 6(b) applies
to the skills list unchanged: a DO NOT CLAIM concept in the skills list is a fabrication no
matter where its text appears in the profile. A term the KEYWORD LEDGER lists as CLAIMABLE is
never a forbidden claim.
"""

#: The PROSE door's mandate paragraph — byte-for-byte the text that shipped as the tail
#: of the former ``_CHECKS_AND_MANDATE``. Split out (not edited) so the TERMINAL door can
#: carry its own; the assembled ``REVIEW_SYSTEM_PROMPT`` below is unchanged, pinned by
#: test.
_MANDATE_PROSE = """\
WHAT IS BLOCKING IN THIS PASS: a failure of one of the numbered checks above EXCEPT check 2
(role ownership is visibility-only — its enforcement is the deterministic audit that follows),
and nothing else. Those checks are the whole of your mandate — they are the ways this CV can be
untrue, misattributed, or incomplete against what the ledger required. Anything else you notice
is "minor" BY DEFINITION: bullet wording, bullet order, which achievement leads an entry,
summary phrasing that does not change what is claimed, length, repetition. You are not the CV's
editor. You are the check on whether it tells the truth.

"""

#: ADR-076 clause 9, BUILT 2026-09-04 (#545) — the whole-document questions, on the
#: TERMINAL door ONLY. They cannot be asked of a drafting round: the prose draft has no
#: composed document to read as a whole, no render measure and no letter beside it.
#:
#: NAMED checks, not a role sentence: ADR-083 measured an open "find anything that would
#: weaken this CV" mandate at 0/5 on the class that prompted it, against 5/5 for one
#: named check on the byte-identical input.
#:
#: VISIBILITY ONLY, and the severity is the decision — four reasons, each from an
#: existing record. (a) Run 17: this reviewer's last blocking widening produced 13 false
#: blocking findings in 5 rounds and destroyed grounded content through the corrector
#: while the deterministic audit found zero. (b) Every corrector round is a memoryless
#: regeneration (ADR-021 wave-6), so a stylistic rewrite mandate is a truthfulness risk.
#: (c) SF-WRITE.29, measured 2026-09-04: in the terminal round the corrector patches the
#: PROSE draft while this reviewer reads the COMPOSED document, so a composite finding is
#: frequently unactionable by the corrector anyway. (d) ADR-076 clause 7 puts the
#: "does this sound like you?" seat with the human. Visibility-only is only worth having
#: because #563(D) gave `minor` findings their first reader — the ADR-039
#: `terminal-review` check reports them at the send seat.
#:
#: ONE finding per check for the whole document (ADR-083 clause 5's container bound,
#: container = the composite): 1 of 5 arm-C runs split one cluster into three blocking
#: issues on one location, which is the ADR-021 re-litigation pathology in miniature.
_TERMINAL_CHECKS = """\
TERMINAL-ROUND CHECKS — the two questions only the finished document can answer. Both are
VISIBILITY ONLY: report what you find with severity "minor", NEVER "blocking", and never
instruct the corrector to rewrite for either. What you write here is shown to the candidate
with their document, and they decide. Report AT MOST ONE finding per check for the whole
document — each is a property of the composite, and three findings describing one pattern is
noise, not thoroughness.

8. CLAIM BALANCE — over AND under. Checks 1 and 5 look for claims the profile does not support.
   This one also looks the other way: is something the CANDIDATE PROFILE clearly supports, and
   this posting clearly wants, missing from the delivered document — or present only as a skills
   entry, or reduced to a phrase with its evidence gone? Include material the candidate scoped
   or limited in their own words: a strength named inside a stated limit ("no IFS/BRC experience,
   but ten years of ISO-9001 audit practice") is a real strength, and a document that drops it
   under-sells the candidate. Name the profile evidence that is not represented and where it
   would belong. Never ask for a skills-list entry — a tag is not evidence — and never ask for
   anything the profile does not already support.

9. VOICE — does this read as written by a person? The tell is mechanical uniformity, and every
   pattern below is one of our own quality rules applied without exception:
   - every bullet carries a number, with none left plain;
   - the summary reproduces the posting's own requirement list in the posting's order;
   - one construction repeated — "[measure] from X to Y", four times, near-identical;
   - the same figures in the same order as the cover letter, so the pair reads as one document
     and its compressed copy.
   Report the pattern and quote two examples of it. Two things you must NOT do: never propose
   removing a figure or a fact to break a pattern — a line vaguer than the truth is the worse
   defect — and never touch an unprompted admission of a gap or a limit. Blind readers name that
   admission as the strongest single reason to trust a document; it must survive any voice work.

"""

#: The TERMINAL door's mandate. The prose door's paragraph is byte-identical to what
#: shipped; this one differs in exactly two ways, both forced.
#:
#: (1) It names checks 8 and 9 as visibility-only. Without that the model must infer a
#: severity for a check that has none, and #563's own triage records this reviewer filing
#: a "blocking" on check 2 whose text says VISIBILITY ONLY — the inference is not safe.
#:
#: (2) Its closing sentence stops saying the mandate IS truthfulness. ADR-062 clause 4
#: forbids a prompt contradicting itself about one concept, and ADR-083's Context item 1
#: is the measurement that the model OBEYS such an exclusion rather than the check: the
#: one production run that noticed redundancy filed it `minor` because this paragraph
#: required exactly that.
#:
#: `repetition` STAYS on the minor list, on both doors. That is ADR-082's decision —
#: repetition is detected by the ATS audit and never repaired by the loop — and ADR-083
#: clause 3 is the open counter-proposal, not a licence taken here.
_MANDATE_TERMINAL = """\
WHAT IS BLOCKING IN THIS PASS: a failure of one of the numbered checks above EXCEPT checks 2, 8
and 9, and nothing else. Those three are VISIBILITY ONLY: check 2's enforcement is the
deterministic audit that follows, and checks 8 and 9 are the candidate's own call, shown to them
with the document. Anything else you notice is "minor" BY DEFINITION: bullet wording, bullet
order, which achievement leads an entry, summary phrasing that does not change what is claimed,
length, repetition — and so is everything you find under checks 8 and 9. You are not the CV's
editor: you never rewrite it, and a style observation never makes the writer run again. Checks 1
and 3-7 are whether this CV tells the truth; checks 8 and 9 are whether it represents the
candidate. This is the only round that sees the finished document, so both get answered here.

"""


_SCHEMA_AND_CLOSER = review_output_schema(
    issue_hint="specific issue with work_history index and description — empty array if nothing found",
    feedback_hint="concise instruction for the tailoring agent to correct the BLOCKING issues — empty string if there are none",
) + """

Keep `feedback` concise and *referential*: name the offending location (work_history index,
field, section) and state what is wrong. Do NOT quote or paste source passages — the corrector
re-reads the candidate profile itself (ADR-021 amended 2026-06-29). The one exception is
check 7's pinned quote, which goes into the ISSUE text in double quotes; the feedback
still names the entry id and the first words only."""

# ONE definition of the checks; TWO doors, differing only in the shape note and the
# mandate (v9 kept them byte-identical, which is exactly why clause 9 could not land
# without this split — the shared blob ended by declaring clause 9's questions "minor"
# BY DEFINITION, and adding a check without touching that would have been the ADR-062
# clause-4 self-contradiction ADR-083 measured the model obeying).
REVIEW_SYSTEM_PROMPT = (
    _AUDITOR_INTRO + _SHAPE_NOTE_PROSE + _CHECKS + "\n" + _MANDATE_PROSE + _SCHEMA_AND_CLOSER
)

TERMINAL_REVIEW_SYSTEM_PROMPT = (
    _AUDITOR_INTRO
    + _SHAPE_NOTE_TERMINAL
    + _CHECKS
    + "\n"
    + _TERMINAL_CHECKS
    + _MANDATE_TERMINAL
    + _SCHEMA_AND_CLOSER
)


def build_review_prompt(source_material: str, tailored_json: dict) -> str:
    """Build the reviewer user prompt for CV tailoring.

    Args:
        source_material: The candidate's master profile JSON serialised as a string.
                         This is the only authoritative source of facts.
        tailored_json: The tailored CV JSON produced by the tailoring agent.
    """
    return (
        "Review this tailored CV draft against the candidate's source material.\n\n"
        f"CANDIDATE PROFILE (source of truth):\n{source_material}\n\n"
        f"TAILORED CV DRAFT (prose only — see SHAPE NOTE):\n"
        f"{json.dumps(tailored_json, ensure_ascii=False, indent=2)}\n\n"
        "Does the draft contain only claims grounded in the source material — no "
        "fabricated bullets, no ungrounded keywords, no overstated claims? "
        "Return your review JSON."
    )


def build_terminal_review_prompt(
    source_material: str,
    composed_json: dict,
    *,
    page_count: int | None,
    target: int,
    condensation_exhausted: bool,
) -> str:
    """Build the TERMINAL-round reviewer user prompt (#538, ADR-076 clause 3).

    Args:
        source_material: The candidate's master profile JSON serialised as a string
                         (plus the ledger/limit blocks the generation path folds in) —
                         the same source the drafting rounds review against.
        composed_json: The COMPOSED document — ``TailoredCVData`` dumped after the
                       full deterministic tail, i.e. the delivered artifact.
        page_count: The real render measure (pages) from the measure-and-condense
                    loop, or ``None`` when measurement failed (the round still runs;
                    the verdict simply lacks the measure).
        target: The resolved target page count (ADR-051).
        condensation_exhausted: True when the deterministic condense loop could not
                    reach the target — stated for honest context, never as a mandate.
    """
    if page_count is not None:
        measure = f"measured pages: {page_count}, target: {target}"
        if condensation_exhausted:
            measure += (
                " — condensation exhausted: the deterministic cuts could not reach "
                "the target"
            )
    else:
        measure = f"render measure unavailable for this round (target: {target} pages)"
    return (
        "Terminal review: the document below is the COMPOSED artifact exactly as it "
        "will be delivered (see SHAPE NOTE — TERMINAL ROUND).\n\n"
        f"CANDIDATE PROFILE (source of truth):\n{source_material}\n\n"
        f"COMPOSED CV (the delivered document):\n"
        f"{json.dumps(composed_json, ensure_ascii=False, indent=2)}\n\n"
        f"RENDER MEASURE (context only): {measure}. Length is enforced by a "
        "deterministic condense mechanism that has already run — NEVER raise a "
        "length, page-count or 'too long / too short' finding.\n\n"
        "Does the composed document contain only claims grounded in the source "
        "material — no fabricated bullets, no ungrounded keywords, no overstated "
        "claims? When naming an issue, identify the work entry by its company name "
        "as well as its index (this composed shape includes joined entries). "
        "Return your review JSON."
    )
