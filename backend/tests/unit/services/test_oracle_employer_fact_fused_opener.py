# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#282 (wave 7) — employer facts extracted as candidate claims when fused
with the courtesy opener in ONE sentence.

Ground truth (``generated_cover_letters`` cb27a19c-.../d3c54592-...,
2026-07-26): both run-6 letters open with a single sentence that FUSES the
"I am writing to express my interest..." courtesy formula with a relative
clause describing the RECIPIENT company —

    "I am writing to express my interest in the AI Engineering Lead
    position at Connect-AI, a fast-growing LegalTech company whose
    AI-powered platform is already used by hundreds of customers."

``extract.extract_claims_from_letter``'s existing employer-fact classifier
(#237 round-3, ``Claim.is_employer_fact`` -> ``not_applicable``) already
covers a company-descriptive sentence that stands on its own (see
``test_oracle_employer_facts.py``). It fails here specifically because the
classifier runs against the WHOLE untrimmed sentence, which contains an "I"
in the courtesy prefix — even though ``_strip_formula_prefix`` then trims
that very prefix away and stores only the company-descriptive remainder as
the claim's TEXT. The claim shown to the user carries no first-person
content at all, yet was classified against a pronoun that lived entirely in
the discarded prefix.

Fix: classify against the RETAINED claim text when a courtesy prefix was
actually stripped and the remainder itself carries no first-person pronoun.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document
from applire.services.oracle.extract import extract_claims_from_letter

PROFILE = {
    "personal_info": {"name": "Max Prober"},
    "work_experience": [
        {
            "id": "w-alpha",
            "company": "Alpha Systems GmbH",
            "role": "Engineering Lead",
            "is_current": True,
            "responsibilities": [
                "Led the ML platform team end-to-end, owning roadmap and "
                "delivery across three squads.",
            ],
        },
    ],
}

LETTER = {
    "body": {
        "paragraphs": [
            "I am writing to express my interest in the AI Engineering "
            "Lead position at ClaimFlow GmbH, a fast-growing InsurTech "
            "company whose AI-powered platform is already used by "
            "hundreds of customers. With hands-on experience in machine "
            "learning, I am eager to contribute.",
        ]
    },
    "recipient": {"company": "ClaimFlow GmbH"},
}


def test_company_descriptive_remainder_of_fused_opener_is_employer_fact():
    claims = extract_claims_from_letter(LETTER, PROFILE)
    company_claims = [
        c for c in claims if "hundreds of customers" in c.text
    ]
    assert company_claims, [c.text for c in claims]
    assert all(c.is_employer_fact for c in company_claims), [
        (c.text, c.is_employer_fact) for c in claims
    ]


def test_fused_opener_no_first_person_in_retained_text():
    """Sanity check on the fixture itself: the RETAINED claim text (after
    the courtesy prefix is stripped) genuinely carries no first-person
    pronoun — this is what the fix keys off."""
    claims = extract_claims_from_letter(LETTER, PROFILE)
    company_claims = [c for c in claims if "hundreds of customers" in c.text]
    assert company_claims
    for c in company_claims:
        lowered = c.text.lower()
        assert " i " not in f" {lowered} "
        assert not lowered.startswith("i ")


@pytest.mark.asyncio
async def test_fused_opener_company_fact_verdicts_not_applicable():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    company_results = [
        r for r in report.claims if "hundreds of customers" in r.claim.text
    ]
    assert company_results
    assert all(r.verdict.verdict == "not_applicable" for r in company_results)


# ── over-drop guard: the second (first-person) sentence must stay gradeable ─


def test_second_sentence_with_first_person_still_not_employer_fact():
    claims = extract_claims_from_letter(LETTER, PROFILE)
    candidate_claims = [c for c in claims if "eager to contribute" in c.text]
    assert candidate_claims
    assert all(not c.is_employer_fact for c in candidate_claims)


# ── regression guard: the existing clean multi-sentence case is unaffected ──


def test_unfused_employer_fact_sentence_still_classified():
    """The pre-existing, already-working shape (separate sentences, no
    formula prefix at all) must not regress."""
    letter = {
        "body": {
            "paragraphs": [
                "I am writing to express my interest in the Senior AI "
                "Engineer position at ClaimFlow GmbH. ClaimFlow is a "
                "fast-growing InsurTech company.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    by_text = {c.text: c for c in claims}
    assert by_text["ClaimFlow is a fast-growing InsurTech company."].is_employer_fact


def test_first_person_sentence_naming_company_with_no_formula_prefix_unaffected():
    """A first-person sentence that names the company but carries NO
    courtesy formula prefix at all (nothing for ``_strip_formula_prefix`` to
    remove) must keep failing the employer-fact check exactly as before —
    the fix must never fire when no prefix was actually stripped."""
    letter = {
        "body": {
            "paragraphs": [
                "I am confident my background fits ClaimFlow GmbH well.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    assert len(claims) == 1
    assert not claims[0].is_employer_fact
