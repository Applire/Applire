# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for ``services/signal_disposition.py`` (#537, ADR-076 clause 2's floor):

the exhaustion-disposition registry. Scaffolding only — nothing in the pipeline calls
this yet (no SIGNAL has migrated), so these tests exercise the registration/lookup
contract itself: a signal that never registered must fail LOUDLY, never silently.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.signal_disposition import (
    ExhaustionDisposition,
    UndeclaredSignalDispositionError,
    _reset_registry_for_tests,
    get_signal_disposition,
    register_signal_disposition,
    registered_signal_ids,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-global by design (a signal registers itself once, like
    a plugin) — isolate each test so a fixture registration cannot leak."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _noop_fallback(*args, **kwargs):
    return None


def test_unregistered_signal_raises_visibly_not_silently():
    with pytest.raises(UndeclaredSignalDispositionError):
        get_signal_disposition("cv._restore_ledger_bullets")


def test_unregistered_signal_error_names_the_signal_and_the_clause():
    with pytest.raises(UndeclaredSignalDispositionError) as exc_info:
        get_signal_disposition("cv._restore_ledger_bullets")
    message = str(exc_info.value)
    assert "cv._restore_ledger_bullets" in message
    assert "ADR-076" in message


def _noop_issue_matches(text: str) -> bool:
    return False


def test_fallback_apply_registration_round_trips():
    register_signal_disposition(
        "cv._restore_ledger_bullets",
        ExhaustionDisposition.FALLBACK_APPLY,
        "keeps the deterministic owner-lookup/rank_cuts machinery as the bounded "
        "sanctioned exception on exhaustion (ADR-076 clause 2 split-fate example).",
        fallback_fn=_noop_fallback,
        issue_matches=_noop_issue_matches,
    )
    record = get_signal_disposition("cv._restore_ledger_bullets")
    assert record.disposition == ExhaustionDisposition.FALLBACK_APPLY
    assert record.fallback_fn is _noop_fallback
    assert record.issue_matches is _noop_issue_matches
    assert record.rationale


def test_fallback_apply_without_issue_matches_is_rejected():
    """2026-08-15 amendment (#540): a fallback-apply signal needs a matcher to know
    whether ITS issue is still outstanding at settle time — without one there is no
    way to wire the fallback to fire only when it should."""
    with pytest.raises(ValueError, match="issue_matches"):
        register_signal_disposition(
            "cv._restore_ledger_bullets_2",
            ExhaustionDisposition.FALLBACK_APPLY,
            "a rationale and a fallback_fn, but no matcher.",
            fallback_fn=_noop_fallback,
        )


def test_ship_and_report_registration_round_trips():
    register_signal_disposition(
        "cover_letter.under_claim_signal",
        ExhaustionDisposition.SHIP_AND_REPORT,
        "the defect ships and is named in the reports at the send seat (ADR-076 "
        "clause 7) rather than patched by a fallback write.",
    )
    record = get_signal_disposition("cover_letter.under_claim_signal")
    assert record.disposition == ExhaustionDisposition.SHIP_AND_REPORT
    assert record.fallback_fn is None


def test_fallback_apply_without_fallback_fn_is_rejected():
    with pytest.raises(ValueError, match="fallback_fn"):
        register_signal_disposition(
            "cv._prefer_measured_outcomes",
            ExhaustionDisposition.FALLBACK_APPLY,
            "a rationale that is present but points at nothing callable.",
        )


def test_ship_and_report_with_fallback_fn_is_rejected():
    """A ship-and-report signal must never carry a hidden write path — that would be
    exactly the silent second write ADR-076 clause 2 forbids."""
    with pytest.raises(ValueError, match="fallback_fn"):
        register_signal_disposition(
            "cover_letter.under_claim_signal",
            ExhaustionDisposition.SHIP_AND_REPORT,
            "a rationale, but wrongly paired with a fallback.",
            fallback_fn=_noop_fallback,
        )


def test_empty_rationale_is_rejected():
    with pytest.raises(ValueError, match="rationale"):
        register_signal_disposition(
            "cv._dedup_skills", ExhaustionDisposition.SHIP_AND_REPORT, ""
        )


def test_whitespace_only_rationale_is_rejected():
    with pytest.raises(ValueError, match="rationale"):
        register_signal_disposition(
            "cv._dedup_skills", ExhaustionDisposition.SHIP_AND_REPORT, "   \n  "
        )


def test_registered_signal_ids_reflects_registrations():
    assert registered_signal_ids() == frozenset()
    register_signal_disposition(
        "cv._tailor_skills_to_jd",
        ExhaustionDisposition.SHIP_AND_REPORT,
        "the #386 coupled-pass pair migrates together per ADR-076 clause 2.",
    )
    assert registered_signal_ids() == frozenset({"cv._tailor_skills_to_jd"})


def test_reregistering_the_same_signal_overwrites_not_duplicates():
    register_signal_disposition(
        "cv._dedup_skills",
        ExhaustionDisposition.SHIP_AND_REPORT,
        "initial declaration.",
    )
    register_signal_disposition(
        "cv._dedup_skills",
        ExhaustionDisposition.FALLBACK_APPLY,
        "revised declaration during development.",
        fallback_fn=_noop_fallback,
        issue_matches=_noop_issue_matches,
    )
    assert registered_signal_ids() == frozenset({"cv._dedup_skills"})
    record = get_signal_disposition("cv._dedup_skills")
    assert record.disposition == ExhaustionDisposition.FALLBACK_APPLY
    assert record.rationale == "revised declaration during development."


def test_registry_ships_empty_no_pass_migrates_in_537():
    """#537's own brief: scaffolding plus enforcement, not a behaviour change — no
    signal is pre-registered by this issue."""
    assert registered_signal_ids() == frozenset()
