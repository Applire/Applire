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

2. RELEVANCE. Within each entry, lead with what this job cares about. Strong, concrete verbs; each bullet should say what the candidate did and what changed because of it. Prefer one specific bullet over two vague ones.

3. MEASURED OVER PROJECTED. Where the profile carries both a target and a measured outcome for the same initiative, write the measured one. "Reduced scrap from 4,1 % to 2,3 %" beats "aimed to reduce scrap". A quantified, measured achievement is the strongest evidence an entry has — under a tight bullet budget it is the LAST thing to cut, never the first.

4. UNMET AND PARTLY-MET REQUIREMENTS. "Do not claim it" is not a complete instruction; each kind gets a different action.
   - ADJACENT (the KEYWORD LEDGER names an adjacent capability for a required concept): give the ADJACENT capability real prominence — a skills-list position and, where the profile supports one, a bullet showing it in use. Present it as itself, on its own merits. Never write the JD's own term as though the candidate held it, and never pair the two as equivalents.
   - EXPLICITLY DENIED, or a plain UNKNOWN gap: omit it. A CV is not the place to disclose a gap — that is the cover letter's job. Never claim it, and never gesture at it with a hedge like "familiar with" or "exposure to".
   An absent required keyword is a truthful outcome. An asserted one the candidate cannot back is a fabrication, and the worst failure mode here.

5. SUMMARY. 2–3 sentences, third person, aimed at THIS role. Lead with the KEYWORD LEDGER's top claimable concepts — the terms the profile's evidence actually backs. Do not let an earlier, no-longer-central specialism dominate because it happens to fill more of the profile.

6. CLAIM STRENGTH. Vivid, never inflated — a bullet drawn from a truthful source but with misleading emphasis is still a defect, and the candidate is the one exposed in the interview. Mirror the profile's own verb strength: if it says "supported", "contributed to" or "was part of", do not upgrade to "led", "owned" or "drove". Never enlarge a team size, budget, scope or user count, and never imply a more senior role than the profile states.

7. SKILLS. Return the skills this JD cares about that the profile supports, most relevant first. A skill with no basis in the profile is omitted, however loudly the JD asks for it.
   - One entry per competence. Never list an acronym, its expansion and a translated form as separate entries — pick the one form a DACH recruiter for this role would expect.
   - Every skill you name in a bullet must also appear in the skills list.

8. LANGUAGE. Write all prose — summary, bullets, skill entries — in the OUTPUT LANGUAGE stated in the user message, translating from the profile's language where needed. Translating is not inventing.
   The test for any single term: **does it NAME something, or DESCRIBE something?** A name is copied exactly, never translated, expanded or "corrected" — products, systems, standards, certifications, methods with proper names, employers, job titles (GxP, MES, SMED, ISO 9001, SAP PP, Kaizen — and equally one you do not recognise). A description is ordinary language and is translated (Team Leadership, Art Direction, Prozessoptimierung). Where a term is a name wrapped in descriptive words, translate only the descriptive words and leave the name untouched.

9. BULLET BUDGETS. Each entry's "max" in ROLE BULLET BUDGETS is a ceiling, not a quota. Prioritise the most JD-relevant achievements within it, and condense an older or less relevant role toward a single strong line rather than padding it out.

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
) -> str:
    """Build the single-call CV tailoring user prompt.

    E049 (#383 prompt-side half): the former CRITICAL GAPS block is gone — it
    unconditionally instructed the writer to "acknowledge in summary" the very
    gaps rule 4 forbids disclosing, and under active budgets the tension made
    the writer self-censor its strongest quantified evidence (#384). Gap
    handling is rule 4's job, driven by the KEYWORD LEDGER.

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
        f"{stated_limits_section}"
        f"{scope_section}"
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
  invented, dropped, or reassigned.
- Preserve all fields that the reviewer did not flag.
- Output ONLY the corrected prose JSON in the same schema as the input — no markdown,
  no commentary.
"""
