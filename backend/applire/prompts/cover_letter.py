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

"""LLM prompt builder for cover letter generation.

The system prompt instructs the model to output strictly valid JSON
matching the letter_data schema. The user prompt provides all context.
"""

import json
from typing import Any

SYSTEM_PROMPT = """You are an expert DACH career coach writing a professional Bewerbungsschreiben (German cover letter).
Output ONLY a single valid JSON object. No markdown, no explanation, no prose outside the JSON.

The JSON must match this schema exactly:
{
  "header": {
    "name": "string",
    "address": "string",
    "phone": "string or null",
    "email": "string or null",
    "photo_url": "string or null"
  },
  "recipient": {
    "name": "string or null",
    "title": "string or null",
    "company": "string or null",
    "address": "string or null",
    "date": "null — the system inserts the letter date after generation; always output null"
  },
  "body": {
    "paragraphs": ["opening paragraph", "main paragraph 1", "main paragraph 2", "closing paragraph"]
  },
  "signature": {
    "closing": "null — the system overwrites the sign-off with a language-routed label after generation; always output null",
    "name": "string"
  }
}

Rules:
- GROUNDING CONTRACT (this letter is signed and sent — false statements are the worst failure):
  Every factual claim in the body must trace to the CANDIDATE PROFILE section of the user message
  (or to the candidate's own PRE-GENERATION INPUTS). Do NOT invent or fabricate facts. In particular:
  * Never invent or alter dates, durations, or tenure.
  * Never invent employers, companies, job titles, degrees, certifications, or named projects.
  * Never invent achievements, metrics, team sizes, budgets, or technologies the candidate has not
    stated, and never overstate the seniority/impact the candidate data supports.
  * The JOB DESCRIPTION states what the employer WANTS — it is NOT a source of NEW facts about
    the candidate. Express motivation and fit using only what the candidate data actually
    contains; do not claim the candidate already has a requirement the candidate data does not show.
  When in doubt, leave it out — write a sincere letter from the real material rather than a
  stronger letter from invented material.
- KEYWORD LEDGER (ADR-048) — when a KEYWORD LEDGER appears in the user message, grounding
  strictly OUTRANKS coverage. The CLAIMABLE entries each carry the profile EVIDENCE that
  supports them: surface those terms from the candidate's own material where the evidence
  fits, never as a stretch and without over-stuffing. The DO-NOT-CLAIM entries are honest
  gaps absent from the profile — never present them as something the candidate has or knows.
- CLAIM FRAMING (the why-me / achievements paragraph is where fabrication creeps in — this is
  the cover-letter equivalent of the CV's keyword-gap rule):
  You may assert a competency, skill, tool, domain, or "track record" ONLY where it traces to a
  specific BULLET in the CANDIDATE PROFILE (or to a CLAIMABLE keyword-ledger entry's evidence).
  A JOB DESCRIPTION requirement term, and ANY DO-NOT-CLAIM concept, must NEVER appear in a
  possessive / competence framing — never "I have", "I have done", "I have a proven track record
  in", "experienced in", "proven track record in", "fluent in", "expertise in", "skilled in",
  "background in", "well-versed in", or any equivalent that asserts the candidate already
  possesses it. For a requirement the candidate's profile does NOT evidence, the ONLY permitted
  framing is forward-looking MOTIVATION — eagerness to grow into, contribute to, or develop the
  area. When the profile does not evidence a strength, do not assert it; express interest instead.
- Write the ENTIRE letter in the language given in the LANGUAGE line of the user message (DE = German, EN = English).
  Never mirror the language of the job description or the candidate profile when it differs from LANGUAGE.
- For German letters: use formal Sie-form, classic Bewerbungsschreiben structure.
- Include Gehaltswunsch in body only if salary is provided.
- Include Eintrittstermin in body only if availability is provided.
- Body should have 3-4 paragraphs: opening (interest + role), why-me (key achievements), company-fit, closing.
- Keep the letter body within the WORD BUDGET given in the user message — that line also
  states the region's page norm (ADR-051 §1: no page/word number is ever hard-coded here).
- Use the tone specified: formal=sehr geehrte/r, professional=warm but polished, conversational=direct.
"""


def build_cover_letter_prompt(
    cv_data: dict[str, Any],
    jd_text: str,
    pre_gen_inputs: dict[str, Any],
    detected_language: str,
    keyword_ledger: list[dict[str, Any]] | None = None,
    role_title: str | None = None,
    word_budget: int | None = None,
    letter_pages: int | None = None,
) -> str:
    """Build the user-turn prompt for the LLM.

    Returns a single string to pass as the user message.
    cv_data: the tailored_data dict from GeneratedCV (contact, summary, work_history, skills).
    jd_text: job.raw_text
    pre_gen_inputs: dict with keys salary, availability, motivation, tone, recipient_name, recipient_company.
    detected_language: 'de' or 'en'
    keyword_ledger: the GapAnalysis Keyword Ledger (ADR-048 / US201). Claimable terms carry
        their profile evidence (surface where supported); honest gaps are do-not-claim.
        Omitted/empty → adds nothing.
    role_title: the TARGET job's role title (JobAnalysis.role_title) — F3 (blind PQ
        blocker): without this, the letter has no explicit anchor for which role it is
        applying to and the LLM sourced a title from the candidate's own CV/summary
        instead (e.g. the candidate's CURRENT title), producing a letter that targets
        the wrong position. Optional so legacy/degraded callers do not break.
    word_budget: feedforward body-word budget from REGION_NORMS (#177, ADR-051 §6
        amended) — the CV's guarantee shape, extended to letters. Optional so
        legacy/degraded callers do not break.
    letter_pages: the region's page norm (REGION_NORMS[region].letter_pages) —
        interpolated into the WORD BUDGET line so SYSTEM_PROMPT never has to
        hard-code "one page" (ADR-051 §1 review finding). Optional so legacy/
        degraded callers do not break; only used when word_budget is also given.
    """
    salary = pre_gen_inputs.get("salary", "")
    availability = pre_gen_inputs.get("availability", "")
    motivation = pre_gen_inputs.get("motivation", "")
    tone = pre_gen_inputs.get("tone", "formal")
    recipient_name = pre_gen_inputs.get("recipient_name", "")
    recipient_company = pre_gen_inputs.get("recipient_company", "")

    # #189: the fallback cv_data path is profile.profile_json, whose schema uses
    # `personal_info` (not `contact`) — so name/email/phone were read blank and the
    # letter's header.name + signature.name came out empty. Read either schema
    # (mirrors services/cv.py:_contact_from_profile).
    contact = cv_data.get("contact") or cv_data.get("personal_info") or {}
    summary = cv_data.get("summary", "")
    skills = cv_data.get("skills", [])
    work_history = cv_data.get("work_history", [])

    # E037 PQ #1: feed the letter GROUNDED profile material instead of a thin snippet.
    # The old top-3-entries / 2-bullets condensation starved the why-me paragraph of real
    # achievements, so the LLM sourced them from the JD's requirement language (fabrication).
    # Carry the real work history (up to 6 entries × 6 bullets) and a generous skill list so
    # achievements are drawn from the candidate's actual record; the JD is trimmed harder
    # below to rebalance the profile-vs-JD ratio. Token budget stays sane.
    work_snippet = ""
    for entry in work_history[:6]:
        work_snippet += f"- {entry.get('role', '')} at {entry.get('company', '')} ({entry.get('start_date', '')}–{entry.get('end_date', 'present')})\n"
        for bullet in entry.get("bullets", [])[:6]:
            work_snippet += f"  • {bullet}\n"

    skills_snippet = ", ".join(
        s if isinstance(s, str) else s.get("name", "")
        for s in skills[:20]
    ) if skills else "—"

    lines = [
        f"LANGUAGE: {detected_language.upper()}",
        f"TONE: {tone}",
        "",
        "=== CANDIDATE PROFILE ===",
        f"Name: {contact.get('name', '')}",
        f"Email: {contact.get('email', '')}",
        f"Phone: {contact.get('phone', '')}",
        f"Location: {contact.get('location', '')}",
        f"Summary: {summary}",
        f"Key skills: {skills_snippet}",
        "Recent experience:",
        work_snippet.strip(),
    ]

    # #177 / ADR-051 §6 amended: feedforward body-word budget from REGION_NORMS —
    # the CV's guarantee shape, extended to letters. Placed before the JD block so
    # it reads as a constraint on the CANDIDATE PROFILE material, not the JD.
    # letter_pages is interpolated here (never hard-coded) so SYSTEM_PROMPT can
    # point at this line instead of stating a literal page count (ADR-051 §1).
    if word_budget:
        if letter_pages:
            page_word = "page" if letter_pages == 1 else "pages"
            budget_line = (
                f"WORD BUDGET: at most {word_budget} words of body text — "
                f"the letter must fit the region's {letter_pages}-{page_word} norm."
            )
        else:
            budget_line = (
                f"WORD BUDGET: at most {word_budget} words of body text — "
                "the letter must fit the region's page norm."
            )
        lines += ["", budget_line]

    lines += [
        "",
        "=== JOB DESCRIPTION (what the employer WANTS — NOT a source of candidate facts) ===",
        jd_text[:2000],  # trimmed (E037 PQ #1): rebalance profile-vs-JD so achievements come from history
    ]

    # ADR-048 §8 / US201: the Keyword Ledger — claimable terms (with profile evidence) to
    # surface, honest gaps never to claim. Grounding strictly outranks coverage.
    from applire.services.keyword_ledger import render_ledger_prompt_block

    ledger_block = render_ledger_prompt_block(keyword_ledger)
    if ledger_block:
        lines += ["", ledger_block]

    lines += [
        "",
        "=== PRE-GENERATION INPUTS ===",
    ]

    # F3 (blind PQ blocker): state the target role and company as explicit facts, and
    # frame this letter as an application FOR THIS ROLE — never for a title mentioned
    # elsewhere in the candidate's own profile (e.g. their current job title).
    if role_title:
        target_company = recipient_company or "the company named in the job description"
        lines.append(
            f"TARGET ROLE: This letter is an application FOR THIS ROLE — \"{role_title}\" — "
            f"at {target_company}. The candidate is applying for THIS role, not for any "
            f"title that appears elsewhere in the candidate's own CANDIDATE PROFILE "
            f"(e.g. their current or past job title). Refer to the role by this exact title."
        )

    lines += [
        f"Recipient name: {recipient_name or '(extract from JD or use generic salutation)'}",
        f"Recipient company: {recipient_company or '(extract from JD)'}",
    ]

    if salary:
        lines.append(f"Gehaltswunsch (salary expectation): {salary}")
    if availability:
        lines.append(f"Eintrittstermin (availability/notice period): {availability}")
    if motivation:
        lines.append(f"Personal motivation (incorporate naturally): {motivation}")

    lines += [
        "",
        "Generate the cover letter JSON now.",
    ]

    return "\n".join(lines)


def build_condense_prompt(
    letter_data: dict[str, Any], word_budget: int, page_count: int, letter_pages: int,
) -> str:
    """One bounded condense-regenerate (#177, ADR-051 §6 amended): same JSON shape,
    same facts, fewer words. Omission-only in spirit — nothing new is claimed.

    Letters have no deterministic bullet-cut model the way CVs do (ADR-051 §4), so
    this is a scoped LLM rewrite — an ADR-approved deviation, bounded to exactly one
    pass by the caller (never a loop).

    letter_pages: the region's page norm (REGION_NORMS[region].letter_pages) — ADR-051
        §1 forbids hard-coding a page number in the prompt text; the caller always
        passes the norm value (currently 1 for DACH, but never literal here).
    """
    page_word = "page" if letter_pages == 1 else "pages"
    return "\n".join([
        f"The following cover letter rendered to {page_count} pages; "
        f"it must fit on {letter_pages} {page_word}.",
        f"Rewrite it to AT MOST {word_budget} words of body text.",
        "Keep the same JSON structure, the same language, tone, recipient and factual claims.",
        "Shorten by cutting redundancy and secondary detail — NEVER add new facts,",
        "achievements, or claims that are not in the original letter.",
        "",
        "=== CURRENT LETTER (JSON) ===",
        json.dumps(letter_data, ensure_ascii=False, indent=2),
    ])
