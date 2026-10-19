# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire. Contact: kontakt@applire.de

"""ADR-048 amended 2026-10-18 (ruling DEL-5 (2)) — a ledger row's ``evidence``
is the gap analysis's summary (its ``reason``), not a source of figures.

Delivery run 2: the gap analysis wrote "14 Jahre Führungserfahrung in der
Produktion" for a vault that says "14 Jahren Erfahrung in der diskreten
Fertigung". Two views handed that string on as the candidate's evidence — the
CV corrector's under-claim finding ("The candidate's profile evidence for it")
and the reviewer's VERIFIED COVERAGE CHECK ("profile evidence:", in a block that
calls itself ground truth) — and both documents carried the figure. One seam
test per call site.
"""
from __future__ import annotations

from applire.services.cv_gap_hints import UnderclaimedConcept, _issue_text
from applire.services.keyword_ledger import render_verified_coverage_block

_SUMMARY = "14 Jahre Führungserfahrung in der Produktion, darunter zwei Fertigungsbereiche."


def test_corrector_finding_labels_the_summary_and_points_at_the_profile() -> None:
    text = _issue_text(
        UnderclaimedConcept(
            concept="Führungserfahrung in der Produktion",
            evidence=_SUMMARY,
            fit_weight=1.0,
            surface_forms=("Führungserfahrung in der Produktion",),
            tag_only=True,
        )
    )
    assert "profile evidence for it" not in text
    assert f'The gap analysis summarised the profile\'s support for it as: "{_SUMMARY}"' in text
    assert "a summary, not the candidate's words" in text
    assert "take every figure, and what it measures, from the CANDIDATE PROFILE itself" in text


def test_corrector_finding_without_evidence_names_no_summary() -> None:
    text = _issue_text(
        UnderclaimedConcept(
            concept="KVP", evidence="", fit_weight=1.0, surface_forms=("KVP",), tag_only=False,
        )
    )
    assert "summarised" not in text


def test_reviewer_coverage_block_carries_no_summary_string() -> None:
    block = render_verified_coverage_block(
        [
            {
                "concept": "Führungserfahrung in der Produktion",
                "surface_forms": ["Führungserfahrung in der Produktion", "Mitarbeiterführung"],
                "evidence": _SUMMARY,
                "evidence_owners": ["Weberit Kunststofftechnik GmbH"],
            }
        ],
        bounded=True,
    )
    assert "14 Jahre" not in block
    assert "profile evidence" not in block
    assert "Führungserfahrung in der Produktion [forms: Führungserfahrung in der Produktion, Mitarbeiterführung]" in block
    assert "owned by: Weberit Kunststofftechnik GmbH" in block
    assert "beyond what that source states, WAIVE it" in block
