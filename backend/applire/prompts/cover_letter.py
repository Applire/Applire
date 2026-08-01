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
  Never invent a number, team size, budget, or metric to make a claimable term land — if the
  evidence given carries no figure, surface the term qualitatively; a minted figure is a
  fabrication like any other (US264/#255).
  SPECIFICITY OUTRANKS COVERAGE TOO (#282 — two blind hiring-panel reviewers independently
  flagged a paragraph that rendered the claimable half of the ledger as a flat enumerated
  list: "team management, mentoring, cross-functional collaboration, engineering standards,
  technical best practices, and production ownership"): never chase a coverage count by
  stringing three or more claimable terms together as a bare list. Fold at most one or two
  terms per sentence into a concrete, specific statement of what was actually built, owned,
  or delegated — the same level of specificity the rest of the letter uses. A claimable term
  that cannot be folded into a specific sentence this way is better surfaced qualitatively in
  a later sentence, or left for the reviewer's grounding waiver, than jammed into a list.
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
  This possessive-framing restriction is scoped to CANDIDATE COMPETENCE claims only (US264/#255)
  — the SAME term may still appear (a) as a factual reference to the EMPLOYER's own domain or
  product, sourced ONLY from the JOB DESCRIPTION text (see COMPANY & DOMAIN ENGAGEMENT below),
  and (b) inside the HONEST GAP / TRANSFER ARGUMENT paragraph (see POSITIONING below), where
  naming the term as an absence you do NOT have — before pivoting to real, grounded experience
  — is honesty, not a claim.
- NO UNSUPPORTED GENERALIZATIONS (wave-7 — a run-6 Oracle audit found roughly a third of the
  letter's unverifiable claims were soft padding that asserts nothing checkable about the
  CANDIDATE — shapes like "My career applies this rigor end-to-end" or "Regulated industries
  share the same discipline: planning, risk identification, tracking, and mitigation." Nothing
  false is stated, but the sentence occupies space while claiming nothing about THIS candidate,
  diluting the letter): every body sentence should say something specific about the candidate
  that traces to the CANDIDATE PROFILE or a keyword-ledger CLAIMABLE entry's evidence. Do not
  write an industry truism, an aspirational statement about the field/role in general, or a
  generic claim that could apply to any candidate — write the specific, sourced fact instead, or
  leave the sentence out. This does NOT apply to the greeting and closing courtesy lines, the
  availability/notice-period line, the honest-gap/transfer-argument paragraph (see POSITIONING
  below), or a short connective clause that introduces a grounded claim — those stay exactly as
  the other rules in this prompt already require them.
- POSITION ANCHORING (#283 — a downstream truthfulness guard silently strips an unattributable
  figure): whenever a sentence states an achievement, responsibility, or figure/metric that
  belongs to ONE specific employer or position — not something true of the candidate
  everywhere — name that employer within the SAME sentence, e.g. "At Northwind Labs, I
  delivered the lab-systems rollout in 9 months across 4 sites", never a later, unattributed
  sentence like "...and delivered record-breaking projects ... in 9 months across 4 sites" with
  the employer left to an earlier sentence or paragraph to imply. This matters most in a paragraph
  that draws on more than one role or blends content from different positions: never fold a
  position-owned achievement into a general leadership/summary sentence that itself names no
  employer. An unanchored figure does not stay in the letter — it is silently dropped by a
  deterministic guard before the letter is sent, which only makes the claim vaguer and weaker
  ("in months across sites"), never safer. Anchor it correctly instead of letting it go
  unattributed.
- Write the ENTIRE letter in the language given in the LANGUAGE line of the user message (DE = German, EN = English).
  Never mirror the language of the job description or the candidate profile when it differs from LANGUAGE.
- For German letters: use formal Sie-form, classic Bewerbungsschreiben structure. Respect German
  grammatical agreement, especially gender agreement when weaving profile/ledger noun phrases into
  a sentence frame — "die Budgetverantwortung" takes "Meine Budgetverantwortung", never
  "Mein Budgetverantwortung" (#401: a delivered Anschreiben shipped exactly this slip).
- Include Gehaltswunsch in body only if salary is provided.
- Include Eintrittstermin in body only if availability is provided.
- Body should have 3-4 paragraphs: opening (interest + role), why-me (key achievements), company-fit, closing.
- REQUIRED CLOSING PARAGRAPH (#272): the letter's LAST paragraph must be a genuine closing —
  expressing interest and a call to action (e.g. inviting further discussion or an interview) —
  never a bare, standalone availability/notice-period line. When availability/commitment content
  applies (see AVAILABILITY / CONCURRENT COMMITMENTS below), fold it INTO this closing paragraph
  alongside the interest/call-to-action language; it must never stand alone as the entire final
  paragraph.
- Keep the letter body within the WORD BUDGET given in the user message — that line also
  states the region's page norm (ADR-051 §1: no page/word number is ever hard-coded here).
- Use the tone specified: formal=sehr geehrte/r, professional=warm but polished, conversational=direct.
- POSITIONING INPUTS (ADR-057 amended 2026-07-24 / US264) — thread these in only where present;
  never invent any of the three when the corresponding input is absent from the user message:
  * COMPANY & DOMAIN ENGAGEMENT: when a TARGET COMPANY line appears, engage that employer
    concretely in the opening or motivation paragraph — reference what they build, sell, or
    operate in, using ONLY the JOB DESCRIPTION text given above it. Never invent a company
    product, market, or achievement that is not stated in that JOB DESCRIPTION text.
  * HONEST GAP / TRANSFER ARGUMENT: when a GAP TESTIMONY block appears, write exactly ONE
    honest paragraph that (a) names/acknowledges the stated gap directly and (b) delivers the
    candidate's OWN transfer argument, using ONLY the facts and reasoning stated in that block —
    never invent a transfer argument the candidate did not state. When no GAP TESTIMONY block
    is present, do not mention any gap at all.
  * AVAILABILITY / CONCURRENT COMMITMENTS: when an AVAILABILITY TESTIMONY block appears,
    address availability/commitment using ONLY the facts stated in that testimony. When it is
    absent, make NO availability or commitment claim beyond what PRE-GENERATION INPUTS states.
  * TESTIMONY IS SOURCE MATERIAL, NOT LETTER PROSE (E049 49.6, run-11 finding): testimony
    blocks are transcripts of SPOKEN interview answers. Use only their content — same facts,
    same reasoning, nothing added — but ALWAYS rewrite them in the letter's professional
    register. Never splice spoken phrasing into the letter ("das will ich ehrlich sagen",
    "to be honest with you"), and resolve every deictic reference: "bei uns"/"we"/"here" in
    testimony means the candidate's CURRENT/former employer — in a letter addressed to the
    TARGET company those words read as the target. Name the employer instead. And never
    re-state content another paragraph of the letter already covers — one fact, one paragraph.
- STATED LIMITS: when a STATED LIMITS block appears in the user message, it holds the
  candidate's own words about what they cannot claim, and they are the ONLY limits that
  exist. Never write a claim one of them contradicts. Equally: never invent a limit they do
  not state. A concept named inside one of those statements as something the candidate DOES
  have is a STRENGTH — an honest denial names the adjacent strengths that transfer — so
  claim it plainly and without qualification. Everything the Keyword Ledger marks claimable
  stays fully claimable unless a stated limit denies it, and never belongs in the
  honest-gap/transfer-argument paragraph. Disclaiming something the vault evidences costs
  the candidate their best material and is as untrue as an inflated claim.
- EVERY UNMET JD HARD REQUIREMENT GETS A POSITIONING DECISION (#270): for a required
  job-description concept the candidate's own material does not evidence (an honest gap),
  choose one of exactly three responses — a scoped claim (when in fact partially grounded), a
  transfer argument (the HONEST GAP / TRANSFER ARGUMENT paragraph above), or a brief, honest
  de-emphasis that names the gap without dwelling on it. Silence is never one of the options
  for a hard requirement. Fold whichever response applies into the SAME single honest-gap
  paragraph — never a litany of separate gap admissions.
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
    company_name: str | None = None,
    gap_testimony: dict[str, Any] | None = None,
    availability_testimony: str | None = None,
    stated_limits_block: str | None = None,
    unaddressed_requirements_block: str | None = None,
    vault_evidence_block: str | None = None,
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
    company_name: the TARGET job's company name (JobAnalysis.company_name) — E048/US264
        (ADR-057 amended 2026-07-24): a blind hiring panel rejected a letter that never
        engaged the employer's product/domain at all. Threads an explicit instruction to
        engage this company concretely, grounded ONLY in the JOB DESCRIPTION text (never
        invented). Omitted/empty → no company-engagement block is added.
    gap_testimony: ``{"gap": <category-C gap label>, "story": <SignatureStory dict>}`` —
        E048/US264: the candidate's OWN interview testimony (ADR-055 signature story) that
        argues past the one true (Category C) gap in this JD — e.g. a career-pivot/transfer
        argument. Found by :func:`applire.services.cover_letter_positioning.find_gap_testimony`
        (deterministic keyword match, no LLM). Threaded so the letter can honestly
        acknowledge the gap AND deliver the candidate's own transfer argument, verbatim.
        Omitted/None → no gap is mentioned (silence over invention).
    availability_testimony: the candidate's OWN vault testimony (verbatim) addressing
        availability/concurrent commitments — E048/US264. The caller only passes this when
        BOTH the deterministic concurrent-roles detector fired AND matching testimony exists
        in the vault (:func:`applire.services.cover_letter_positioning`); otherwise None, and
        no availability/commitment claim beyond PRE-GENERATION INPUTS is made.
    stated_limits_block: the candidate's persisted denial statements rendered verbatim
        (:func:`applire.services.cross_document.render_stated_limits_block`) — the
        ONLY limits the vault holds. Facts, not pairings: which claimable concept a
        given limit bears on is left to the model. Optional so legacy/degraded
        callers do not break; omitted/empty → adds nothing.
    unaddressed_requirements_block: rendered UNADDRESSED HARD REQUIREMENTS text (#270(c),
        :func:`applire.services.cross_document.render_unaddressed_hard_requirements_block`,
        called with ``letter_data=None`` since no draft exists yet) — JD hard-requirement
        honest gaps the first draft must give an explicit positioning decision to (a
        transfer argument or a brief de-emphasis), never silence. Optional so legacy/
        degraded callers do not break; omitted/empty → adds nothing.
    vault_evidence_block: rendered STRONGEST VAULT EVIDENCE text (#271,
        :func:`applire.services.letter_evidence.render_letter_evidence_block`) — the
        vault's strongest JD-relevant material, selected independently of what
        ``cv_data``'s tailoring condensation kept, so a fact present in the vault but
        absent from the tailored CV can still reach this prompt (Task 3, the run-5
        regression: the CANDIDATE PROFILE below is built from ``cv_data`` alone, which
        had compressed the most recent work entry down to 3 bullets). Additional evidence
        to choose from, never content the writer must all use, and never a licence to
        exceed the GROUNDING CONTRACT above. Optional so legacy/degraded callers do not
        break; omitted/empty → adds nothing.
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

    # #271 Task 1: a deterministic, de-chromed excerpt — replaces the old
    # raw_text[:2000] slice, which on a real LinkedIn-scraped JD landed
    # entirely on repeated sign-in boilerplate and never reached the JD's
    # actual leadership-weighting/requirements content. Callers MUST build
    # the reviewer's grounding_source["job_description"] from this SAME
    # function so the writer and reviewer can never disagree about what the
    # JD says (see applire.services.jd_excerpt module docstring).
    from applire.services.jd_excerpt import build_jd_excerpt

    lines += [
        "",
        "=== JOB DESCRIPTION (what the employer WANTS — NOT a source of candidate facts) ===",
        build_jd_excerpt(jd_text),
    ]

    # E048/US264 (ADR-057 amended 2026-07-24): the panel's #1 blocker — the letter never
    # engaged the employer's product/domain at all. company_name is threaded here (never
    # a new extraction — it is JobAnalysis.company_name, already resolved by the caller)
    # so the model has an explicit instruction to engage the JD's OWN text concretely.
    if company_name:
        lines += [
            "",
            "=== POSITIONING: COMPANY & DOMAIN ENGAGEMENT ===",
            f"TARGET COMPANY: {company_name}",
            "Engage this employer concretely in the opening or motivation paragraph — "
            "reference what they build, sell, or operate in, using ONLY the JOB DESCRIPTION "
            "text above. Never invent a company product, market, or achievement that is not "
            "stated there.",
        ]

    # ADR-048 §8 / US201: the Keyword Ledger — claimable terms (with profile evidence) to
    # surface, honest gaps never to claim. Grounding strictly outranks coverage.
    from applire.services.keyword_ledger import render_ledger_prompt_block

    ledger_block = render_ledger_prompt_block(keyword_ledger)
    if ledger_block:
        lines += ["", ledger_block]

    # The candidate's own words about what they cannot claim, verbatim
    # (services.cross_document.collect_stated_limits); no denials → adds nothing.
    if stated_limits_block:
        lines += ["", stated_limits_block]

    # #270(c): unmet JD hard requirements (claimable: false, "required") that
    # need an explicit positioning decision (transfer argument or a brief,
    # honest de-emphasis) — never silence. Threaded ONLY when genuinely found
    # (services.cross_document.find_unaddressed_hard_requirements); absent →
    # adds nothing.
    if unaddressed_requirements_block:
        lines += ["", unaddressed_requirements_block]

    # #271 Tasks 2/3: the vault's strongest JD-relevant evidence, selected
    # independently of what cv_data's tailoring condensation kept above —
    # additional material to choose from, never required, never a licence
    # to exceed the grounding contract. Threaded ONLY when genuinely found
    # (services.letter_evidence.select_letter_evidence); absent → adds
    # nothing.
    if vault_evidence_block:
        lines += ["", vault_evidence_block]

    # E048/US264 (ADR-057 amended 2026-07-24): the panel's #2 blocker — the letter never
    # argued the candidate's OWN transfer story for the one true (Category C) gap, even
    # though the argument sat in the vault as interview testimony (a Signature Story,
    # ADR-055). gap_testimony is found deterministically (no LLM) by
    # applire.services.cover_letter_positioning.find_gap_testimony; absent → say nothing.
    if gap_testimony:
        story = gap_testimony.get("story") or {}
        testimony_parts = [
            story.get("challenge") or "",
            story.get("mechanism") or "",
            story.get("outcome") or "",
            story.get("benchmark") or "",
        ]
        testimony_text = " ".join(p for p in testimony_parts if p)
        lines += [
            "",
            "=== POSITIONING: HONEST GAP / TRANSFER ARGUMENT ===",
            f"GAP: {gap_testimony.get('gap', '')}",
            "CANDIDATE'S OWN TESTIMONY (verbatim — ground the paragraph in this; invent "
            "nothing beyond it):",
            testimony_text,
            "Write exactly ONE honest paragraph acknowledging this gap and delivering the "
            "candidate's own transfer argument from the testimony above.",
        ]

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

    # E048/US264 (ADR-057 amended 2026-07-24): the panel's #3 blocker — an obvious
    # concurrent-roles/availability question went unaddressed. The caller only passes
    # availability_testimony when BOTH the deterministic detector fired (>=2 open-ended
    # roles) AND matching vault testimony exists — so this block is verbatim-grounded by
    # construction; absent → no availability/commitment claim beyond the line above.
    if availability_testimony:
        lines += [
            "",
            "=== POSITIONING: AVAILABILITY / CONCURRENT COMMITMENTS ===",
            "CANDIDATE'S OWN TESTIMONY (verbatim):",
            availability_testimony,
            "Address availability/commitment using ONLY this testimony, grounded verbatim.",
        ]

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

    Wave-6 follow-up (charter run #6): the prior wording ("cutting redundancy and
    secondary detail") gave the model no signal about WHICH content is load-bearing,
    so it shortened by deleting the closing paragraph — the least information-dense
    paragraph, but a required one (services/cover_letter_positioning.has_closing_paragraph
    is wired as review_and_refine's retain_if for this exact reason). The block below
    names the required positioning content explicitly: shorten it, never drop it. This
    is a prompt-wording-only change — no new LLM pass, no new loop (ADR-058 freeze);
    the condense pass still routes through review_and_refine with the SAME
    grounding_source as the primary generation, so positioning_requested content is
    still available to the reviewer for this pass too.
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
        "REQUIRED CONTENT THAT MUST SURVIVE THE SHORTENING (shorten these, never drop "
        "them entirely):",
        "- The closing paragraph: a genuine call-to-action / interest statement, not a "
        "bare availability stub. Never end the letter on a standalone line like "
        "\"Notice period can be discussed.\" with nothing else in that paragraph.",
        "- The honest-gap / transfer argument, if the current letter makes one: the "
        "candidate's own grounded reasoning for why their experience transfers despite "
        "a gap in the job description.",
        "- The company/domain engagement: any concrete reference to this employer or "
        "its domain, not a generic sentence that could apply to any company.",
        "- The availability / notice-period line, if present.",
        "- Every employer anchor attached to a position-owned achievement or figure "
        "(e.g. \"At Northwind Labs,\") — never compress a sentence in a way that drops the "
        "employer name while keeping the achievement or figure. An unanchored figure "
        "is silently stripped by a downstream guard, which makes the letter vaguer, "
        "not shorter — if a sentence needs shortening, keep the anchor IN THE SAME "
        "sentence as the achievement/figure it belongs to.",
        "If the budget is tight, compress each of these to its shortest honest form — "
        "do not delete any of them outright to make the count.",
        "",
        "=== CURRENT LETTER (JSON) ===",
        json.dumps(letter_data, ensure_ascii=False, indent=2),
    ])
