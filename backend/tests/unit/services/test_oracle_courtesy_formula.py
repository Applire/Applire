# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial-pass residual (2026-07-23) — courtesy/meta formula filter.

An entirely honest generated cover letter still audited unverifiable-dominated
because pure courtesy openers/closers ("I am writing to express my
interest…", "Thank you for your time and consideration.") were extracted as
claims and, having no vault-checkable content, piled into the unverifiable
bucket. They are formulas, not factual claims about the candidate — this
module pins that they are no longer extracted at all, while a clause that
ALSO carries a factual assertion keeps its full original text as a real
claim (conservative by construction).

Deliberately does NOT touch the pinned F14 regression in
``test_oracle_letter_audit.py`` — "I look forward to hearing from you." (a
bare, non-meeting-specific closer) is intentionally left OUTSIDE the formula
seed set so that existing coverage of soft-but-extracted claims stays green.
"""
from __future__ import annotations

import pytest

from applire.services.oracle.extract import _is_pure_formula_clause, extract_claims_from_letter


# ── the pure-formula predicate ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "I am excited to apply for the Senior Automation Engineer role at your company.",
        "I am writing to express my interest in the DevOps Engineer position at Contoso.",
        "Thank you for your time and consideration.",
        "I appreciate your time and consideration.",
        "Please do not hesitate to contact me at your earliest convenience.",
        "I look forward to discussing this further.",
        "I would welcome the opportunity to discuss this further.",
        "It is with great pleasure that I write to you.",
        # DE
        "Sehr geehrte Damen und Herren,",
        "Ich freue mich auf ein persönliches Gespräch.",
        "Mit freundlichen Grüßen",
        "Vielen Dank für Ihre Zeit und Aufmerksamkeit.",
    ],
)
def test_pure_courtesy_formula_detected(text):
    assert _is_pure_formula_clause(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # A genuine narrative sentence, no formula seed at all.
        "At BioNTech, I led AI automation projects that reduced manual QA "
        "effort by 40%.",
        # Multi-skill enumeration clauses (#237 follow-up) — never formulas.
        "My experience includes designing and implementing RESTful APIs "
        "with Python, FastAPI.",
        "I have worked with PostgreSQL, SQLAlchemy, and Docker.",
        "I automated workflows using Git, GitHub Actions.",
        # The pinned F14 regression: bare "look forward to hearing" stays.
        "I look forward to hearing from you.",
        # A seed phrase that ALSO carries a substantive fact must not be
        # dropped whole — split_clauses already isolates it into its own
        # clause upstream, but the predicate itself must not over-fire even
        # when handed the untouched sentence.
        "I am writing because I led the migration of production databases "
        "at Contoso.",
    ],
)
def test_substantive_clause_not_flagged_as_formula(text):
    assert _is_pure_formula_clause(text) is False


def test_empty_and_short_text_not_flagged():
    assert _is_pure_formula_clause("") is False
    assert _is_pure_formula_clause("   ") is False


# ── wired into letter extraction ─────────────────────────────────────────────

def test_pure_courtesy_paragraph_yields_no_claim():
    letter = {
        "body": {
            "paragraphs": [
                "I am writing to express my interest in the Senior Automation "
                "Engineer role at your company."
            ]
        }
    }
    assert extract_claims_from_letter(letter) == []


def test_mixed_courtesy_and_fact_keeps_the_factual_clause_only():
    """A sentence bolting a real fact onto an opener via a clause-boundary
    conjunction: the courtesy clause is dropped, the fact survives whole."""
    letter = {
        "body": {
            "paragraphs": [
                "I am writing to express my interest, and I led migration "
                "efforts at Contoso."
            ]
        }
    }
    claims = extract_claims_from_letter(letter)
    texts = [c.text for c in claims]
    assert texts == ["I led migration efforts at Contoso."]


def test_courtesy_closer_paragraph_yields_no_claim():
    letter = {"body": {"paragraphs": ["Thank you for your time and consideration."]}}
    assert extract_claims_from_letter(letter) == []


def test_multiple_paragraphs_only_formula_ones_are_dropped():
    letter = {
        "body": {
            "paragraphs": [
                "I am excited to apply for the Senior Automation Engineer "
                "role at your company.",
                "I automated workflows using Git, GitHub Actions.",
                "Thank you for your time and consideration.",
            ]
        }
    }
    claims = extract_claims_from_letter(letter)
    assert len(claims) == 1
    assert claims[0].text == "I automated workflows using Git, GitHub Actions."
