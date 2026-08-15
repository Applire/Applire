# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for ``services/review_compliance.py`` (#537, ADR-076 clause 2):

* signal-class classification (deliverable 2) — best-effort, log-only;
* the three mechanically-checkable implementation-compliance shapes (deliverable 1) —
  quoted-term presence-add, quoted-term forbidden-claim-removal, and repetition-count
  reduction — plus the ``unmeasurable`` outcome for everything else;
* per-signal-class aggregation, including the ``under_claim`` known-empty class.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.review_severity import SEVERITY_BLOCKING, SEVERITY_MINOR
from applire.services.review_compliance import (
    ComplianceOutcome,
    SignalClass,
    aggregate_by_signal_class,
    classify_signal,
    evaluate_compliance,
    measure_corrector_compliance,
)
from applire.services.review_issues import ReviewIssue

# --------------------------------------------------------------------------
# Signal classification (deliverable 2)
# --------------------------------------------------------------------------


def test_verified_coverage_cue_classifies_as_coverage():
    issue = (
        "VERIFIED COVERAGE CHECK — the claimable term 'Budgetverantwortung' is "
        "still absent from the draft."
    )
    assert classify_signal(issue) == SignalClass.COVERAGE


def test_do_not_claim_cue_classifies_as_presence():
    issue = "DO NOT CLAIM — 'LegalTech' is presented as something the candidate has done."
    assert classify_signal(issue) == SignalClass.PRESENCE


def test_unanchored_cue_classifies_as_anchoring():
    issue = "Paragraph 2: the '73%' figure is unanchored — no employer is named in the sentence."
    assert classify_signal(issue) == SignalClass.ANCHORING


def test_figure_ownership_wrong_owner_cue_classifies_as_anchoring():
    issue = "Wrong owner: the headcount figure belongs to Northwind, not Acme."
    assert classify_signal(issue) == SignalClass.ANCHORING


def test_unaddressed_hard_requirement_cue_classifies_as_unaddressed_requirement():
    issue = "The hard requirement 'ISO 45001' is unaddressed anywhere in the draft."
    assert classify_signal(issue) == SignalClass.UNADDRESSED_REQUIREMENT


def test_positioning_requested_key_cue_classifies_as_unaddressed_requirement():
    issue = "Required content not delivered: company_domain_engagement is missing from the body."
    assert classify_signal(issue) == SignalClass.UNADDRESSED_REQUIREMENT


def test_oversell_cue_classifies_as_figure():
    issue = "The summary overstates seniority — 'led' where the source says 'contributed to'."
    assert classify_signal(issue) == SignalClass.FIGURE


def test_bare_figure_with_no_cue_falls_back_to_figure_via_figure_detection():
    issue = "'73%' appears in the summary but the profile evidence only supports '61%'."
    assert classify_signal(issue) == SignalClass.FIGURE


def test_plain_prose_with_no_cue_and_no_figure_is_other():
    issue = "Fabricated bullet: 'led the Kubernetes migration' has no support in the profile."
    assert classify_signal(issue) == SignalClass.OTHER


def test_presence_cue_wins_over_coverage_cue_when_both_could_apply():
    """'do not claim' + 'claimable' both present — presence is checked first because it
    is the more specific phrase (module docstring's stated precedence)."""
    issue = "'Kafka' is a DO NOT CLAIM term but is listed among claimable coverage terms."
    assert classify_signal(issue) == SignalClass.PRESENCE


def test_classify_signal_never_returns_under_claim():
    """No emitter reaches the reviewer prompt for this class today (module docstring) —
    the classifier must never manufacture a hit for it."""
    samples = [
        "VERIFIED COVERAGE CHECK — 'X' absent.",
        "DO NOT CLAIM — 'Y' presented as done.",
        "unanchored figure",
        "hard requirement unaddressed",
        "overstates seniority",
        "'73%' is present",
        "a completely unrelated sentence",
    ]
    for issue in samples:
        assert classify_signal(issue) != SignalClass.UNDER_CLAIM, issue


# --------------------------------------------------------------------------
# Compliance shapes (deliverable 1)
# --------------------------------------------------------------------------


def test_missing_term_shape_implemented_when_term_now_present():
    issue = "VERIFIED COVERAGE CHECK — 'Budgetverantwortung' is not in the draft."
    verdict = evaluate_compliance(issue, "no mention here", "now carries Budgetverantwortung ca. 6 Mio. €")
    assert verdict.outcome == ComplianceOutcome.IMPLEMENTED
    assert verdict.shape == "missing_term_added"


def test_missing_term_shape_not_implemented_when_still_absent():
    issue = "VERIFIED COVERAGE CHECK — 'Budgetverantwortung' is not in the draft."
    verdict = evaluate_compliance(issue, "no mention here", "still no mention here")
    assert verdict.outcome == ComplianceOutcome.NOT_IMPLEMENTED


def test_missing_term_shape_requires_all_quoted_terms_present():
    issue = "Required content not delivered — 'Digitalisierung' and 'Fertigung' both absent."
    only_one = evaluate_compliance(issue, "", "now mentions Digitalisierung only")
    assert only_one.outcome == ComplianceOutcome.NOT_IMPLEMENTED
    both = evaluate_compliance(issue, "", "now mentions Digitalisierung and Fertigung")
    assert both.outcome == ComplianceOutcome.IMPLEMENTED


def test_forbidden_claim_shape_implemented_when_term_removed():
    issue = "DO NOT CLAIM — 'LegalTech' is presented as something the candidate has done."
    verdict = evaluate_compliance(issue, "has LegalTech experience", "rewritten with no mention of it")
    assert verdict.outcome == ComplianceOutcome.IMPLEMENTED
    assert verdict.shape == "forbidden_claim_removed"


def test_forbidden_claim_shape_unmeasurable_when_term_still_present():
    """Still present is deliberately NOT scored non-compliant — the term may
    legitimately remain reframed as an honest aspiration (module docstring)."""
    issue = "DO NOT CLAIM — 'LegalTech' is presented as something the candidate has done."
    verdict = evaluate_compliance(
        issue, "has LegalTech experience", "While I have not worked in LegalTech directly..."
    )
    assert verdict.outcome == ComplianceOutcome.UNMEASURABLE
    assert verdict.shape == "forbidden_claim_removed"


def test_repetition_shape_implemented_when_count_drops():
    issue = "'Bei Weberit Kunststofftechnik GmbH' is repeated 6 times in a single paragraph."
    current = "Bei Weberit Kunststofftechnik GmbH " * 6
    next_draft = "Bei Weberit Kunststofftechnik GmbH " * 2
    verdict = evaluate_compliance(issue, current, next_draft)
    assert verdict.outcome == ComplianceOutcome.IMPLEMENTED
    assert verdict.shape == "repetition_reduced"


def test_repetition_shape_not_implemented_when_count_unchanged():
    issue = "'Bei Weberit Kunststofftechnik GmbH' is repeated 6 times in a single paragraph."
    current = "Bei Weberit Kunststofftechnik GmbH " * 4
    verdict = evaluate_compliance(issue, current, current)
    assert verdict.outcome == ComplianceOutcome.NOT_IMPLEMENTED


def test_repetition_shape_not_implemented_when_count_increases():
    issue = "'X' is repeated 2 times in a single paragraph."
    verdict = evaluate_compliance(issue, "X X", "X X X X")
    assert verdict.outcome == ComplianceOutcome.NOT_IMPLEMENTED


# --------------------------------------------------------------------------
# The unmeasurable path — MUST be explicit, never silently scored either way
# --------------------------------------------------------------------------


def test_unclassifiable_prose_issue_is_unmeasurable_not_silently_scored():
    issue = "The summary overstates the candidate's seniority beyond what the profile supports."
    verdict = evaluate_compliance(issue, "old summary text", "new summary text, still overstated")
    assert verdict.outcome == ComplianceOutcome.UNMEASURABLE
    assert verdict.shape is None


def test_cross_document_contradiction_prose_is_unmeasurable():
    issue = (
        "The CV asserts 'Digitalisierung' but the letter disclaims it as an honest gap — "
        "the ledger marks it claimable, so the letter is what is wrong."
    )
    verdict = evaluate_compliance(issue, "cv text", "letter text unchanged")
    assert verdict.outcome == ComplianceOutcome.UNMEASURABLE


def test_quote_bearing_issue_with_no_recognised_cue_is_unmeasurable():
    """Quotes alone are not enough — without a recognised missing/forbidden/repeated
    cue, this must not be silently treated as a presence-add demand."""
    issue = "The phrase 'growth mindset' reads as filler and could be tightened."
    verdict = evaluate_compliance(issue, "growth mindset here", "growth mindset here still")
    assert verdict.outcome == ComplianceOutcome.UNMEASURABLE
    assert verdict.shape is None


def test_empty_issue_list_measures_to_nothing():
    assert measure_corrector_compliance([], "current", "next") == []


def test_only_blocking_issues_are_measured_minor_issues_excluded():
    """A minor issue never reaches the corrector (the severity gate ships the draft
    instead of retrying) — there is nothing for 'the next draft' to have implemented,
    so it must never appear in the measured verdicts at all."""
    issues = [
        ReviewIssue("VERIFIED COVERAGE CHECK — 'X' is not in the draft.", SEVERITY_BLOCKING),
        ReviewIssue("'X' is repeated 9 times in a single paragraph.", SEVERITY_MINOR),
    ]
    verdicts = measure_corrector_compliance(issues, "no X here", "now has X")
    assert len(verdicts) == 1
    assert verdicts[0].issue.startswith("VERIFIED COVERAGE")


# --------------------------------------------------------------------------
# Aggregation (deliverable 2)
# --------------------------------------------------------------------------


def test_aggregate_includes_every_signal_class_even_at_zero():
    agg = aggregate_by_signal_class([])
    assert set(agg) == set(SignalClass)
    for bucket in agg.values():
        assert bucket.total == 0


def test_aggregate_under_claim_bucket_is_always_zero():
    """No emitter reaches the reviewer prompt for this class today (module docstring) —
    the aggregate must always report it as a genuine, visible zero."""
    issues = [
        ReviewIssue("VERIFIED COVERAGE CHECK — 'X' is not in the draft.", SEVERITY_BLOCKING),
        ReviewIssue("DO NOT CLAIM — 'Y' is presented as something the candidate has done.", SEVERITY_BLOCKING),
        ReviewIssue("Fabricated bullet with no support.", SEVERITY_BLOCKING),
    ]
    verdicts = measure_corrector_compliance(issues, "no X, has Y", "has X now, still Y, same fabrication")
    agg = aggregate_by_signal_class(verdicts)
    assert agg[SignalClass.UNDER_CLAIM].total == 0


def test_aggregate_counts_by_outcome_within_a_class():
    issues = [
        ReviewIssue("VERIFIED COVERAGE CHECK — 'X' is not in the draft.", SEVERITY_BLOCKING),
        ReviewIssue("VERIFIED COVERAGE CHECK — 'Z' is not in the draft.", SEVERITY_BLOCKING),
    ]
    verdicts = measure_corrector_compliance(issues, "no X or Z here", "now has X but not the other term")
    agg = aggregate_by_signal_class(verdicts)
    bucket = agg[SignalClass.COVERAGE]
    assert bucket.implemented == 1
    assert bucket.not_implemented == 1
    assert bucket.total == 2
