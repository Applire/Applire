# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#282 (wave 7) — honest gap disclaimers wrongly graded ``unverifiable``.

Charter run #6's cover-letter Oracle audit reported ``unverifiable_dominated``
on a letter both blind hiring-panel reviewers called "refreshingly honest".
Ground truth (``generated_cover_letters`` cb27a19c-... / d3c54592-...,
2026-07-26): among the 24 unverifiable claims were pure denial/delegation
sentences with no positive claim to ground —

    "I have not configured embedding models, vector stores, or rerankers
    myself, nor have I set up or worked hands-on with Prometheus, Grafana,
    or ELK stacks."
    "I lack direct LegalTech domain experience"
    "reranking was handled by our system engineer."

A negative statement has no vault evidence to trace to — the vault holds no
"evidence of absence" — so counting these ``unverifiable`` means the more
honest the letter, the worse the product's own credibility signal scores it.

PO decision (2026-07-26): route a PURE denial/delegation clause to the
existing ``not_applicable`` verdict (never a new verdict class — that is
issue #287, out of scope here). ``Claim.is_denial`` is the classification
signal (mirrors ``Claim.is_employer_fact``, #237 round-3); ``verify_claim``
short-circuits it before any vault-grounding attempt, exactly like the
employer-fact routing.

Critical anti-loophole (#207/#278 lesson): a sentence that DENIES one thing
but SMUGGLES a real positive claim in the same clause —

    "While I have not configured embedding models myself, I bring the
    discipline from regulated environments."
    "I have not led AI teams, though I effectively ran the ML org."

— must NOT get the free pass; the smuggled clause must stay fully gradeable.
Both shapes are reproduced here (invented fixtures, not verbatim from run-6).
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
            "role": "Engineering Lead",
            "is_current": True,
            "responsibilities": [
                "Led the ML platform team end-to-end, owning roadmap and "
                "delivery across three squads.",
            ],
        },
    ],
}


# ── pure denials must be classified is_denial=True ──────────────────────────


def test_pure_denial_clause_is_classified_denial():
    letter = {
        "body": {
            "paragraphs": [
                "I lack direct FinTech domain experience, and I have not "
                "configured vector databases or rerankers myself, nor have "
                "I set up observability stacks like Prometheus or Grafana.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    by_text = {c.text: c for c in claims}
    assert by_text["I lack direct FinTech domain experience"].is_denial
    assert by_text[
        "I have not configured vector databases or rerankers myself, nor "
        "have I set up observability stacks like Prometheus or Grafana."
    ].is_denial


def test_delegation_denial_clause_is_classified_denial():
    """"X was handled by our system engineer" denies personal ownership of X
    just as plainly as "I have not done X" — same not_applicable routing."""
    letter = {
        "body": {
            "paragraphs": [
                "Database design was my own work; the actual reranker "
                "tuning was handled by our platform engineer.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    by_text = {c.text: c for c in claims}
    assert by_text["the actual reranker tuning was handled by our platform engineer."].is_denial


@pytest.mark.asyncio
async def test_denial_claim_verdicts_not_applicable():
    verdict = await verify_claim(
        Claim(
            text="I lack direct FinTech domain experience",
            location="body.paragraphs[0][0]",
            is_denial=True,
        ),
        PROFILE,
    )
    assert verdict.verdict == "not_applicable"
    assert verdict.checker == "extraction"


@pytest.mark.asyncio
async def test_full_letter_report_excludes_pure_denials_from_domination():
    letter = {
        "body": {
            "paragraphs": [
                "I lack direct FinTech domain experience, and I have not "
                "configured vector databases or rerankers myself, nor have "
                "I set up observability stacks like Prometheus or Grafana.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    denial_results = [r for r in report.claims if r.claim.is_denial]
    assert len(denial_results) == 2
    assert all(r.verdict.verdict == "not_applicable" for r in denial_results)
    assert report.counts["not_applicable"] == 2
    # Both claims were the ENTIRE report — with them excluded from the
    # denominator there is nothing left to dominate.
    assert report.unverifiable_dominated is False


# ── anti-loophole: a smuggled positive claim must stay gradeable ────────────


def test_denial_with_comma_pivot_smuggled_claim_not_exempted():
    """"...myself, I bring the discipline..." — no explicit pivot word, just
    a bare comma before a fresh first-person clause. Must NOT be classified
    a pure denial (#278: a negation is scoped to its own clause, never to a
    co-occurring sibling clause)."""
    letter = {
        "body": {
            "paragraphs": [
                "While I have not configured vector databases myself, I "
                "bring the discipline from regulated environments.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    assert len(claims) == 1
    assert not claims[0].is_denial


def test_denial_with_though_pivot_smuggled_claim_not_exempted():
    """"I have not led AI teams, though I effectively ran the ML org." — the
    task's own named anti-loophole shape."""
    letter = {
        "body": {
            "paragraphs": [
                "I have not led AI teams, though I effectively ran the ML "
                "org day-to-day.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    assert len(claims) == 1
    assert not claims[0].is_denial


@pytest.mark.asyncio
async def test_smuggled_claim_still_reaches_normal_grounding():
    """The composite claim must still be graded through the ordinary vault
    pipeline (never short-circuited to not_applicable, never dropped)."""
    letter = {
        "body": {
            "paragraphs": [
                "I have not led AI teams, though I effectively ran the ML "
                "org day-to-day.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    assert len(report.claims) == 1
    result = report.claims[0]
    assert not result.claim.is_denial
    assert result.verdict.verdict != "not_applicable"


def test_single_clause_pure_denial_with_own_pronoun_not_smuggled():
    """The denial clause's OWN subject pronoun ("I have not...") must never
    be mistaken for a smuggled sibling clause — only a segment lacking any
    denial marker of its own counts."""
    letter = {
        "body": {
            "paragraphs": [
                "I have not deployed production Kubernetes clusters myself.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    assert len(claims) == 1
    assert claims[0].is_denial


def test_ordinary_positive_claim_is_never_classified_denial():
    """A plain affirmative sentence with no negation marker at all must
    never be misclassified — regression guard against an over-broad marker
    list."""
    letter = {
        "body": {
            "paragraphs": [
                "At Alpha Systems GmbH, I led the ML platform team "
                "end-to-end, owning roadmap and delivery.",
            ]
        },
        "recipient": {"company": "ClaimFlow GmbH"},
    }
    claims = extract_claims_from_letter(letter, PROFILE)
    assert len(claims) == 1
    assert not claims[0].is_denial
