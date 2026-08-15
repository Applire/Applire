# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for ``services/review_compliance.py`` (#537, ADR-076 clause 2):

* signal-class classification (deliverable 2) — best-effort, log-only;
* the three mechanically-checkable implementation-compliance shapes (deliverable 1) —
  quoted-term presence-add, quoted-term forbidden-claim-removal, and repetition-count
  reduction — plus the ``unmeasurable`` outcome for everything else;
* the forbidden-claim shape's structural one-sidedness and the ``indeterminate``
  outcome that keeps it from silently inflating a compliance fraction (the
  coordinator's finding on review) — plus a positive check that the OTHER two shapes
  are genuinely two-sided and need no such treatment;
* per-signal-class aggregation, including the ``under_claim`` known-empty class and
  the conservative/optimistic bound properties a nonzero ``indeterminate`` count forces.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.review_severity import SEVERITY_BLOCKING, SEVERITY_MINOR
from applire.services.review_compliance import (
    ComplianceOutcome,
    SignalClass,
    SignalClassBucket,
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


def test_forbidden_claim_shape_indeterminate_when_term_still_present():
    """Still present is deliberately NOT scored non-compliant, and deliberately NOT
    folded into `unmeasurable` either — the term may legitimately remain reframed as
    an honest aspiration (module docstring), so this is its OWN outcome,
    `indeterminate`, distinct from "no shape matched" (`unmeasurable`)."""
    issue = "DO NOT CLAIM — 'LegalTech' is presented as something the candidate has done."
    verdict = evaluate_compliance(
        issue, "has LegalTech experience", "While I have not worked in LegalTech directly..."
    )
    assert verdict.outcome == ComplianceOutcome.INDETERMINATE
    assert verdict.outcome != ComplianceOutcome.UNMEASURABLE
    assert verdict.shape == "forbidden_claim_removed"


def test_forbidden_claim_shape_never_returns_not_implemented():
    """Pins the shape's structural one-sidedness (the coordinator's finding): across a
    spread of still-present cases — a plain repeat, a possession restated, an honest
    aspiration reframe — this shape must NEVER produce NOT_IMPLEMENTED. If a future
    edit adds a branch that does, this test forces a conscious decision about it
    rather than letting the asymmetry silently disappear."""
    issue = "DO NOT CLAIM — 'LegalTech' is presented as something the candidate has done."
    still_present_variants = [
        "has LegalTech experience",  # unchanged from current
        "led the LegalTech integration project",  # possession restated differently
        "While I have not worked in LegalTech directly, my background transfers",  # honest reframe
        "wants to grow into LegalTech",  # aspiration
    ]
    for next_text in still_present_variants:
        verdict = evaluate_compliance(issue, "has LegalTech experience", next_text)
        assert verdict.outcome != ComplianceOutcome.NOT_IMPLEMENTED, next_text
        assert verdict.outcome == ComplianceOutcome.INDETERMINATE, next_text


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


def test_repetition_shape_is_genuinely_two_sided():
    """Unlike the forbidden-claim shape, repetition-reduction has no branch where a
    plausible-but-unresolvable case is routed toward `implemented` — both outcomes
    are reachable and neither is INDETERMINATE (checked as part of the coordinator's
    one-sidedness audit: this shape does not need the same treatment)."""
    issue = "'X' is repeated 3 times in a single paragraph."
    reduced = evaluate_compliance(issue, "X X X", "X")
    unchanged = evaluate_compliance(issue, "X X X", "X X X")
    assert reduced.outcome == ComplianceOutcome.IMPLEMENTED
    assert unchanged.outcome == ComplianceOutcome.NOT_IMPLEMENTED
    assert ComplianceOutcome.INDETERMINATE not in (reduced.outcome, unchanged.outcome)


def test_missing_term_shape_is_genuinely_two_sided():
    """Unlike the forbidden-claim shape, missing-term-add has no branch where a
    plausible-but-unresolvable case is routed toward `implemented` — both outcomes
    are reachable and neither is INDETERMINATE (checked as part of the coordinator's
    one-sidedness audit: this shape does not need the same treatment)."""
    issue = "VERIFIED COVERAGE CHECK — 'Budgetverantwortung' is not in the draft."
    added = evaluate_compliance(issue, "no mention", "now carries Budgetverantwortung")
    still_absent = evaluate_compliance(issue, "no mention", "still no mention")
    assert added.outcome == ComplianceOutcome.IMPLEMENTED
    assert still_absent.outcome == ComplianceOutcome.NOT_IMPLEMENTED
    assert ComplianceOutcome.INDETERMINATE not in (added.outcome, still_absent.outcome)


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
    assert bucket.indeterminate == 0
    assert bucket.total == 2


def test_aggregate_carries_indeterminate_as_its_own_counter_not_folded_into_unmeasurable():
    """The coordinator's fix: a still-present forbidden-claim verdict must land in
    its OWN `indeterminate` slot, never silently added to `unmeasurable` — the two
    are different reasons a verdict can't resolve, and merging them would hide the
    forbidden-claim shape's one-sided bias."""
    issues = [
        ReviewIssue(
            "DO NOT CLAIM — 'LegalTech' is presented as something the candidate has done.",
            SEVERITY_BLOCKING,
        ),
    ]
    verdicts = measure_corrector_compliance(
        issues, "has LegalTech experience", "still has LegalTech experience, unchanged"
    )
    bucket = aggregate_by_signal_class(verdicts)[SignalClass.PRESENCE]
    assert bucket.indeterminate == 1
    assert bucket.unmeasurable == 0
    assert bucket.not_implemented == 0
    assert bucket.total == 1


def test_lower_bound_rate_counts_indeterminate_against_compliance():
    """The conservative bound ADR-076 clause 2 migration decisions must be read
    against: every `indeterminate` verdict counts in the denominator but not the
    numerator, exactly like a genuine `not_implemented`."""
    bucket = SignalClassBucket(
        SignalClass.PRESENCE, implemented=1, not_implemented=0, indeterminate=1, unmeasurable=0
    )
    assert bucket.lower_bound_rate == pytest.approx(0.5)


def test_upper_bound_rate_excludes_indeterminate_entirely():
    """The optimistic bound: the SAME bucket as above, but `indeterminate` dropped
    from the denominator — a reader who computes only this number and calls it
    "the compliance rate" gets the bias the coordinator flagged."""
    bucket = SignalClassBucket(
        SignalClass.PRESENCE, implemented=1, not_implemented=0, indeterminate=1, unmeasurable=0
    )
    assert bucket.upper_bound_rate == pytest.approx(1.0)


def test_lower_bound_rate_is_strictly_below_upper_bound_rate_when_indeterminate_present():
    bucket = SignalClassBucket(
        SignalClass.COVERAGE, implemented=3, not_implemented=1, indeterminate=2, unmeasurable=0
    )
    assert bucket.lower_bound_rate < bucket.upper_bound_rate


def test_bound_rates_agree_when_indeterminate_is_zero():
    bucket = SignalClassBucket(
        SignalClass.COVERAGE, implemented=3, not_implemented=1, indeterminate=0, unmeasurable=5
    )
    assert bucket.lower_bound_rate == bucket.upper_bound_rate == pytest.approx(0.75)


def test_bound_rates_are_none_when_nothing_to_divide_by():
    bucket = SignalClassBucket(
        SignalClass.UNDER_CLAIM, implemented=0, not_implemented=0, indeterminate=0, unmeasurable=0
    )
    assert bucket.lower_bound_rate is None
    assert bucket.upper_bound_rate is None
