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

# Prompt version: v4
# Used by: services/cv.py → LLMProvider.aparse_json + reviewer.review_and_refine
# Changes from v1: Rules 1, 3, 5 hardened against hallucination;
#                  Rule 6 added (entry count constraint);
#                  Rule 7 added (language, was Rule 6);
#                  build_retry_prompt added for review layer retries.
# Changes from v2: Rule 7 no longer delegates language detection to the model —
#                  build_user_prompt now takes output_language (resolved
#                  deterministically from job_analyses.jd_language, ADR-038)
#                  and emits an explicit OUTPUT LANGUAGE directive.
# Changes from v3: Rule 8 added (US169 / FMEA JF-M-6.2) — claim-strength calibration
#                  (generator-side oversell prevention; the reviewer already detects it).
# Changes from v4: Rule 4 amended (#235, Tiramisu founder-acceptance F3) — the summary
#                  must lead with the KEYWORD LEDGER's top claimable concepts instead of
#                  defaulting to whatever career phase dominates the raw profile text.
# Added in retry-refinement work: CV_TAILORING_REFINEMENT_PROMPT — refinement-mode
#                  system prompt used on review-loop retries (patch the previous tailored
#                  CV JSON; the reviewer quotes profile content when needed).

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from applire.services.cv_budget import BudgetResult

SYSTEM_PROMPT = """\
You are an expert DACH career consultant specialising in writing tailored German CVs (Lebenslauf).
Your task is to rewrite a candidate's profile to maximise fit for a specific job, following these rules:

1. Rephrase and re-emphasise bullets already in CANDIDATE PROFILE to highlight relevance to the job.
   Use strong action verbs. Do NOT add new achievements, technologies, projects, or metrics that are
   not explicitly present in CANDIDATE PROFILE. Quantify only where CANDIDATE PROFILE explicitly
   provides numbers or metrics — never infer or invent figures.
2. Preserve the reverse-chronological order of work_history entries exactly as provided in
   CANDIDATE PROFILE — do NOT reorder entries. Relevance is expressed through bullet selection
   and phrasing, not by changing the sequence.
3. Filter and reorder the skills list to lead with skills explicitly required in the job description.
   Keyword gaps may ONLY be incorporated if they are explicitly demonstrated in the candidate's
   work history or skills list. If a keyword gap has no explicit basis in CANDIDATE PROFILE, omit it.
4. Write a concise professional summary (2–3 sentences, third person) tailored to the role.
   When a KEYWORD LEDGER block is present below, LEAD the summary with the JD's top CLAIMABLE
   concepts (the terms the profile evidence backs) — the summary is the first thing a reviewer
   reads and must truthfully aim at THIS job. Do not let an earlier, no-longer-central
   specialism dominate the summary when the ledger shows stronger, more current support
   elsewhere (#235 — a summary for a role the ledger backs must not read as still positioned
   for the candidate's previous career).
5. Keep all factual data EXACTLY as provided — company names, roles, dates, degrees, technologies,
   project names, and metrics. Do NOT invent, infer, or embellish ANY fact not present in
   CANDIDATE PROFILE. When in doubt, leave it out.
6. The number of work_history entries in your output must equal exactly the number in CANDIDATE
   PROFILE. Do not add, remove, or split entries.
7. Output language: write ALL prose — summary, every work_history bullet, and skill names — in the
   OUTPUT LANGUAGE stated in the user message. If CANDIDATE PROFILE is written in a different
   language, you MUST translate its content; translating is NOT inventing (Rules 1 and 5 protect
   facts, not the source language). Skill and discipline PHRASES are ordinary language and MUST be
   translated (e.g. "Brand Identity", "Art Direction", "Motion Design", "Team Leadership"). Only
   genuinely language-invariant PROPER NOUNS stay unchanged: company names, product/tool/framework
   names (Figma, Adobe Photoshop, Python, AWS), dates and metrics. Never copy bullets or skills
   verbatim in the source language and never mirror the language of CANDIDATE PROFILE or the job
   description when it differs from the OUTPUT LANGUAGE.
8. Claim-strength calibration — do NOT inflate. A bullet drawn from a truthful source but with
   misleading emphasis is still a defect: the candidate will be exposed in the interview. Stay
   within the seniority, scope, and impact the CANDIDATE PROFILE actually evidences:
   - Mirror the source's verb strength. If the profile says "supported", "contributed to",
     "assisted with", or "was part of", do NOT upgrade it to "led", "owned", "drove", or
     "spearheaded". Use a leadership verb only where the profile states the candidate led.
   - Never invent or enlarge team sizes, budgets, user counts, or other magnitudes.
   - Never assign a more senior role/title than the profile states.
   Strong action verbs (Rule 1) means vivid, not inflated — re-emphasise relevance without
   overstating the candidate's actual depth.
9. When a ROLE BULLET BUDGETS block is present in the user message (E042/US237,
   ADR-051 §3), treat each listed role's "max" as a per-role bullet-count ceiling, not a
   quota to fill: prioritise the most JD-relevant achievements within that ceiling, and
   condense older/less relevant roles toward a single line as their budget instructs. The
   whole document targets the stated page count — the budgets exist to get you there
   without a separate trim pass.

Respond ONLY with a valid JSON object matching this schema — no markdown, no explanations:

{
  "contact": {
    "name": string,
    "email": string or null,
    "phone": string or null,
    "location": string or null,
    "linkedin": string or null
  },
  "summary": string,
  "work_history": [
    {
      "company": string,
      "role": string,
      "start_date": string,
      "end_date": string or null,
      "bullets": [string]
    }
  ],
  "skills": [string],
  "education": [
    {
      "institution": string,
      "degree": string,
      "field": string,
      "start_date": string,
      "end_date": string or null
    }
  ],
  "languages": [
    {"language": string, "level": string}
  ]
}"""


def build_user_prompt(
    job_analysis: dict,
    profile: dict,
    keyword_gaps: list[str],
    critical_gaps: list[str],
    output_language: str = "de",
    keyword_ledger: list[dict] | None = None,
    budget: "BudgetResult | None" = None,
) -> str:
    language_name = "GERMAN" if output_language == "de" else "ENGLISH"
    # ADR-048 §8 / US200: the Keyword Ledger splits the JD's expectations into claimable
    # terms (carry their profile evidence — surface where supported) and honest gaps
    # (never claim). Grounding strictly outranks coverage. Empty → adds nothing.
    from applire.services.keyword_ledger import render_ledger_prompt_block

    ledger_block = render_ledger_prompt_block(keyword_ledger)
    ledger_section = f"{ledger_block}\n\n" if ledger_block else ""
    # E042/US237, ADR-051 §3: per-role bullet-count ceilings computed deterministically
    # BEFORE generation, so the model aims at the target page count directly rather than
    # relying on a post-hoc trim. Empty/None budget → adds nothing (back-compat).
    from applire.services.cv_budget import render_budget_table

    budget_block = render_budget_table(budget) if budget is not None else ""
    budget_section = f"{budget_block}\n\n" if budget_block else ""
    return (
        "Tailor the candidate's profile for the job below.\n\n"
        f"OUTPUT LANGUAGE: {language_name} — write the summary, all work_history bullets, and "
        f"skills in {language_name}, translating from the profile's language where needed; keep "
        "company names, product names, dates, and metrics unchanged.\n\n"
        f"JOB ANALYSIS:\n{json.dumps(job_analysis, ensure_ascii=False, indent=2)}\n\n"
        f"CANDIDATE PROFILE:\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        f"{ledger_section}"
        f"{budget_section}"
        f"KEYWORD GAPS (incorporate only where explicitly supported by profile):\n"
        f"{json.dumps(keyword_gaps, ensure_ascii=False)}\n\n"
        f"CRITICAL GAPS (acknowledge in summary if applicable):\n"
        f"{json.dumps(critical_gaps, ensure_ascii=False)}\n\n"
        "Return the tailored CV JSON."
    )


def build_retry_prompt(previous_draft: dict, feedback: str, source: str) -> str:
    """Build the retry user prompt after a reviewer rejection of a tailored CV.

    The candidate profile (``source``) IS re-sent so the corrector can re-read the
    ground truth (ADR-021 amended 2026-06-29 / US194). The reviewer's critique is now
    *referential* — it points at the offending entry/field rather than quoting the
    profile verbatim — so the corrector must consult the source to fix a fabricated
    skill or a mutated fact correctly. This keeps the reviewer's output small and
    cap-safe while still grounding the correction.
    """
    return (
        "A quality review of your previous CV tailoring identified the following issues. "
        "Patch the JSON to address every issue, using the CANDIDATE PROFILE as the only "
        "source of truth, and return the corrected object.\n\n"
        f"REVIEW FEEDBACK:\n{feedback}\n\n"
        f"CANDIDATE PROFILE (source of truth):\n{source}\n\n"
        f"PREVIOUS OUTPUT:\n{json.dumps(previous_draft, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the corrected tailored CV JSON."
    )


CV_TAILORING_REFINEMENT_PROMPT = """\
You are a tailored CV corrector. You receive (1) a previously-tailored CV JSON and
(2) a quality reviewer's critique listing specific issues (fabricated skills, achievements
not present in the source profile, etc.). Patch the JSON to address every issue.

Rules:
- The previous tailored CV is your working draft. Modify it to resolve the reviewer's issues.
- Do not invent skills, achievements, or experience. The CANDIDATE PROFILE is provided as
  the source of truth — re-read it to ground any correction. Restrict your changes to
  deletions, nullifications, and rewordings supported by that profile.
- Preserve all fields that the reviewer did not flag.
- Output ONLY the corrected TailoredCVData JSON in the same schema as the input — no
  markdown, no commentary.
"""
