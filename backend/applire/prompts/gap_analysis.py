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

# Prompt version: v6 (ADR-061 amended, 2026-08-02 — charter run 15 #427):
#   - The RATIONALE and the PUBLISHED EVIDENCE are separate fields. v5's schema
#     described `reason` as "the evidence" while the PROFICIENCY-CEILING rule below
#     told the model to name the declared proficiency in that same `reason` — one
#     field serving as both the grading audit trail and the text the document
#     writers are handed as the candidate's claim. `services/gap.py` copies `reason`
#     onto ledger `evidence` (by design, ADR-048) and four generation-facing
#     renderers emit `evidence` verbatim into BOTH document chains, as writer input
#     and as reviewer demand. Run 15: the ledger carried "SAP (basic proficiency) —
#     unter der geforderten Tiefe", the letter reviewer demanded "Surface … 'SAP'
#     (basic proficiency)", and the condense corrector complied — publishing
#     "Grundkenntnisse in SAP PP/MM" over the round-1 writer's correct "arbeite
#     täglich mit SAP PP und MM", for a candidate the vault shows using those
#     modules daily and serving as Key-User. Both blind reviewers named it their
#     only inconsistency. The mirror image of #304's inflation, on the same skill.
#     The ceiling now goes to `classification_note`, which `gap.py` never reads.
#
# Prompt version: v5 (ADR-069, 2026-08-01 — charter run 12 #397):
#   - PROFICIENCY-CEILING rule: `Skill.proficiency` had been present in the prompt
#     payload since #304/#317 with NO rule weighing it — run 12 classified a skill
#     declared "SAP (Anwender)" as "direct" for the JD's depth-worded "Sicherer
#     Umgang mit SAP", reasoning from years-since-first-mention alone (LLM log
#     2026-07-31 18:05:42). Declared proficiency is a CEILING (ADR-061 lineage).
#   - SCOPE REQUIREMENTS block (ADR-069 clauses 1-2): quantified scope requirements
#     (team size, budget) arrive as a separate labelled section carrying the JD's
#     stated bar verbatim AND the candidate's typed vault values WITH their field
#     semantics; the model judges sufficiency (a deterministic >= would compare
#     different quantities — direct reports vs total span) and must cite the vault
#     entry for any "direct". Code enforces the fail-closed floor afterwards.
#
# Prompt version: v4
# Used by: services/gap.py → LLMProvider.aparse_json
#
# v4 changes vs v3 (F4, blind PQ 2026-07-02 — negation stance):
#   - STANCE rule: a candidate denial ("no hands-on Azure experience") is evidence
#     AGAINST a skill → "gap", never "direct"/"partial"
#   - COMPOUND-requirement rule: surface_forms of a claimable concept may list ONLY
#     supported technologies — an unsupported/denied token gets its own "gap" entry
#   - user prompt: profile `metadata` (the enrichment audit trail) is no longer dumped
#     as unlabeled profile text; interview-sourced records render under a labeled
#     CANDIDATE INTERVIEW STATEMENTS section that states the denial rule
#
# v3 changes vs v2 (ADR-035):
#   - match_score REMOVED from the schema — it is computed deterministically in Python
#   - LLM returns `classifications`: one bucket (direct/partial/gap) per JD requirement
#   - LLM must classify ONLY the requirements it is given; it may not invent requirements

import json

from applire.services.gap_inference import PreClassification

SYSTEM_PROMPT = """\
You are an expert career coach specialised in the DACH (Germany, Austria, Switzerland) job market.
Your task is to produce a three-category gap analysis by classifying each job requirement against \
a candidate's profile. You do NOT compute a numeric score.

You will receive:
  1. JOB ANALYSIS — structured extract of the job description
  2. CANDIDATE PROFILE — structured master profile (skills may include years_experience)
  3. CANDIDATE INTERVIEW STATEMENTS — (only when present) answers the candidate gave in gap
     interviews; these may CONFIRM or DENY experience
  4. PRE-CLASSIFICATION — a rule-based pre-pass with: matched (direct), inferred_b (likely, confirm
     or reject), unresolved (no rule signal — you decide)
  5. REQUIREMENTS — the exact list you must classify: required + nice-to-have + ATS keywords

Classify EVERY entry in REQUIREMENTS (required, nice_to_have, AND keywords) into exactly one status:
  • "direct"  — the candidate clearly has this AND meets any stated years/seniority bar
  • "partial" — likely/inferred from context (adjacent skills, employer/domain), OR the skill is
                present but the candidate's years are below a stated bar or cannot be confirmed
  • "gap"     — no signal in the profile

Rules:
  - Classify ONLY the requirements given. Do NOT add, rename, merge, or split requirements.
  - Use profile years_experience and the JD seniority_level to decide direct vs partial.
  - When a skill is present but its years cannot be confirmed against a stated bar, choose "partial".
  - PROFICIENCY CEILING: when a requirement is DEPTH-WORDED ("sicherer Umgang", "fundierte
    Kenntnisse", "Expertenkenntnisse", "profound/expert-level command"), weigh the profile
    skill's "proficiency" field. Declared proficiency is a CEILING — the candidate's own
    declaration caps what may be claimed. A skill the profile declares at a basic/Anwender
    tier can NEVER be "direct" for a depth-worded requirement: classify it "partial" (below
    the bar — omit adjacent_evidence) and name the declared proficiency in
    "classification_note" — NEVER in "reason". "reason" is shown to the CV and cover-letter
    writers as what the candidate may claim; a ceiling is a fact about YOUR grading, not
    about the candidate, and writing it into "reason" makes the documents understate them
    (a real run published "Grundkenntnisse in SAP PP/MM" for a candidate who uses those
    modules daily and was a Key-User). State in "reason" what the candidate genuinely CAN
    claim for this requirement; put the cap and your grading logic in "classification_note". The
    skill's mere presence, or a years figure alone, is NOT depth evidence: years_experience
    measures how long ago the skill first appears in the profile, not how deeply it is used.
  - "partial" covers TWO different situations and you must distinguish them, because the document
    writers act on them differently:
      (a) ADJACENT — the candidate does not have the named thing but has a different capability that
          genuinely stands in for it (JD asks for TOGAF; the profile has 5 years of arc42). Set
          "adjacent_evidence" to the profile's own name for that capability ("arc42"). The writers
          will give THAT prominence — they must never present the JD's own word as the candidate's.
      (b) BELOW THE BAR — the candidate has exactly the named thing, just less of it than asked.
          Omit "adjacent_evidence" entirely; there is nothing else to promote.
    Never invent an adjacency to fill the field, and never set it on a "direct" entry.
  - STANCE: a denial or negative statement by the candidate ("I have no hands-on Azure
    experience", "AWS, not Azure") is evidence AGAINST that skill. Classify such a requirement
    "gap" — never "direct" or "partial" — and never cite a denial as supporting evidence.
    A skill being merely MENTIONED in the profile or statements is not a signal; what counts
    is what the candidate actually did.
  - COMPOUND requirements that name multiple technologies (e.g. "Cloud environment
    qualification (AWS, Azure)"): list in surface_forms ONLY the technologies the profile
    actually supports. A technology with no profile signal — or an explicit denial — must NOT
    appear among a claimable concept's surface forms; classify it as its own "gap" requirement
    instead.

SCOPE REQUIREMENTS (a separate labelled section, present only when the posting states a
quantified scope bar — team size, budget). These are judged, never keyword-matched:
  - For each entry return one object in "scope_classifications". Judge the SUFFICIENCY of the
    candidate's typed values against the JD's stated bar. The two sides may measure DIFFERENT
    quantities — every candidate value states its own semantics (e.g. team_size counts direct
    reports in that one role, while a JD figure may be a total organisational span). A value
    measuring a different quantity than the bar supports at most "partial"; state in "reason"
    which quantities you compared.
  - PROVENANCE (#328): every candidate value also carries "provenance", and a budget value
    carries the "unit" its figure states. "derived" means that entry's OWN responsibilities/
    achievements prose states this figure — code checked it, you did not. "uncorroborated"
    means the value is the candidate's stated testimony that no bullet of that entry
    repeats: still a real typed value, still eligible for "direct", but do NOT describe it
    in "reason" as something the CV says. Provenance never substitutes for
    "attested_evidence" below — that citation is yours to make and is verified separately.
  - "direct" requires a candidate value that meets the bar ON THE SAME quantity, and you MUST
    set "cited_entry" to that value's "entry" string exactly as given.
  - "partial" — BELOW THE BAR: a candidate value measuring the SAME quantity that falls short
    of the bar is "partial", never "gap" (known-insufficient is real signal, not silence —
    the candidate has scope experience, just less than asked). A value measuring a DIFFERENT
    quantity also supports at most "partial" (see above).
  - "gap" is reserved for NO candidate values at all, or values that carry no signal about
    the bar either way.
  - ATTESTED PROSE (ADR-070): when a work entry's own responsibilities/achievements prose in
    the CANDIDATE PROFILE states a quantified fact bearing on this bar (e.g. deputy coverage
    of a larger span, an interim leadership stint, a concrete project budget), you MUST
    report it in "attested_evidence": quote the sentence VERBATIM from that entry's prose,
    name the entry, and state in "unit" the quantity the quote itself measures (e.g.
    "site headcount covered as deputy", "direct reports", "project budget"). A verified
    attestation supports "partial" even when no typed value exists — never "direct".
  - NEVER classify a scope requirement from the presence of related words anywhere in the
    profile — only from the typed candidate values handed to you in its own entry, plus a
    verbatim "attested_evidence" quote per the rule above (reporting a quote is not
    presence-matching: the quote must itself STATE a quantity, and code verifies it
    resolves in the cited entry's own prose).

Respond ONLY with a valid JSON object matching this schema — no markdown, no explanations.

Schema:
{
  "classifications": [
    {
      "requirement": "exact requirement string from REQUIREMENTS",
      "status": "direct|partial|gap",
      "reason": "PUBLISHED TO THE DOCUMENT WRITERS as the candidate's claimable evidence for this requirement: what the profile shows the candidate genuinely has. Never state a ceiling, a shortfall, or your grading logic here — those go in classification_note.",
      "classification_note": "INTERNAL, never shown to any writer or reviewer: why this status follows — including any declared-proficiency ceiling you applied. Omit when you have nothing to add beyond reason.",
      "adjacent_evidence": "ONLY when status is partial AND the reason is that the candidate has a DIFFERENT but adjacent capability: the profile's own name for that capability. Omit otherwise.",
      "surface_forms": ["literal aliases an ATS scans for, e.g. K8s for Kubernetes, CI/CD for CI/CD pipelines"]
    }
  ],
  "scope_classifications": [
    {
      "concept": "exact concept string from SCOPE REQUIREMENTS — omit the whole array when that section is absent",
      "status": "direct|partial|gap",
      "reason": "which quantities you compared and why the status follows",
      "cited_entry": "REQUIRED when status is direct: the exact 'entry' string of the candidate value that meets the bar. Omit otherwise.",
      "attested_evidence": {"entry": "Role @ Company of the work entry whose prose states the fact", "quote": "that entry's own responsibility/achievement sentence, VERBATIM, stating a quantity bearing on the bar", "unit": "the quantity the quote itself measures, in a few words"}
    }
  ],
  "strengths": ["requirements where the candidate clearly meets or exceeds the bar"],
  "keyword_gaps": ["ATS keywords from the JD that are absent from the candidate's profile"]
}

Guidelines:
- Echo each requirement string exactly as given so it can be matched back.
- surface_forms: list the literal strings an ATS would scan for, including the requirement itself
  plus common abbreviations/variants. When a JD keyword is a variant of a concept the candidate
  already holds (e.g. keyword "CI/CD" vs required "CI/CD pipelines"), group it as a surface form of
  that concept rather than marking it a separate gap.
- reason is the grounding evidence for a direct/partial status — cite the profile signal.
- Do NOT reject inferred_b items without a clear counter-signal in the profile.
- keyword_gaps: list exact terms from the JD absent from the profile."""


# F4: the label carries the stance rule right where the statements appear, so the
# classifier never reads a denial as unlabeled profile text.
_STATEMENTS_LABEL = (
    "CANDIDATE INTERVIEW STATEMENTS (answers the candidate gave in gap interviews — they may "
    "CONFIRM or DENY experience; a denial or negative statement about a skill is evidence "
    'AGAINST it: classify that requirement "gap", never "direct"/"partial"):'
)


_INTERVIEW_SOURCES = ("interview", "agent_interview")


def _interview_statements(profile: dict) -> list[dict]:
    """Interview-sourced enrichment records from the profile's audit trail.

    Only the ``changes`` payload is surfaced — it is the candidate-stated content
    (values + rationale quoting the answer). Other sources (cv_upload, …) merely
    duplicate profile facts and stay out of the prompt entirely.

    Both interview doors count (#231 fix): the built-in guided interview
    (``interview``) AND the agent-elicited ``submit_claims`` door
    (``agent_interview``, E045/ADR-054) — a denial given through the agent
    channel is just as real testimony as one given through the UI, and the
    v4 stance rule (module docstring) can only apply to a denial it can see.
    """
    meta = profile.get("metadata") or {}
    if not isinstance(meta, dict):
        return []
    history = meta.get("enrichment_history") or []
    if not isinstance(history, list):
        return []
    return [
        {"changes": record.get("changes") or []}
        for record in history
        if isinstance(record, dict) and record.get("source") in _INTERVIEW_SOURCES
    ]


def build_user_prompt(
    job_analysis: dict,
    profile: dict,
    pre: PreClassification,
    scope_requirements: list[dict] | None = None,
) -> str:
    pre_dict = {
        "matched": pre.matched,
        "inferred_b": [
            {"requirement": c.requirement, "reason": c.reason} for c in pre.inferred_b
        ],
        "unresolved": pre.unresolved,
    }
    requirements = {
        "required": list(job_analysis.get("required_skills") or []),
        "nice_to_have": list(job_analysis.get("nice_to_have_skills") or []),
        "keywords": list(job_analysis.get("keywords") or []),
    }
    # F4: `metadata` (the enrichment audit trail) must not masquerade as profile
    # facts — a prior interview answer quoted in it ("no hands-on Azure
    # experience") would read as a token-match FOR the skill. The profile block
    # carries the candidate's actual data; interview-sourced records render under
    # the labeled statements section below. (The idempotency fingerprint hashes
    # profile_json itself — services/gap.py — so this prompt-shape change does
    # not affect reuse.)
    profile = profile if isinstance(profile, dict) else {}
    profile_wo_meta = {k: v for k, v in profile.items() if k != "metadata"}
    statements = _interview_statements(profile)
    statements_block = (
        f"{_STATEMENTS_LABEL}\n{json.dumps(statements, ensure_ascii=False, indent=2)}\n\n"
        if statements
        else ""
    )
    # ADR-069 clause 2: the scope block is assembled by services/gap.py — the JD's
    # stated bar verbatim plus the candidate's typed vault values WITH their field
    # semantics. Only the sufficiency JUDGEMENT is the model's; the facts, the
    # fail-closed floor and the citation check are code's.
    scope_block = (
        "SCOPE REQUIREMENTS (quantified requirements — judge sufficiency per the system "
        "rules; do NOT keyword-match):\n"
        f"{json.dumps(scope_requirements, ensure_ascii=False, indent=2)}\n\n"
        if scope_requirements
        else ""
    )
    return (
        "Produce the gap analysis JSON.\n\n"
        f"JOB ANALYSIS:\n{json.dumps(job_analysis, ensure_ascii=False, indent=2)}\n\n"
        f"CANDIDATE PROFILE:\n{json.dumps(profile_wo_meta, ensure_ascii=False, indent=2)}\n\n"
        f"{statements_block}"
        f"{scope_block}"
        f"PRE-CLASSIFICATION:\n{json.dumps(pre_dict, ensure_ascii=False, indent=2)}\n\n"
        f"REQUIREMENTS:\n{json.dumps(requirements, ensure_ascii=False, indent=2)}"
    )
