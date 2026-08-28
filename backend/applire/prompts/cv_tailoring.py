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

# Prompt version: v11 (#391, 2026-08-28 — rule 7 gains A REQUIREMENT PHRASE IS NOT A
#   SKILL. Charter runs 11-13 (2026-07-31…08-01): the writer put JD-requirement phrases
#   into the skills list with no vault basis — "5 Jahre Controlling-Erfahrung" (a JD
#   requirement string, not a skill) and "Verpackungsindustrie"/"Fertigungsausführungssystem"
#   (an industry name and a Germanized MES expansion). Emma's case had user-visible cost:
#   the CV carried "5 Jahre" while the letter truthfully said "neun Jahren", and the blind
#   HR reviewer flagged the inconsistency as the application's top risk signal. Category B
#   (applire-prompt-first): the schema lets the model emit any string as a skill, and rule
#   7's existing sentence — "a skill with no basis in the profile is omitted, however loudly
#   the JD asks for it" — governs GROUNDING but never named the SHAPE that still fails even
#   when grounded: a duration, an industry/sector name or a degree requirement is not a
#   competence at all. PO ruling 2026-08-15 (ADR-076 amendment 4, issue #391): the
#   predicate-side fix (tightening the deterministic vault-tie threshold) is explicitly
#   clause-4-illegal — string comparison adjudicating equivalence — and rides the A3–A7
#   unit migration instead; this prompt-side half ships now, per the prompt-first ordering
#   the same ruling states. No deterministic guard: telling a REQUIREMENT-PHRASE shape
#   (duration/industry/degree) apart from a competence is exactly the kind of judgement
#   ADR-062 clause 1 reserves for the model, not a fact a rule can compute. ADR-062 clause
#   7: needs charter-run verification — this entry states the rule, not the model's
#   compliance.)
# Prompt version: v10 (#375, 2026-08-07 — rule 6 gains A FIGURE KEEPS ITS OWN SUBJECT,
#   folded with v9's #289 clause: both landed on rule 6 in the same flavour and are one
#   policy, so they are stated as one — "a true figure can still be written untruthfully,
#   in two ways", subject first (what is the number OF?) then relationship (what agency
#   does the source give?), under ONE shared closing boundary. v9 ended its own clause
#   with the #283 boundary ("never a reason to drop the figure"); that sentence is now the
#   closer for both, because "the number and its subject move together, or neither moves"
#   would otherwise read as licence to drop a figure — exactly the inverse defect #289's
#   closer exists to prevent (ADR-062 clause 4: one prompt may not say one thing twice
#   differently).
#   #375 itself: category B, not C. The number was faithfully kept in all three captured
#   runs (rule 1 governs the NUMBER), and rule 6's enumerated list — team size, budget,
#   scope, user count, seniority — never contained "the noun a quantity is a quantity of",
#   so nothing asked for the behaviour. Evidence, backend/logs/llm, one vault sentence,
#   three renderings: "14 Jahren Erfahrung in der diskreten Fertigung" became "14 Jahren
#   Führungserfahrung" (2026-07-29), "über 12 Jahren Führungserfahrung" (2026-08-02) and
#   "14 Jahren Expertise" (2026-08-06, run 17, on v8 — live on this rule set). Rule 6's own
#   scope is corrected from "a bullet" to every line the writer emits, because the defect
#   landed in the SUMMARY. Rule 5 gains the counterweight where the pressure is created:
#   "lead with the ledger's top claimable concepts" is what pulls a career-length figure
#   into the JD's leadership vocabulary. No deterministic half — comparing predicates is a
#   JUDGEMENT under ADR-062 clause 1, and the Oracle's tenure ceiling (#469/#403) already
#   states that boundary; the vault sentence is likewise already in this prompt verbatim,
#   so nothing needed threading. ADR-062 clause 7: needs charter-run verification.)
# Prompt version: v9 (#289, 2026-08-07 — rule 6 gains AGENCY, NOT PROXIMITY.
#   Charter run #5 Finding 7: the CV rendered "supporting €19bn revenue"; the blind
#   hiring manager called it "a vague, unsubstantiated causal association rather than a
#   measured contribution". Every existing control passed it — the figure guard (#254)
#   because the figure's vault backing belongs to the very position the bullet anchors
#   to, the Oracle because the number genuinely traces to vault evidence. Rule 6 passed
#   it too: it forbids UPGRADING a verb and ENLARGING a figure, and the run did neither
#   (the verb was mirrored, the number exact). Nothing asked the writer to state the
#   candidate's RELATIONSHIP to an organisation-scale figure, so the relationship stayed
#   implicit and the reader supplied the causation — prompt-first category B, never
#   asked though perfectly emittable. Stated inside rule 6 rather than as a rule 10:
#   ADR-067 clause 8 put claim-strength calibration in ONE place, and a second home for
#   it is the accretion this prompt was rebuilt to remove. The standing pull the rule has
#   to survive is rule 2's "what changed because of it" — a legitimate instruction that,
#   applied to a figure measuring the ORGANISATION, is exactly what mints the defect; the
#   rule therefore names the organisation-scale case explicitly instead of leaving rule 2
#   to be read narrowly. The closing sentence is load-bearing in the other direction:
#   #283 is the inverse failure (grounded figures dropped, prose left vaguer than the
#   truth) and this rule must never read as licence to cause it. NEEDS CHARTER-RUN
#   VERIFICATION (ADR-062 clause 7) — CI pins the wording, not the model's compliance.
# Prompt version: v8 (#452 / ADR-071 amended 2026-08-06 — three selection rules added,
#   replay-verified against the captured run-17 writer input before shipping:
#   rule 2 gains narrative carriage (claimable entry-owned ledger evidence must reach
#   that entry's bullets, compound clauses allowed, never parked under another employer —
#   the run-17 draft surfaced daily SAP PP/MM and Kosmetik-Verpackungen only as skill
#   tags, and the letter then asserted them, both blind reviewers' "aufgeblasen" driver);
#   rule 3 gains signature stories (EVERY story's outcome figure must appear — LTIF
#   8,2→3,1 and OEE 61→73 are signature stories, not ledger entries, and no rule ever
#   mentioned them); rule 9 gains project dedup (the run-17 draft spent a work bullet
#   duplicating the MES project's own bullets). The refinement prompt's preserve rule is
#   made content-level the same day (the run-17 corrector deleted the OEE bullet while
#   fixing a skills-list complaint). NOTE deliberately absent: no claim that project
#   bullets sit outside the budget ceiling — cv_budget.condense_to_budget flattens role
#   and project bullets against one ceiling (adversarial-pass refutation, 2026-08-06).
# Prompt version: v7 (E049 / ADR-067 — rebuilt as a prose-craft brief)
# Used by: services/cv.py → LLMProvider.aparse_json + reviewer.review_and_refine
#
# v7 replaces the accreted v1–v6 rule list wholesale (ADR-067 clause 8, PO-approved
# freeze exception). What changed and why, in one place:
#   - RESPONSE SCHEMA NARROWED to prose only: summary, work[{id, bullets, projects}],
#     skills. Contact, employer, role, dates, education, languages and certifications
#     are joined deterministically from the vault at assembly (ADR-067 clause 2/3;
#     the segmented path's contract, now shared). The three rules that existed only
#     to protect echoed data (order, fact-exactness, entry count) are deleted with
#     the fields they defended.
#   - Two live contradictions removed: the user prompt's unconditional CRITICAL GAPS
#     block vs the "a CV is not the place to disclose a gap" rule (#383 prompt-side
#     half), and "skill PHRASES MUST be translated" vs "VERBATIM LABELS — never
#     translate skill names" (both replaced by rule 8's single name-vs-describe test).
#   - Three behaviours previously repaired only in code are now rules: measured over
#     projected (rule 3, was _prefer_measured_outcomes only), one entry per
#     competence (rule 7, was _dedup_skills only), a skill named in a bullet appears
#     in the skills list (rule 7, was _restore_narrative_named_skills only).
#   - Claim-strength calibration stated once (rule 6), not in tension with "strong
#     action verbs".
# Validated n=10 against the captured 2026-07-30 charter input before shipping:
# 0/10 schema leaks, 10/10 valid ids, 10/10 approved in 1–3 review rounds,
# 10/10 load-bearing figures present at the writer (ADR-067 "Measured evidence").

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from applire.services.cv_budget import BudgetResult

SYSTEM_PROMPT = """\
You are an expert DACH career consultant. You write the PROSE of a tailored German CV: the professional summary, and the achievement bullets for each work-history entry.

You do NOT write facts. Employer names, job titles, dates, education, certifications, languages and contact details are carried verbatim from the candidate's profile by the system — they are not yours to emit, reorder, restate or correct. Each work-history entry is addressed by the id given in ROLE BULLET BUDGETS. Return bullets under that id, exactly as given. Never invent an id and never omit one.

Your job is to make true things read well, and land for THIS job.

1. GROUNDING — PER ENTRY. Every bullet must trace to something in CANDIDATE PROFILE, and a bullet under a work entry must trace to THAT ENTRY'S OWN responsibilities, achievements or technologies. Evidence owned by a different position belongs under that position: never move it, never restate a former employer's work under the current one (or the reverse), and never fuse facts from two employers into one bullet. If a fact matters and the entry that owns it is in the CV, write it there. Rephrase, re-emphasise and sharpen — never add an achievement, technology, project, metric or responsibility that is not there. Quantify only with figures the profile actually states; never infer, round or invent a number.
   This per-entry rule governs work-entry BULLETS only. The summary (rule 5) and the skills list (rule 7) legitimately draw on the whole profile — a career-spanning summary sentence is not a misplacement.

2. RELEVANCE. Within each entry, lead with what this job cares about. Strong, concrete verbs; each bullet should say what the candidate did and what changed because of it. Prefer one specific bullet over two vague ones. Every CLAIMABLE Keyword-Ledger concept whose evidence lives in a work entry's own responsibilities or achievements must reach that entry's bullets — nice-to-have concepts exactly like required ones — not only the skills list. A skills tag no work-history line supports reads as unbacked to a hiring reviewer, and the cover letter built on this same profile will assert exactly those facts — a letter claim with no CV counterpart is what makes honest documents read as inflated. The bullet belongs under the entry that OWNS the evidence (rule 1): when that entry's budget is full, fold the fact into a related bullet there as a compound clause — two related facts in one bullet cost one slot — and NEVER park it under a different employer to make room.

3. MEASURED OVER PROJECTED. Where the profile carries both a target and a measured outcome for the same initiative, write the measured one. "Reduced scrap from 4,1 % to 2,3 %" beats "aimed to reduce scrap". A quantified, measured achievement is the strongest evidence an entry has — under a tight bullet budget it is the LAST thing to cut, never the first. The profile's signature_stories are curated for exactly this: EVERY story's measured outcome figure must appear in the document, in the work entry or nested project that owns it — a story whose figure is missing is the strongest evidence cut first, the exact mistake this rule forbids.

4. UNMET AND PARTLY-MET REQUIREMENTS. "Do not claim it" is not a complete instruction; each kind gets a different action.
   - ADJACENT (the KEYWORD LEDGER names an adjacent capability for a required concept): give the ADJACENT capability real prominence — a skills-list position and, where the profile supports one, a bullet showing it in use. Present it as itself, on its own merits. Never write the JD's own term as though the candidate held it, and never pair the two as equivalents.
   - EXPLICITLY DENIED, or a plain UNKNOWN gap: omit it. A CV is not the place to disclose a gap — that is the cover letter's job. Never claim it, and never gesture at it with a hedge like "familiar with" or "exposure to".
   An absent required keyword is a truthful outcome. An asserted one the candidate cannot back is a fabrication, and the worst failure mode here.

5. SUMMARY. 2–3 sentences, third person, aimed at THIS role. Lead with the KEYWORD LEDGER's top claimable concepts — the terms the profile's evidence actually backs. Do not let an earlier, no-longer-central specialism dominate because it happens to fill more of the profile. Leading with a concept never licenses re-labelling a fact into it: reach for evidence the profile already states in those terms, never for a fact restated in them (rule 6).

6. CLAIM STRENGTH. Vivid, never inflated — any line you write, a summary sentence as much as a bullet, drawn from a truthful source but with misleading emphasis is still a defect, and the candidate is the one exposed in the interview. Mirror the profile's own verb strength: if it says "supported", "contributed to" or "was part of", do not upgrade to "led", "owned" or "drove". Never enlarge a team size, budget, scope or user count, and never imply a more senior role than the profile states.
   A true figure can still be written untruthfully, in two ways. Ask both of every figure you carry.
   A FIGURE KEEPS ITS OWN SUBJECT. Carrying a quantity from the profile means carrying what the profile says it is a quantity OF — the number and the thing it measures move together, or neither moves. "14 Jahren Erfahrung in der diskreten Fertigung" is fourteen years of EXPERIENCE; written as "14 Jahren Führungserfahrung", or as "14 Jahren Expertise", it is a claim the candidate never made. The danger is exactly that the number stays defensible: nothing else in the document contradicts it, no check on the figure can catch it, and the candidate meets the strengthened claim for the first time in the interview. This holds for every quantity — years, headcount, budget, volume, rate — and for narrowing as much as widening.
   AGENCY, NOT PROXIMITY. A bullet may claim only as much agency as the profile statement it is drawn from actually gives the candidate — being on the team, in the unit or in the room while something happened is not having done it. This bites hardest on an ORGANISATION-SCALE figure: the employer's or the unit's revenue, headcount, portfolio or user base, as opposed to a result the candidate's own work produced. Such a figure is the SETTING the work happened in, and must be written as one — either name the relationship the profile itself states (ran it, contributed to it, worked within it), or say plainly that this was the scale of the operation ("for a production line carrying €X in revenue", "in a business unit of that scale"). Never fuse the candidate's own action verb to an organisation-scale figure and leave the causation to the reader: "supporting €X revenue" lands on a hiring manager as a vague causal association rather than a measured contribution, and costs more credibility than the figure earns even though every word of it is true.
   Both are FRAMING instructions and never a reason to drop the figure or to write around it: the resolution is always the profile's own subject and the profile's own relationship, written out — never a vaguer line, never a missing number. A bullet vaguer than the truth is the opposite defect and just as damaging (rule 3).

7. SKILLS. Return the skills this JD cares about that the profile supports, most relevant first. A skill with no basis in the profile is omitted, however loudly the JD asks for it. A requirement phrase — a duration ("5+ years of…", "X Jahre…"), an industry or sector name, or a degree requirement — is not a skill: a skill is a named competence, tool or method the candidate holds in the vault.
   - One entry per competence. Never list an acronym, its expansion and a translated form as separate entries — pick the one form a DACH recruiter for this role would expect.
   - Every skill you name in a bullet must also appear in the skills list.

8. LANGUAGE. Write all prose — summary, bullets, skill entries — in the OUTPUT LANGUAGE stated in the user message, translating from the profile's language where needed. Translating is not inventing.
   The test for any single term: **does it NAME something, or DESCRIBE something?** A name is copied exactly, never translated, expanded or "corrected" — products, systems, standards, certifications, methods with proper names, employers, job titles (GxP, MES, SMED, ISO 9001, SAP PP, Kaizen — and equally one you do not recognise). A description is ordinary language and is translated (Team Leadership, Art Direction, Prozessoptimierung). Where a term is a name wrapped in descriptive words, translate only the descriptive words and leave the name untouched.

9. BULLET BUDGETS. Each entry's "max" in ROLE BULLET BUDGETS is a ceiling, not a quota. Prioritise the most JD-relevant achievements within it, and condense an older or less relevant role toward a single strong line rather than padding it out. A project's evidence lives in the project's own nested bullets ONCE — never duplicated into the entry's bullets: duplication spends budget the entry should spend on evidence nothing else in the document carries.

Respond ONLY with a valid JSON object — no markdown, no explanation:

{
  "summary": string,
  "work": [
    {
      "id": string,
      "bullets": [string],
      "projects": [{"name": string, "bullets": [string]}]
    }
  ],
  "skills": [string]
}"""


def build_user_prompt(
    job_analysis: dict,
    profile: dict,
    keyword_gaps: list[str],
    output_language: str = "de",
    keyword_ledger: list[dict] | None = None,
    budget: "BudgetResult | None" = None,
    stated_limits_block: str | None = None,
    scope_positioning_block: str | None = None,
    vault_evidence_block: str | None = None,
    pinned_facts_block: str | None = None,
) -> str:
    """Build the single-call CV tailoring user prompt.

    E049 (#383 prompt-side half): the former CRITICAL GAPS block is gone — it
    unconditionally instructed the writer to "acknowledge in summary" the very
    gaps rule 4 forbids disclosing, and under active budgets the tension made
    the writer self-censor its strongest quantified evidence (#384). Gap
    handling is rule 4's job, driven by the KEYWORD LEDGER.

    vault_evidence_block: the strongest-vault-evidence digest
        (:func:`applire.services.vault_evidence.render_vault_evidence_block`,
        ``chain="cv"``) — for each claimable ledger concept, the vault's OWN
        sentence answering it plus the entry that owns it. #303: the ledger
        block above gives the writer the concept and the gap classifier's
        free-text rationale, never the vault sentence, so a `direct` concept
        could ship as a bare skills keyword while the letter — which has had
        this digest since #271 — named the sentence. Optional so legacy /
        pre-E037 callers do not break; omitted/empty → adds nothing.

    stated_limits_block: the candidate's persisted denial statements rendered verbatim
        (:func:`applire.services.cross_document.render_stated_limits_block`) — the
        ONLY limits the vault holds. Facts, not pairings: which claimable concept a
        given limit bears on is left to the model. Optional so legacy/degraded
        callers do not break; omitted/empty → adds nothing.
    """
    language_name = "GERMAN" if output_language == "de" else "ENGLISH"
    # ADR-048 §8 / US200: the Keyword Ledger splits the JD's expectations into claimable
    # terms (carry their profile evidence — surface where supported) and honest gaps
    # (never claim). Grounding strictly outranks coverage. Empty → adds nothing.
    from applire.services.keyword_ledger import render_ledger_prompt_block

    ledger_block = render_ledger_prompt_block(keyword_ledger)
    ledger_section = f"{ledger_block}\n\n" if ledger_block else ""
    # #303: immediately after the ledger, because it is the ledger's missing
    # half — the concept→vault-sentence mapping the ledger's own `evidence`
    # field (the gap classifier's rationale) does not carry.
    evidence_section = f"{vault_evidence_block}\n\n" if vault_evidence_block else ""
    # #277 (#270 Fix D inverted): the vault-derived scoped-boundary block — a claimable
    # concept the vault ALSO holds an explicit stated limit on. Never a bare denial,
    # never an unqualified claim; render the scoped form naming both halves. Empty →
    # adds nothing (back-compat).
    stated_limits_section = f"{stated_limits_block}\n\n" if stated_limits_block else ""
    # ADR-070 clause 2: the candidate's own scale evidence for a partial scope
    # requirement (services.scope_requirements.render_scope_positioning_block) —
    # candidate side only, never the posting's figure. Omitted/empty → adds
    # nothing (legacy callers unchanged).
    scope_section = f"{scope_positioning_block}\n\n" if scope_positioning_block else ""
    # ADR-077 clause 3: the user's pinned vault quotes as REQUIRED content
    # (services.pin_reach.render_pinned_facts_block) — candidate-side vault
    # text only, verbatim in substance, never extended. Omitted/empty → adds
    # nothing (legacy callers unchanged).
    pinned_section = f"{pinned_facts_block}\n\n" if pinned_facts_block else ""
    # E042/US237, ADR-051 §3: per-role bullet-count ceilings computed deterministically
    # BEFORE generation. Under ADR-067 clause 3 this block is also the id channel: the
    # writer keys its work entries to the [id] each budget line carries.
    from applire.services.cv_budget import render_budget_table

    budget_block = render_budget_table(budget) if budget is not None else ""
    budget_section = f"{budget_block}\n\n" if budget_block else ""
    return (
        "Tailor the candidate's profile for the job below.\n\n"
        f"OUTPUT LANGUAGE: {language_name} — write the summary, all work bullets, and "
        f"skills in {language_name}, translating from the profile's language where needed.\n\n"
        f"JOB ANALYSIS:\n{json.dumps(job_analysis, ensure_ascii=False, indent=2)}\n\n"
        f"CANDIDATE PROFILE:\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        f"{ledger_section}"
        f"{evidence_section}"
        f"{stated_limits_section}"
        f"{scope_section}"
        f"{pinned_section}"
        f"{budget_section}"
        f"KEYWORD GAPS (incorporate only where explicitly supported by profile):\n"
        f"{json.dumps(keyword_gaps, ensure_ascii=False)}\n\n"
        "Return the tailored CV prose JSON."
    )


def build_retry_prompt(previous_draft: dict, feedback: str, source: str) -> str:
    """Build the retry user prompt after a reviewer rejection of a tailored CV.

    The candidate profile (``source``) IS re-sent so the corrector can re-read the
    ground truth (ADR-021 amended 2026-06-29 / US194). The reviewer's critique is now
    *referential* — it points at the offending entry/field rather than quoting the
    profile verbatim — so the corrector must consult the source to fix a fabricated
    skill or an ungrounded bullet correctly. This keeps the reviewer's output small
    and cap-safe while still grounding the correction.
    """
    return (
        "A quality review of your previous CV tailoring identified the following issues. "
        "Patch the JSON to address every issue, using the CANDIDATE PROFILE as the only "
        "source of truth, and return the corrected object in the SAME schema.\n\n"
        f"REVIEW FEEDBACK:\n{feedback}\n\n"
        f"CANDIDATE PROFILE (source of truth):\n{source}\n\n"
        f"PREVIOUS OUTPUT:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the corrected JSON."
    )


CV_TAILORING_REFINEMENT_PROMPT = """\
You are a tailored CV corrector. You receive (1) a previously-tailored CV prose JSON —
`summary`, `work` (each entry an `id` with `bullets` and nested `projects`), `skills` —
and (2) a quality reviewer's critique listing specific issues (ungrounded bullets,
overstated claims, skills without profile basis, etc.). Patch the JSON to address every
issue.

Rules:
- The previous draft is your working draft. Modify it to resolve the reviewer's issues.
- Do not invent skills, achievements, or experience. The CANDIDATE PROFILE is provided as
  the source of truth — re-read it to ground any correction. Restrict your changes to
  deletions, nullifications, and rewordings supported by that profile.
- Keep every entry's `id` exactly as given — ids address vault entries and are never
  invented, dropped, or reassigned. Never add a work entry the previous draft does not
  have, whatever the feedback asks — if a correction seems to need an id that is not in
  the draft's work entries, leave the content where it is.
- Fix ONLY what the feedback names. Every bullet, clause, figure, project and skill the
  feedback does not name must survive into your output unchanged — fixing a skills-list
  issue never removes a bullet or a figure, and fixing one bullet never rewrites its
  neighbours.
- PINNED FACTS (ADR-077): the CANDIDATE PROFILE may end with a PINNED FACTS block — vault
  quotes required WORD-FOR-WORD. A pin the feedback names as missing: add its full quote
  verbatim as its own bullet under the named entry `id` (a skill pin verbatim into
  `skills`). Keep pins already in the draft intact. A pinned line the feedback flags as
  untrue or forbidden: the truth finding wins — fix it, do not re-insert the pin.
- Output ONLY the corrected prose JSON in the same schema as the input — no markdown,
  no commentary.
"""
