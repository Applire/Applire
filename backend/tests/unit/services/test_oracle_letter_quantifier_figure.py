# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#237 (run-4 residual) — a fabricated figure borrowed from an UNRELATED
vault fact must never camouflage as checker conservatism.

Live-reproduced (run-4 report + LLM debug log, 2026-07-24): the generated
letter said "mentoring teams of 5+" for a StartupX mentoring story, but the
ONLY "5" anywhere in the vault is "Lead a team of five tech leads and system
owners" — an unrelated achievement on the candidate's CURRENT, DIFFERENT
role (E2E Supply Chain). Two independent gaps let this slip past every
existing check:

1. ``matchers/figures.py``'s number regex requires >=2 digits (single
   digits sit below the signal floor by design — date fragments and spelled-
   number wording variance). "5+" never became a ``Figure`` at all, so the
   claim never reached the numbers/attribution checkers (section 1-2 of
   ``verify_claim``) — it fell through to the figure-free grounding branch
   (section 3), scored below the coverage floor, and defaulted to
   ``unverifiable`` — indistinguishable from genuine checker conservatism.
2. Even with (1) fixed, the vault fact spells the number out ("five"); a
   naive digit-only match would still find no vault occurrence of "5" and
   misreport ``unbacked`` (no evidence) rather than the more precise
   ``misattributed`` (real evidence, wrong owner) the run-4 ground truth
   calls for.

Fix: a bounded, unambiguous "N+" quantifier pattern (never a date fragment,
never spelled-word variance) is recognized as a figure; the vault index
additionally recognizes EN/DE spelled-out small number words (mirroring
``services/profile/reconcile/stance.py``'s independent ``_spelled_figures``,
"one"/"eins" excluded — ambiguous with the article) so "five" indexes as
figure value "5" too. The letter clause names StartupX (giving it a strict
attribution anchor), so the resolved match — owned exclusively by the
DIFFERENT current role — routes through the existing #196 attribution red
flag and verdicts ``misattributed``, never ``unverifiable``.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document

PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "work_experience": [
        {
            "id": "w-e2e",
            "company": "E2E Supply Chain",
            "role": "Engineering Manager",
            "start_date": "2023-01",
            "end_date": None,
            "achievements": [
                "Lead a team of five tech leads and system owners.",
            ],
        },
        {
            "id": "w-startupx",
            "company": "StartupX",
            "role": "Mentor",
            "start_date": "2019-01",
            "end_date": "2021-12",
            "achievements": [
                "Coached early-career engineers on system design fundamentals.",
            ],
        },
    ],
}

LETTER = {
    "body": {
        "paragraphs": [
            "As a mentor at StartupX, I take pride in mentoring teams of 5+ "
            "engineers.",
        ]
    }
}


def _quantifier_claim(report):
    hits = [r for r in report.claims if "5+" in r.claim.text]
    assert hits, "expected a decomposed claim carrying the '5+' quantifier"
    return hits[0]


@pytest.mark.asyncio
async def test_cross_role_quantifier_figure_flags_misattributed_not_unverifiable():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    result = _quantifier_claim(report)
    assert result.verdict.verdict != "unverifiable", result.verdict
    assert result.verdict.verdict == "misattributed", result.verdict
    assert result.verdict.checker in ("attribution", "numbers")
    assert result.verdict.evidence


@pytest.mark.asyncio
async def test_same_role_quantifier_figure_stays_grounded():
    """Over-drop guard: the SAME "N+" figure, anchored to the role that
    actually owns the evidence, must stay grounded — the fix must not turn
    every quantifier clause into a false positive."""
    letter = {
        "body": {
            "paragraphs": [
                "As an Engineering Manager at E2E Supply Chain, I lead teams "
                "of 5+ tech leads and system owners.",
            ]
        }
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    result = _quantifier_claim(report)
    assert result.verdict.verdict == "grounded", result.verdict


@pytest.mark.asyncio
async def test_unmatched_quantifier_figure_still_flags_unbacked_not_unverifiable():
    """A "N+" quantifier with NO vault occurrence at all (spelled or digit)
    is a plain fabrication — must verdict unbacked (checker numbers), still
    never unverifiable."""
    letter = {
        "body": {
            "paragraphs": [
                "As a mentor at StartupX, I take pride in mentoring teams "
                "of 37+ engineers.",
            ]
        }
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    hits = [r for r in report.claims if "37+" in r.claim.text]
    assert hits
    assert hits[0].verdict.verdict == "unbacked"
    assert hits[0].verdict.checker == "numbers"
