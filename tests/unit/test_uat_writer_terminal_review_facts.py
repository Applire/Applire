# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""F-4 (#672 line 123) — the `terminal-review` check reports FACTS about the
DELIVERED document, not the last verdict's findings as if they were open.

The mechanism, measured rather than assumed. `review_and_refine` reviews draft N; when
the verdict carries a blocking finding the CORRECTOR runs in the SAME iteration and
produces draft N+1, and `reviewer.py:1018`'s `exhausted` return then delivers draft
N+1 — which no reviewer ever read (`CV_TERMINAL_REVIEW_MAX_RETRIES` is 1, so there is
no later reviewer iteration inside one invocation). Over the captured real-provider
terminal reviews of 2026-08-16…2026-09-19: in **46 of 46** documents whose last verdict
was un-approved with blocking findings the corrector ran after that verdict. The founder
UAT of 2026-09-20 read "the document was delivered unreviewed after 2 round(s). Open
findings: …" for four findings that were all present-and-corrected in the delivered
`tailored_data`.

Why DRAFT IDENTITY and not text presence drives the status: the presence rule was
measured and rejected. Of 181 blocking findings in those last rounds only 42 quote any
phrase at all, and where presence IS measurable it points both ways — a coverage finding
("the claimable keyword \"Performance-Optimierung\" is absent from the draft") is
RESOLVED once its quote appears, while an overstatement finding's quoted noun phrase
legitimately survives the rewrite. `services/review_compliance.py` documents exactly
that one-sidedness, which is why the per-finding detail comes from THAT instrument
(#537, already running on this adjacency every round) rather than from a new predicate.

The check is NOT silenced (do-not #1): every finding is still named, and no blocking
settle path may ever read as a clean pass.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.review_issues import ReviewSettle  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    CorrectionFacts,
    build_terminal_review_check,
    measure_correction,
    settle_to_outcome,
)

#: A draft shape the corrector plausibly emits, and its post-correction twin. The
#: overstatement is reworded (its quoted noun phrase SURVIVES — the polarity trap) and
#: the missing keyword is added (its quote APPEARS — the opposite polarity).
_REVIEWED = {
    "summary": "Owned the LucaNet rollout end to end.",
    "work": [{"bullets": ["Led the consolidation subproject."]}],
}
_CORRECTED = {
    "summary": "Ran the intercompany subproject of the LucaNet rollout.",
    "work": [{"bullets": ["Led the consolidation subproject.", "Performance-Optimierung der Abfragen."]}],
}

_FINDING_OVERSTATEMENT = (
    'The summary claims "Owned the LucaNet rollout end to end", but the profile records '
    "the Head of Accounting as responsible."
)
_FINDING_COVERAGE = (
    'The claimable keyword "Performance-Optimierung" is absent from the draft.'
)


def _settle(path, *, blocking=(), settled=None, approved=False, rounds=1):
    return ReviewSettle(
        path=path,
        approved=approved,
        blocking_issues=tuple(blocking),
        minor_issues=(),
        rounds=rounds,
        settled=settled if settled is not None else _CORRECTED,
    )


def _details(outcome):
    return build_terminal_review_check(outcome, previous=None, document="cv").details or ""


# --- the measured fact, both directions -------------------------------------


def test_a_correction_after_the_verdict_is_reported_as_unverified_never_open():
    outcome = settle_to_outcome(
        _settle("exhausted", blocking=(_FINDING_OVERSTATEMENT, _FINDING_COVERAGE)),
        chain_id="cv_terminal_review",
        reviewed_draft=_REVIEWED,
    )
    assert outcome.correction is not None
    assert outcome.correction.delivered_is_reviewed is False
    check = build_terminal_review_check(outcome, previous=None, document="cv")
    assert check.status == "not_applicable"
    details = check.details or ""
    assert "UNVERIFIED" in details
    assert "not re-reviewed" in details
    # do-not #1: the findings are still named, verbatim.
    assert "LucaNet" in details and "Performance-Optimierung" in details
    assert "Open findings" not in details
    assert "delivered unreviewed" not in details


def test_a_finding_raised_against_the_delivered_draft_is_still_a_fail():
    """THE ASSERTED BASELINE, and the guard: the status follows the MEASURED fact, not
    the settle path. Same `exhausted` path, same findings — but the delivered draft IS
    the reviewed one, so the findings genuinely stand and `fail` is the honest word."""
    outcome = settle_to_outcome(
        _settle("exhausted", blocking=(_FINDING_OVERSTATEMENT,), settled=_REVIEWED),
        chain_id="cv_terminal_review",
        reviewed_draft=_REVIEWED,
    )
    assert outcome.correction is not None
    assert outcome.correction.delivered_is_reviewed is True
    check = build_terminal_review_check(outcome, previous=None, document="cv")
    assert check.status == "fail"
    assert "Open findings" in (check.details or "")
    assert "LucaNet" in (check.details or "")


def test_the_measurement_overrides_the_settle_path_in_both_directions():
    """A path-only rule cannot produce these two answers, so nothing but the measured
    fact can make both of these assertions hold at once."""
    corrected = settle_to_outcome(
        _settle("cycle_detected", blocking=("x",)),
        chain_id="c", reviewed_draft=_REVIEWED,
    )
    not_corrected = settle_to_outcome(
        _settle("exhausted", blocking=("x",), settled=_REVIEWED),
        chain_id="c", reviewed_draft=_REVIEWED,
    )
    # `cycle_detected` defaults to fail without the measurement; measured, it is
    # unverified. `exhausted` defaults to unverified; measured, it is a fail.
    assert corrected.status == "not_applicable"
    assert not_corrected.status == "fail"


# --- the compliance FACTS, with their scope stated -------------------------


def test_the_compliance_counts_reach_the_details_and_name_their_own_scope():
    outcome = settle_to_outcome(
        _settle("exhausted", blocking=(_FINDING_OVERSTATEMENT, _FINDING_COVERAGE, "The tone is mechanical.")),
        chain_id="cv_terminal_review",
        reviewed_draft=_REVIEWED,
    )
    facts = outcome.correction
    assert facts is not None
    assert facts.measured + facts.unmeasurable == 3
    # The prose-only finding has no mechanically checkable shape and must be counted as
    # unmeasurable, never folded into a compliant or non-compliant bucket.
    assert facts.unmeasurable >= 1
    details = _details(outcome)
    assert "could decide" in details
    assert "no mechanically checkable shape" in details


def test_findings_with_no_checkable_shape_say_so_instead_of_reporting_a_fraction():
    outcome = settle_to_outcome(
        _settle("exhausted", blocking=("The tone is mechanical.", "The voice is uneven.")),
        chain_id="cv_terminal_review",
        reviewed_draft=_REVIEWED,
    )
    assert outcome.correction is not None and outcome.correction.measured == 0
    details = _details(outcome)
    assert "cannot be decided deterministically" in details
    assert "could decide" not in details


def test_no_compliance_is_measured_when_the_delivered_draft_is_the_reviewed_one():
    """There was no correction to measure — reporting zeros as if a correction had been
    checked would be an instrument claiming to have examined something it never saw."""
    facts = measure_correction(
        _settle("exhausted", blocking=(_FINDING_COVERAGE,), settled=_REVIEWED), _REVIEWED
    )
    assert facts.delivered_is_reviewed is True
    assert (facts.implemented, facts.not_implemented, facts.indeterminate, facts.unmeasurable) == (0, 0, 0, 0)


# --- the fold --------------------------------------------------------------


def test_an_approved_verdict_over_the_delivered_draft_supersedes_an_earlier_exhaustion():
    """The clause-3 re-entry loop exists to re-review an earlier round's correction.
    When it approved the DELIVERED document, the earlier round's findings have been
    re-read and cleared — reporting them (as open OR as unverified) is the same false
    alarm one level up."""
    earlier = settle_to_outcome(
        _settle("exhausted", blocking=(_FINDING_OVERSTATEMENT,)),
        chain_id="cv_terminal_review", reviewed_draft=_REVIEWED,
    )
    later = settle_to_outcome(
        _settle("approved", approved=True, settled=_CORRECTED),
        chain_id="cv_terminal_review", reviewed_draft=_CORRECTED,
    )
    folded = later.worse_of(earlier)
    assert folded.status == "pass"
    assert folded.rounds == earlier.rounds + later.rounds
    # order-independent
    assert earlier.worse_of(later).status == "pass"


def test_an_approval_that_was_not_over_the_delivered_draft_supersedes_nothing():
    """The asserted baseline for the rule above: the approval only clears the earlier
    round when the approved draft IS what shipped."""
    earlier = settle_to_outcome(
        _settle("exhausted", blocking=(_FINDING_OVERSTATEMENT,)),
        chain_id="cv_terminal_review", reviewed_draft=_REVIEWED,
    )
    later = settle_to_outcome(
        _settle("approved", approved=True, settled=_CORRECTED),
        chain_id="cv_terminal_review", reviewed_draft=_REVIEWED,
    )
    assert later.correction is not None and later.correction.delivered_is_reviewed is False
    assert later.worse_of(earlier).status != "pass"


def test_without_the_measurement_the_fold_is_unchanged():
    """Byte-identical for any producer that has not wired `reviewed_draft` — the letter
    until Lead C opts in (NOTE D-1)."""
    earlier = settle_to_outcome(_settle("exhausted", blocking=("open",)), chain_id="c")
    later = settle_to_outcome(_settle("approved", approved=True), chain_id="c")
    assert later.correction is None
    assert later.worse_of(earlier).status == "not_applicable"
    assert earlier.worse_of(later).status == "not_applicable"


# --- the reporting layer may never become a way to fail --------------------


def test_a_failing_measurement_never_costs_the_report(monkeypatch):
    import applire.services.terminal_review_outcome as mod

    def boom(*a, **k):
        raise RuntimeError("instrument exploded")

    monkeypatch.setattr(mod, "measure_correction", boom)
    outcome = mod.settle_to_outcome(
        _settle("exhausted", blocking=("open",)),
        chain_id="c", reviewed_draft=_REVIEWED,
    )
    assert outcome.correction is None
    # Falls back to the settle-path table, and still never reads as clean.
    assert outcome.status == "not_applicable"
    assert "UNVERIFIED" in _details(outcome)


def test_a_non_dict_settled_draft_cannot_crash_the_measurement():
    facts = measure_correction(_settle("exhausted", blocking=("open",), settled=None), _REVIEWED)
    assert facts.delivered_is_reviewed is False


# --- the schema contract ---------------------------------------------------


def test_no_fourth_status_value_was_introduced():
    """`ATSCheck.status` is read by two REST routes, two MCP tools, the ATS panel and
    E058 group 4 under a persisted-report back-compat contract."""
    from applire.schemas.ats import ATSCheck

    assert set(ATSCheck.model_fields["status"].annotation.__args__) == {
        "pass", "fail", "not_applicable"
    }


def test_correction_facts_default_to_not_measured():
    outcome = settle_to_outcome(_settle("exhausted", blocking=("open",)), chain_id="c")
    assert outcome.correction is None
    assert CorrectionFacts(delivered_is_reviewed=True).measured == 0
