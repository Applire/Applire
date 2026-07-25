# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#237 round-3 (live MCP probe residual, 2026-07-24) — employer-fact claims.

Live-reproduced: "ClaimFlow is a fast-growing InsurTech company." and "Its AI
platform automates insurance claims processing for European insurers." are
statements about the TARGET COMPANY (sourced from the JD; the ADR-021
reviewer already validates them against JD text), not the candidate. The
deterministic vault audit can never ground them — filing them
``unverifiable`` structurally inflates domination on every letter that
engages the employer (#255 now REQUIRES this). Classified deterministically:
a sentence naming the recipient company with NO first-person pronoun (EN/DE)
is an employer fact; the classification carries forward within the SAME
paragraph (mirroring the employer-ANCHOR continuation) so a follow-up
sentence that only uses a pronoun ("Its AI platform...") is caught too.
Verdicted ``not_applicable`` (checker ``extraction``) — extracted and shown,
never silently dropped, but excluded from the domination denominator.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document, verify_claim
from applire.services.oracle.extract import extract_claims_from_letter
from applire.schemas.oracle import Claim

PROFILE = {
    "personal_info": {"name": "Max Prober"},
    "work_experience": [
        {
            "id": "w-alpha",
            "company": "Alpha Systems GmbH",
            "role": "Principal Platform Engineer",
            "is_current": True,
            "responsibilities": [
                "Built an internal LLM-assisted document classification "
                "service in Python (FastAPI, PostgreSQL, Docker).",
            ],
        },
    ],
}

LETTER = {
    "body": {
        "paragraphs": [
            "I am writing to express my interest in the Senior AI "
            "Engineer (m/f/d) position at ClaimFlow GmbH. ClaimFlow is a "
            "fast-growing InsurTech company. Its AI platform automates "
            "insurance claims processing for European insurers.",
            "At Alpha Systems GmbH, I built an LLM-assisted document "
            "classification service in Python.",
        ]
    },
    "recipient": {"company": "ClaimFlow GmbH"},
}


def test_employer_fact_sentence_naming_recipient_is_classified():
    claims = extract_claims_from_letter(LETTER, PROFILE)
    by_text = {c.text: c for c in claims}
    assert by_text["ClaimFlow is a fast-growing InsurTech company."].is_employer_fact


def test_employer_fact_classification_carries_within_same_paragraph():
    """"Its AI platform..." never names ClaimFlow itself — the
    classification must carry forward from the PRECEDING sentence in the
    SAME paragraph (mirroring the anchor-continuation mechanism)."""
    claims = extract_claims_from_letter(LETTER, PROFILE)
    by_text = {c.text: c for c in claims}
    target = by_text[
        "Its AI platform automates insurance claims processing for "
        "European insurers."
    ]
    assert target.is_employer_fact


def test_first_person_sentence_naming_recipient_is_not_an_employer_fact():
    """A candidate self-statement that happens to name the recipient company
    is NOT an employer fact — the first-person pronoun disqualifies it
    regardless of the company mention."""
    letter = {
        "body": {
            "paragraphs": [
                "I am confident my skills are a strong match for ClaimFlow "
                "GmbH.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    assert len(claims) == 1
    assert not claims[0].is_employer_fact


def test_employer_fact_run_breaks_on_first_person_sentence():
    """The paragraph-scoped carry must reset once a first-person sentence
    intervenes — a later company-silent sentence must NOT keep inheriting a
    stale classification across an intervening candidate statement."""
    letter = {
        "body": {
            "paragraphs": [
                "ClaimFlow is a fast-growing InsurTech company. My skills "
                "align well with this kind of work. It has a great "
                "culture.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    by_text = {c.text: c for c in claims}
    assert by_text["ClaimFlow is a fast-growing InsurTech company."].is_employer_fact
    assert not by_text["My skills align well with this kind of work."].is_employer_fact
    assert not by_text["It has a great culture."].is_employer_fact


def test_candidate_sentence_about_own_role_is_not_an_employer_fact():
    claims = extract_claims_from_letter(LETTER, PROFILE)
    by_text = {c.text: c for c in claims}
    own = by_text[
        "At Alpha Systems GmbH, I built an LLM-assisted document "
        "classification service in Python."
    ]
    assert not own.is_employer_fact


@pytest.mark.asyncio
async def test_employer_fact_claim_verdicts_not_applicable():
    verdict = await verify_claim(
        Claim(
            text="ClaimFlow is a fast-growing InsurTech company.",
            location="body.paragraphs[0][1]",
            is_employer_fact=True,
        ),
        PROFILE,
    )
    assert verdict.verdict == "not_applicable"
    assert verdict.checker == "extraction"


@pytest.mark.asyncio
async def test_full_letter_report_excludes_employer_facts_from_domination():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    employer_fact_results = [r for r in report.claims if r.claim.is_employer_fact]
    assert len(employer_fact_results) == 2
    assert all(r.verdict.verdict == "not_applicable" for r in employer_fact_results)
    assert report.counts["not_applicable"] == 2
