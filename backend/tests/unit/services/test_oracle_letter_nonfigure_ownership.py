# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#248 — the non-figure counterpart of the df78cac letter figure-ownership
fix (`test_oracle_letter_figure_ownership.py`).

Live-reproduced 2026-07-24 (generated_cover_letters 37ee8f77-4ca8-4859-9fb1-
60d481ce49ac, founder charter run 3): the generated letter read —

    "In my recent role at NordPharm, I initiated and led an agentic GenAI
    system automating the authoring and review of regulatory submission
    documentation, built with LangGraph/LangChain and RAG over
    reference documents. This system, running on Databricks,
    demonstrated my hands-on expertise in LLM applications, retrieval
    systems, AI evaluation, AI reliability, and AI observability, with a
    deterministic verification layer ensuring trustworthiness. As Founder
    of Applire, I developed an AI-powered platform..."

The clause "a deterministic verification layer ensuring trustworthiness."
carries NO figure — df78cac's ``_unattributable_figure_flag`` never even
matches its verify_claim branch (section 3, figure-free grounding, is a
SEPARATE code path from section 2c's figure loop). Its only vault backing is
``work_experience[5].achievements[0]`` ("Built deterministic verification
layer (Truthfulness Oracle) auditing every generated document") — Applire's,
NOT NordPharm's, even though the clause sits in a sentence ("This system,
running on Databricks...") that a reader attributes to the immediately
preceding NordPharm paragraph. The clause names NO employer itself (unlike
the earlier NordPharm sentence, or the later "As Founder of Applire..."
sentence), so ``source_experience_id`` is ``None`` — and
``_attribution_red_flag`` fails open on a ``None`` source id by design (#196:
never guess). The letter graded this claim ``grounded``.

Root cause, pinned against ground truth (see also
``test_oracle_extract.py``'s #248 section):

1. The vault stores the FULL legal entity name ("NordPharm SE"); the letter
   never repeats the suffix. Before this fix, ``letter_named_experience_ids``
   (whole-letter, used by the #243-adjacent ownership escape hatch) therefore
   never found NordPharm at all — only Applire — so the letter looked like a
   single-employer letter and the "letter names exactly one employer, and it
   owns the evidence" escape cleared every unanchored Applire-owned claim,
   including this one.
2. Even with (1) fixed, the SAME escape hatch would then wrongly flag the
   EARLIER, entirely honest "In my recent role at NordPharm..." sentence,
   whose own evidence is genuinely NordPharm's — that sentence's OWN text does
   name NordPharm (just without "SE"), so a purely letter-wide ambiguity test
   cannot tell it apart from the "This system..." blend, which names no
   employer at all. ``Claim.sentence_named_ids`` (loose, sentence-scoped, set
   at extraction time) is the fix: it lets the audit ask "does THIS claim's
   OWN enclosing sentence already name an owner of its evidence?" before ever
   falling back to the letter-wide, more permissive escape.

Fix (mirrors df78cac, generalized in ``audit._unattributable_evidence_flag``
from figures to the figure-free grounding path, section 3 of
``verify_claim``): an UNANCHORED figure-free claim whose ENTIRE vault backing
is owned, and whose OWN sentence does not name any of those owners, and the
letter overall does not unambiguously name exactly that one owner either, is
genuinely unattributable — downgrades to ``unverifiable`` (checker
``attribution``) instead of laundering the blend as ``grounded``. Anchored
claims are untouched (df78cac / #196's ``misattributed`` path already covers
those); role-agnostic backing (summary/skills/job-agnostic stories) still
clears any position, anchored or not.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document

PROFILE = {
    "personal_info": {"name": "Jonas Bergmann"},
    "work_experience": [
        {
            "id": "w-nordpharm",
            "company": "NordPharm SE",
            "role": "Associate Director",
            "is_current": True,
            "achievements": [
                "Initiated and led an agentic GenAI system automating the "
                "authoring and review of regulatory submission "
                "documentation, built with LangGraph/LangChain and RAG "
                "over reference documents.",
            ],
        },
        {
            "id": "w-applire",
            "company": "Applire",
            "role": "Founder & Lead Developer",
            "is_current": True,
            "achievements": [
                "Built deterministic verification layer (Truthfulness "
                "Oracle) auditing every generated document.",
            ],
        },
    ],
}

# The exact run-3 blend shape, trimmed to the three load-bearing sentences.
BLEND_LETTER = {
    "body": {
        "paragraphs": [
            "In my recent role at NordPharm, I initiated and led an agentic "
            "GenAI system automating the authoring and review of regulatory "
            "submission documentation, built with LangGraph/"
            "LangChain and RAG over reference documents.",
            "This system, running on Databricks, demonstrated my hands-on "
            "expertise in LLM applications and AI reliability, with a "
            "deterministic verification layer ensuring trustworthiness.",
            "As Founder of Applire, I developed an AI-powered platform "
            "with privacy-by-design and GDPR compliance.",
        ]
    }
}


def _claim(report, needle):
    hits = [r for r in report.claims if needle in r.claim.text]
    assert hits, f"expected a decomposed claim containing {needle!r}"
    return hits[0]


@pytest.mark.asyncio
async def test_blended_clause_is_not_grounded_on_foreign_owner_evidence():
    """The live #248 bug: the blend clause's evidence is exclusively
    Applire's; the clause names no employer; NordPharm is (correctly, once
    the suffix tolerance lands) also named elsewhere in the letter — full
    attribution is impossible, so it must NOT verdict grounded."""
    report = await audit_document("cover_letter", PROFILE, letter_data=BLEND_LETTER)
    result = _claim(report, "deterministic verification layer ensuring trustworthiness")
    assert result.verdict.verdict != "grounded", result.verdict
    # Unanchored (no single employer stated in THIS clause's text) — the
    # "honest downgrade" branch, mirroring df78cac's unanchored-figure
    # precedent (unverifiable/attribution), not a fabricated misattribution
    # verdict the claim's own text never claims.
    assert result.verdict.verdict == "unverifiable"
    assert result.verdict.checker == "attribution"
    assert result.verdict.evidence


@pytest.mark.asyncio
async def test_honest_nordpharm_sentence_stays_grounded_not_over_dropped():
    """Over-drop guard: the earlier, entirely honest "In my recent role at
    NordPharm..." sentence must NOT be caught by the same mechanism just
    because it also fails the STRICT anchor (missing the "SE" suffix) — its
    own sentence names NordPharm, and NordPharm genuinely owns its evidence."""
    report = await audit_document("cover_letter", PROFILE, letter_data=BLEND_LETTER)
    result = _claim(report, "initiated and led an agentic GenAI system")
    assert result.verdict.verdict == "grounded", result.verdict


@pytest.mark.asyncio
async def test_single_employer_letter_unanchored_owned_clause_stays_grounded():
    """Over-drop guard (the grounded-starvation failure already fixed once):
    a letter naming only ONE employer overall, with an unanchored,
    employer-free sentence whose owned backing belongs to that SAME single
    employer, must stay grounded — the normal honest case."""
    letter = {
        "body": {
            "paragraphs": [
                "At Applire, I own product, architecture and development.",
                "I also built a deterministic verification layer auditing "
                "every generated document.",
            ]
        }
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    result = _claim(report, "deterministic verification layer auditing")
    assert result.verdict.verdict == "grounded", result.verdict


@pytest.mark.asyncio
async def test_role_agnostic_backing_stays_grounded_regardless_of_ownership_check():
    """A figure-free claim backed by role-agnostic evidence (professional
    summary) is never attribution-starved, anchored or not, and regardless
    of how many employers the letter names."""
    profile = {
        **PROFILE,
        "professional_summary": {
            "en": "Builds a deterministic verification layer ensuring trustworthy AI systems."
        },
    }
    letter = {
        "body": {
            "paragraphs": [
                "In my recent role at NordPharm, I led automation.",
                "As Founder of Applire, I built a platform.",
                "I also built a deterministic verification layer ensuring "
                "trustworthy systems.",
            ]
        }
    }
    report = await audit_document("cover_letter", profile, letter_data=letter)
    result = _claim(report, "I also built a deterministic verification layer")
    assert result.verdict.verdict == "grounded", result.verdict
