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


# --------------------------------------------------------------------------
# Ceiling follow-up (2026-08-15): clause-4 token matching, corpus-derived cues,
# the grounded-presence proxy, the anchor shape, the structured-output shape,
# and the per-(class, shape) aggregate. Fixture texts are VERBATIM (or minimally
# trimmed) real reviewer issues from logs/llm/2026-08-15.jsonl and -08-14.jsonl —
# a fixture drawn from one prior incident is not a sample of the population.
# --------------------------------------------------------------------------


def test_term_matching_is_token_level_not_substring_german_compound():
    # Real corpus issue [44]: demands `Arbeitssicherheit` while its own text names
    # `Arbeitssicherheitsmanagement` — a substring check is satisfied by the compound.
    issue = "The body omits the required claimable term 'Arbeitssicherheit'."
    verdict = evaluate_compliance(
        issue, "", "Er verantwortet das Arbeitssicherheitsmanagement im Werk."
    )
    assert verdict.outcome is ComplianceOutcome.NOT_IMPLEMENTED
    verdict = evaluate_compliance(issue, "", "Er verantwortet die Arbeitssicherheit im Werk.")
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_term_matching_deutsch_is_not_satisfied_by_deutschland():
    issue = "The verified claimable keyword 'Deutsch' is absent from the draft."
    verdict = evaluate_compliance(issue, "", "Der Kandidat wohnt in Deutschland.")
    assert verdict.outcome is ComplianceOutcome.NOT_IMPLEMENTED


def test_term_matching_normalises_hyphen_and_space_variants():
    issue = "The claimable term 'ISO 45001' is absent from the draft."
    verdict = evaluate_compliance(issue, "", "Vorbereitung auf ISO-45001 im Werk.")
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_term_matching_hyphenated_compound_is_one_word():
    # Adversarial review 2b-adjacent: dash→space normalisation re-opened the compound
    # trap through the hyphen path. A hyphenated compound is ONE word — its parts do
    # not satisfy a bare-term demand, on either side of the hyphen.
    issue = "The claimable term 'Mail' is absent from the draft."
    verdict = evaluate_compliance(issue, "", "Bitte per E-Mail kontaktieren.")
    assert verdict.outcome is ComplianceOutcome.NOT_IMPLEMENTED
    issue = "The claimable term 'Sicherheit' is absent from the draft."
    verdict = evaluate_compliance(issue, "", "Wir bieten Instandhaltungs-Sicherheit.")
    assert verdict.outcome is ComplianceOutcome.NOT_IMPLEMENTED


def test_bare_unquoted_claimable_keyword_is_extracted():
    # Real corpus issue [02]: gpt-5.6-luna quotes nothing — the term is bare prose.
    issue = (
        "The verified claimable keyword Koblenz is absent from the draft; it is "
        "supported by the candidate's profile location."
    )
    verdict = evaluate_compliance(issue, "", "Standort Koblenz ist im Profil verankert.")
    assert verdict.shape == "missing_term_added"
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED
    verdict = evaluate_compliance(issue, "", "Kein Ortsbezug im Entwurf.")
    assert verdict.outcome is ComplianceOutcome.NOT_IMPLEMENTED


def test_identifies_as_absent_phrasing_is_extracted():
    issue = "The deterministic coverage check identifies Maschinendatenerfassung as absent."
    verdict = evaluate_compliance(issue, "", "Einführung der Maschinendatenerfassung an 14 Maschinen.")
    assert verdict.shape == "missing_term_added"
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_typographic_and_double_quotes_are_recognised():
    issue = "The keyword „Mittelstand“ is not mentioned in the draft."
    verdict = evaluate_compliance(issue, "", "Erfahrung im produzierenden Mittelstand.")
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_grounded_qualifier_turns_presence_demand_into_negative_only_proxy():
    # Real corpus issue [04]: presence is checkable, "in a grounded way" is not —
    # a present term may be keyword-stuffed (#250), so presence proves nothing.
    issue = (
        "The deterministic coverage check identifies 'Arbeitsvorbereitung' as absent, "
        "although the profile supports it. Surface this capability in a grounded way."
    )
    present = evaluate_compliance(issue, "", "Er verantwortet die Arbeitsvorbereitung.")
    assert present.shape == "grounded_term_present_proxy"
    assert present.outcome is ComplianceOutcome.INDETERMINATE
    absent = evaluate_compliance(issue, "", "Nichts davon steht im Entwurf.")
    assert absent.outcome is ComplianceOutcome.NOT_IMPLEMENTED


def test_grounded_proxy_shape_never_returns_implemented():
    # Pin the NEGATIVE_ONLY sidedness the same way the forbidden-claim pin works:
    # no draft text may ever produce IMPLEMENTED from the proxy shape.
    issue = (
        "The claimable term 'Supply Chain' is absent; surface this grounded "
        "cross-functional experience accurately."
    )
    for next_text in (
        "Supply Chain steht jetzt im Text.",
        "Supply Chain Supply Chain Supply Chain.",
        "Kein Treffer.",
        "",
    ):
        verdict = evaluate_compliance(issue, "", next_text)
        assert verdict.shape == "grounded_term_present_proxy"
        assert verdict.outcome is not ComplianceOutcome.IMPLEMENTED


def test_plain_presence_demand_without_qualifier_stays_two_sided():
    issue = "The claimable term 'Industrie 4.0' is absent from the draft."
    assert (
        evaluate_compliance(issue, "", "Teil der Industrie 4.0 Roadmap.").outcome
        is ComplianceOutcome.IMPLEMENTED
    )
    assert (
        evaluate_compliance(issue, "", "Nichts dergleichen.").outcome
        is ComplianceOutcome.NOT_IMPLEMENTED
    )


def test_anchor_shape_implemented_when_figure_and_entity_share_a_sentence():
    # Real corpus issue [19]-shape: figure must co-occur with its owner in-sentence.
    issue = (
        "The figure 90 is not anchored to Weberit in the same sentence as the "
        "site-leadership claim."
    )
    good = (
        "Bei Weberit habe ich den Gesamtstandort mit rund 90 Mitarbeitenden vertreten. "
        "Weitere Erfahrung liegt in der Fertigungssteuerung."
    )
    verdict = evaluate_compliance(issue, "", good)
    assert verdict.shape == "figure_anchored_in_sentence"
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_anchor_shape_not_implemented_when_figure_sentence_lacks_entity():
    issue = "The figure 90 is not anchored to Weberit in the same sentence."
    bad = (
        "Ich habe den Gesamtstandort mit rund 90 Mitarbeitenden vertreten. "
        "Bei Weberit leitete ich zwei Fertigungsbereiche."
    )
    verdict = evaluate_compliance(issue, "", bad)
    assert verdict.outcome is ComplianceOutcome.NOT_IMPLEMENTED


def test_anchor_shape_is_per_occurrence_one_anchored_figure_is_not_enough():
    # 2026-08-14 corpus: "The figures '4,1 %' … are not anchored to their employer
    # (Weberit …) in their respective sentences" — anchoring ONE of them must not
    # score the whole issue implemented.
    issue = (
        "The figures '38' and '90' are not anchored to their employer "
        "(Weberit Kunststofftechnik GmbH) in their respective sentences."
    )
    partially = (
        "Bei Weberit Kunststofftechnik GmbH führe ich 38 Mitarbeitende. "
        "Der Gesamtstandort umfasst 90 Mitarbeitende."
    )
    assert evaluate_compliance(issue, "", partially).outcome is ComplianceOutcome.NOT_IMPLEMENTED
    fully = (
        "Bei Weberit Kunststofftechnik GmbH führe ich 38 Mitarbeitende. "
        "Für Weberit Kunststofftechnik GmbH vertrat ich den Standort mit 90 Mitarbeitenden."
    )
    assert evaluate_compliance(issue, "", fully).outcome is ComplianceOutcome.IMPLEMENTED


def test_anchor_shape_without_extractable_figure_is_unmeasurable():
    # Real corpus issue [13]: a TERM-anchor demand ("this responsibility is not
    # anchored to Weberit") names no figure — stays unmeasurable rather than
    # half-checked. Known residual, documented in the #537 ceiling comment.
    issue = (
        "This position-owned responsibility is not anchored to Weberit in the same "
        "sentence as the resulting experience claim."
    )
    verdict = evaluate_compliance(issue, "", "Beliebiger Entwurf.")
    assert verdict.shape is None
    assert verdict.outcome is ComplianceOutcome.UNMEASURABLE


def test_anchor_shape_judgement_variant_stays_unmeasurable():
    # Real corpus issue [41]: "re-anchor to the recorded project context" — no
    # capitalized entity behind any anchor phrasing, so no mechanical check fires.
    issue = (
        "Re-anchor the project to the recorded project context without asserting "
        "the employer attribution, or remove that employer attribution."
    )
    assert evaluate_compliance(issue, "", "Text mit 14 Maschinen.").shape is None


def test_ungrounded_value_shape_requires_structured_output_flag():
    # Real corpus issue J3 (job_analysis): on prose the same demand must NOT get the
    # two-sided treatment — an aspiration reframe may keep the term legitimately.
    # next_text is stringify_draft output: ONE LEAF VALUE PER LINE, keys dropped.
    issue = 'The keyword "Mittelstand" is not present or clearly implied by the posting.'
    kept_leaves = "Produktionsleitung\nMittelstand\nLean-Produktion"
    prose = evaluate_compliance(issue, "", kept_leaves)
    assert prose.shape != "ungrounded_value_removed"
    structured = evaluate_compliance(issue, "", kept_leaves, structured_output=True)
    assert structured.shape == "ungrounded_value_removed"
    assert structured.outcome is ComplianceOutcome.NOT_IMPLEMENTED


def test_ungrounded_value_shape_is_genuinely_two_sided():
    issue = 'The seniority_level "Executive" is not stated or unambiguously implied.'
    removed = evaluate_compliance(issue, "", "Senior\nOperations", structured_output=True)
    assert removed.outcome is ComplianceOutcome.IMPLEMENTED
    kept = evaluate_compliance(issue, "", "Executive\nOperations", structured_output=True)
    assert kept.outcome is ComplianceOutcome.NOT_IMPLEMENTED


def test_ungrounded_value_shape_matches_exact_leaves_not_substrings():
    # Adversarial review, field-scope refutation: "Executive" corrected out of
    # seniority_level must not be scored NOT_IMPLEMENTED because the word survives
    # legitimately inside ANOTHER field's value. stringify_draft renders one leaf per
    # line, so exact-leaf comparison scopes the check to field values.
    issue = 'The seniority_level "Executive" is not stated or unambiguously implied.'
    other_field = "Senior\nSenior Executive Assistant\nExecutive calendar management"
    verdict = evaluate_compliance(issue, "", other_field, structured_output=True)
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_measure_corrector_compliance_threads_structured_output():
    issues = [
        ReviewIssue(
            text='The keyword "Lean Management" is not stated in the posting.',
            severity=SEVERITY_BLOCKING,
        )
    ]
    verdicts = measure_corrector_compliance(
        issues, "Lean Management", "Lean Management", structured_output=True
    )
    assert verdicts[0].shape == "ungrounded_value_removed"
    default = measure_corrector_compliance(issues, "Lean Management", "Lean Management")
    assert default[0].shape != "ungrounded_value_removed"


def test_every_verdict_carries_sidedness_matching_its_shape():
    from applire.services.review_compliance import CheckSidedness

    cases = {
        "missing_term_added": (
            "The claimable term 'Koblenz' is absent from the draft.",
            CheckSidedness.TWO_SIDED,
        ),
        "grounded_term_present_proxy": (
            "The claimable term 'Koblenz' is absent; surface it in a grounded way.",
            CheckSidedness.NEGATIVE_ONLY,
        ),
        "forbidden_claim_removed": (
            "Do not claim 'SAP Basis' — it is presented as a candidate competence.",
            CheckSidedness.POSITIVE_ONLY,
        ),
        "figure_anchored_in_sentence": (
            "The figure 14 is not anchored to Weberit in the same sentence.",
            CheckSidedness.TWO_SIDED,
        ),
    }
    for expected_shape, (issue, sidedness) in cases.items():
        verdict = evaluate_compliance(issue, "", "irrelevant")
        assert verdict.shape == expected_shape, verdict
        assert verdict.sidedness is sidedness
    unmeasurable = evaluate_compliance("Pure prose judgement.", "", "x")
    assert unmeasurable.sidedness is None


def test_availability_family_stays_unmeasurable():
    # 7 of the 11 unmeasurable issues in the 2026-08-15 corpus are this family:
    # no fixed target token exists, so no shape may pretend to grade it.
    issue = (
        "The body does not deliver the required availability content. No availability "
        "or notice-period information is provided, and the closing paragraph does not "
        "fold availability into the call to action."
    )
    verdict = evaluate_compliance(issue, "", "Ich bin ab sofort verfügbar.")
    assert verdict.outcome is ComplianceOutcome.UNMEASURABLE


def test_aggregate_by_shape_separates_one_sided_from_two_sided_counts():
    from applire.services.review_compliance import aggregate_by_shape

    verdicts = [
        evaluate_compliance(
            "The claimable term 'Koblenz' is absent from the draft.", "", "Koblenz."
        ),
        evaluate_compliance(
            "The claimable term 'Supply Chain' is absent; surface it in a grounded way.",
            "",
            "Supply Chain.",
        ),
        evaluate_compliance("Pure prose judgement with no shape.", "", "x"),
    ]
    by_shape = aggregate_by_shape(verdicts)
    keys = {shape for (_cls, shape) in by_shape}
    assert {"missing_term_added", "grounded_term_present_proxy", "none"} <= keys
    # Shape totals must sum to the class totals — nothing dropped, nothing doubled.
    assert sum(b.total for b in by_shape.values()) == len(verdicts)
    proxy_bucket = next(
        b for (_cls, shape), b in by_shape.items() if shape == "grounded_term_present_proxy"
    )
    assert proxy_bucket.indeterminate == 1
    assert proxy_bucket.implemented == 0


# --------------------------------------------------------------------------
# Adversarial-audit fixes (2026-08-15, second pass): each test pins one repro
# from the corpus audit of commit 1824d388.
# --------------------------------------------------------------------------


def test_possessive_apostrophes_are_not_quote_delimiters():
    # Audit Repro A: two genitive apostrophes captured everything between them and
    # swallowed the real quoted demand entirely.
    issue = (
        "The letter does not explicitly state the candidate's scope evidence as "
        "required by 'scope_positioning'. The candidate's attested scope must be "
        "stated prominently and honestly."
    )
    verdict = evaluate_compliance(issue, "", "Der Abschnitt scope_positioning ist da.")
    assert verdict.shape == "grounded_term_present_proxy"
    # the real quoted term was found and is present -> proxy INDETERMINATE, never a
    # false NOT_IMPLEMENTED from a garbage capture like "s scope evidence as required by"
    assert verdict.outcome is ComplianceOutcome.INDETERMINATE


def test_quoted_draft_sentence_is_not_a_demand_term():
    # Audit Repro B: the reviewer quotes the WHOLE current sentence as evidence; the
    # fix rewrites that sentence, so treating it as a required term inverts the verdict.
    issue = (
        "The sentence 'Rheinwerk Verpackungen fertigt hochwertige Kunststoff- und "
        "Verbundverpackungen für Konsumgüterkunden in Europa.' omits 'Lebensmittelkunden' "
        "from the job_description."
    )
    fixed = (
        "Rheinwerk Verpackungen fertigt hochwertige Kunststoff- und Verbundverpackungen "
        "für Konsumgüter- und Lebensmittelkunden in Europa."
    )
    verdict = evaluate_compliance(issue, "", fixed)
    assert verdict.shape == "missing_term_added"
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_waiver_bearing_issue_is_unmeasurable():
    # Audit Repro C: "waive it" semantics cannot be parsed mechanically — grading the
    # waived term as a demand produced a false NOT_IMPLEMENTED. Honest outcome: none.
    issue = (
        "The following claimable keywords are absent: 5S, Industrie 4.0. Grounding "
        "waiver: 'Budgetverantwortung' is already covered by the figure '6 Mio. €' in "
        "the draft, so waive it. Demand the remaining terms."
    )
    verdict = evaluate_compliance(issue, "", "5S und Industrie 4.0 stehen im Text.")
    assert verdict.shape is None
    assert verdict.outcome is ComplianceOutcome.UNMEASURABLE


def test_anchor_shape_scopes_figures_to_the_demanded_entity():
    # Audit Repro D: an issue narrating figures of SEVERAL employers demands an anchor
    # for one — the other employers' figures must not be checked against that entity.
    issue = (
        "The scope content is delivered for the candidate's 38-person Weberit "
        "responsibility and 90-person site-leadership cover, but the sentence about "
        "the 14-person scope does not name Rasselstein in the same sentence."
    )
    correctly_split = (
        "Bei Weberit führe ich 38 Mitarbeitende und vertrat den Standort mit 90 "
        "Mitarbeitenden. Bei Rasselstein verantwortete ich eine 14-köpfige Schicht."
    )
    verdict = evaluate_compliance(issue, "", correctly_split)
    assert verdict.shape == "figure_anchored_in_sentence"
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_anchor_entity_trailing_period_is_stripped():
    # Audit Repro F: entity captured at sentence end carried the period and the
    # token-boundary match then demanded a period the draft doesn't have there.
    issue = (
        "The OEE claim of 12 % needs its sentence to anchor explicitly to "
        "Weberit Kunststofftechnik GmbH."
    )
    good = "Bei Weberit Kunststofftechnik GmbH stieg die OEE um 12 % im Spritzguss."
    verdict = evaluate_compliance(issue, "", good)
    assert verdict.shape == "figure_anchored_in_sentence"
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_anchor_shape_does_not_split_on_colon():
    # Adversarial review 2b: the home-grown splitter severed "bei Weberit: … 90 …" —
    # the canonical split_sentences keeps a colon-elaborated statement together.
    issue = "The figure 90 is not anchored to Weberit in the same sentence."
    colon_style = (
        "Standortverantwortung bei Weberit: Führung von rund 90 Mitarbeitenden am "
        "Hauptstandort."
    )
    verdict = evaluate_compliance(issue, "", colon_style)
    assert verdict.outcome is ComplianceOutcome.IMPLEMENTED


def test_prominently_and_honestly_route_to_the_proxy_not_two_sided():
    # Audit Repro E — the dangerous direction: a stuffed keyword scored IMPLEMENTED
    # because "prominently"/"honestly" were missing from the qualifier cue.
    issue = (
        "The required scope evidence 'scope_positioning' is absent; the attested scope "
        "must be stated prominently and honestly."
    )
    stuffed = "Skills: Leadership, Operations, scope_positioning, ISO 9001."
    verdict = evaluate_compliance(issue, "", stuffed)
    assert verdict.shape == "grounded_term_present_proxy"
    assert verdict.outcome is ComplianceOutcome.INDETERMINATE
