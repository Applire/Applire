# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#237 — "an unverifiable-dominated report should itself fail louder".

Discrimination fixes (claim decomposition, attribution wiring, quantifier
figures) can only take a report so far — a report legitimately dominated by
claims the vault cannot check is still a signal the reader must not miss.
``TruthfulnessReport.unverifiable_dominated`` makes that judgement a typed,
backend-computed fact instead of a frontend-only heuristic re-derived from
raw counts (the exact field name/threshold are fixed by #237's expected
behaviour: strictly more than half of all claims verdicted ``unverifiable``).
"""
from __future__ import annotations

from applire.schemas.oracle import Claim, ClaimResult, ClaimVerdict, TruthfulnessReport


def _result(verdict: str) -> ClaimResult:
    return ClaimResult(
        claim=Claim(text="x", location="loc[0]"),
        verdict=ClaimVerdict(verdict=verdict, checker="grounding"),
    )


def test_empty_report_is_not_unverifiable_dominated():
    report = TruthfulnessReport.from_results("cv", [])
    assert report.unverifiable_dominated is False


def test_majority_unverifiable_report_is_dominated():
    results = [_result("unverifiable")] * 3 + [_result("grounded")] * 2
    report = TruthfulnessReport.from_results("cover_letter", results)
    assert report.counts["unverifiable"] == 3
    assert report.unverifiable_dominated is True


def test_exactly_half_unverifiable_is_not_dominated():
    """Strictly greater than 50%, not a tie — a 2/4 report is not dominated."""
    results = [_result("unverifiable")] * 2 + [_result("grounded")] * 2
    report = TruthfulnessReport.from_results("cover_letter", results)
    assert report.unverifiable_dominated is False


def test_minority_unverifiable_report_is_not_dominated():
    results = [_result("unverifiable")] * 2 + [_result("grounded")] * 3
    report = TruthfulnessReport.from_results("cv", results)
    assert report.unverifiable_dominated is False


def test_all_unverifiable_single_claim_report_is_dominated():
    results = [_result("unverifiable")]
    report = TruthfulnessReport.from_results("cv", results)
    assert report.unverifiable_dominated is True


# ── round-3 probe residual — ``not_applicable`` excluded from the ratio ─────
#
# Employer-fact statements (about the TARGET company, not the candidate) are
# extracted and verdicted ``not_applicable`` (checker ``extraction``) rather
# than silently vanished — but they must never count toward the domination
# denominator: a letter that correctly engages with the employer (#255) must
# not be penalised for doing so.


def test_not_applicable_claims_still_counted_but_excluded_from_ratio():
    results = (
        [_result("not_applicable")] * 2
        + [_result("unverifiable")] * 2
        + [_result("grounded")] * 3
    )
    report = TruthfulnessReport.from_results("cover_letter", results)
    assert report.counts["not_applicable"] == 2
    assert sum(report.counts.values()) == len(results)
    # 2 unverifiable / 5 checkable (2+3, not_applicable excluded) = 40%.
    assert report.unverifiable_dominated is False


def test_report_dominated_by_unverifiable_even_when_not_applicable_present():
    results = (
        [_result("not_applicable")] * 3
        + [_result("unverifiable")] * 3
        + [_result("grounded")] * 1
    )
    report = TruthfulnessReport.from_results("cover_letter", results)
    # 3 unverifiable / 4 checkable (3+1) = 75%, dominated regardless of the
    # 3 not_applicable claims sitting alongside them.
    assert report.unverifiable_dominated is True


def test_report_with_only_not_applicable_claims_is_not_dominated():
    results = [_result("not_applicable")] * 4
    report = TruthfulnessReport.from_results("cover_letter", results)
    assert report.unverifiable_dominated is False
