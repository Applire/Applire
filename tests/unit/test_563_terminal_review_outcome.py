# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#563 (D) — the terminal review's outcome reaches a reader.

Before this, the only readers of ``REVIEW_EXHAUSTED`` / ``REVIEW_CYCLE_DETECTED``
anywhere in the repository were their own producer and the unit tests pinning that
producer's log format (positive set exhausted 2026-09-04). ADR-076 clause 2's
*ship-and-report* disposition had a ship half and no report half.

Two mechanisms under test:

1. ``review_and_refine``'s ``on_settle`` hook (ADR-021 amended 2026-09-04) — the
   settle path, approval flag, open issues and rounds used, delivered exactly once,
   never able to change which draft ships.
2. ``services/terminal_review_outcome.py``'s mapping of that settle onto the ADR-039
   check vocabulary — ``pass`` / ``fail`` / ``not_applicable``, no fourth status.

No Docker, no DB, no real LLM.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.review_issues import ReviewSettle, normalize_issues  # noqa: E402
from applire.services.reviewer import review_and_refine  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    TERMINAL_REVIEW_CHECK_ID,
    build_terminal_review_check,
    settle_to_outcome,
)


@pytest.fixture
def mock_provider():
    return AsyncMock()


# ---------------------------------------------------------------------------
# 1. The loop hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_settle_is_not_called_when_absent_and_loop_is_unchanged(mock_provider):
    """Default None: the loop is byte-identical for every existing caller."""
    draft = {"summary": "a"}
    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}
    result = await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
    )
    assert result is draft


@pytest.mark.asyncio
async def test_on_settle_reports_the_disabled_path_with_path_none(mock_provider):
    """`max_retries<=0` keeps its distinguishing `path=None` — the ONLY way a
    consumer can tell 'the review layer did not run' from 'it ran and found
    nothing' (ADR-081 clause 9 'unknown, never 0')."""
    seen: list[ReviewSettle] = []
    await review_and_refine(
        source="src",
        draft={"summary": "a"},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=0,
        on_settle=seen.append,
    )
    assert len(seen) == 1
    assert seen[0].path is None
    assert seen[0].ran is False


@pytest.mark.asyncio
async def test_on_settle_reports_approved_once(mock_provider):
    seen: list[ReviewSettle] = []
    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}
    await review_and_refine(
        source="src",
        draft={"summary": "a"},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        on_settle=seen.append,
    )
    assert len(seen) == 1
    assert seen[0].path == "approved"
    assert seen[0].approved is True
    assert seen[0].blocking_issues == ()


@pytest.mark.asyncio
async def test_on_settle_reports_exhaustion_with_the_open_blocking_issues(mock_provider):
    """The #563 shape: retries spent, blocking findings still open."""
    seen: list[ReviewSettle] = []
    mock_provider.aparse_json.side_effect = [
        {"approved": False,
         "issues": [{"severity": "blocking", "issue": "LucaNet block omits the ownership limit"}],
         "feedback": "fix it"},
        {"summary": "b"},
    ]
    await review_and_refine(
        source="src",
        draft={"summary": "a"},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=1,
        on_settle=seen.append,
    )
    assert len(seen) == 1
    assert seen[0].path == "exhausted"
    assert seen[0].approved is False
    assert seen[0].blocking_issues == ("LucaNet block omits the ownership limit",)
    assert seen[0].rounds == 1


@pytest.mark.asyncio
async def test_on_settle_reports_minor_only_with_the_observations(mock_provider):
    """The severity gate settled it — a legitimate ship. This is where ADR-076
    clause 9's visibility-only findings surface."""
    seen: list[ReviewSettle] = []
    mock_provider.aparse_json.return_value = {
        "approved": False,
        "issues": [{"severity": "minor", "issue": "every bullet is quantified — reads mechanical"}],
        "feedback": "",
    }
    await review_and_refine(
        source="src",
        draft={"summary": "a"},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        on_settle=seen.append,
    )
    assert seen[0].path == "minor_only"
    assert seen[0].blocking_issues == ()
    assert seen[0].minor_issues == ("every bullet is quantified — reads mechanical",)


@pytest.mark.asyncio
async def test_a_raising_on_settle_never_breaks_the_loop(mock_provider):
    """ADR-021's never-raises contract extends to the hook — in the direction
    that loses the report, never the document."""
    draft = {"summary": "a"}
    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}

    def boom(_settle):
        raise RuntimeError("reporting blew up")

    result = await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        on_settle=boom,
    )
    assert result is draft


@pytest.mark.asyncio
async def test_on_settle_sees_the_draft_that_actually_ships(mock_provider):
    """The hook runs AFTER retain_if/required_fields/settle_guard, so its
    `settled` is the delivered draft, not the pre-selection one."""
    seen: list[ReviewSettle] = []
    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}
    await review_and_refine(
        source="src",
        draft={"summary": "a"},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "rev",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        settle_guard=lambda d, hist: {"summary": "guarded"},
        on_settle=seen.append,
    )
    assert seen[0].settled == {"summary": "guarded"}


# ---------------------------------------------------------------------------
# 2. The ADR-039 mapping
# ---------------------------------------------------------------------------


def _settle(path, *, approved=False, blocking=(), minor=(), rounds=1):
    return ReviewSettle(
        path=path,
        approved=approved,
        blocking_issues=tuple(blocking),
        minor_issues=tuple(minor),
        rounds=rounds,
        settled={},
    )


@pytest.mark.parametrize(
    "path,expected",
    [
        (None, "not_applicable"),
        ("reviewer_call_failed", "not_applicable"),
        ("approved", "pass"),
        ("minor_only", "pass"),
        ("generator_call_failed", "fail"),
        ("cycle_detected", "fail"),
        ("exhausted", "fail"),
    ],
)
def test_every_settle_path_maps_to_one_of_the_three_adr_039_statuses(path, expected):
    outcome = settle_to_outcome(
        _settle(path, approved=(path == "approved"), blocking=("open finding",) if expected == "fail" else ()),
        chain_id="cv_terminal_review",
    )
    check = build_terminal_review_check(outcome, previous=None, document="cv")
    assert check.status == expected
    assert check.id == TERMINAL_REVIEW_CHECK_ID


def test_a_fail_names_the_open_findings_in_details():
    outcome = settle_to_outcome(
        _settle("exhausted", blocking=("the LucaNet project bullet omits the ownership limitation",)),
        chain_id="cv_terminal_review",
    )
    check = build_terminal_review_check(outcome, previous=None, document="cv")
    assert check.status == "fail"
    assert "LucaNet" in (check.details or "")


def test_a_minor_only_pass_still_names_the_observations():
    """Otherwise a visibility-only clause-9 finding would have no reader at all —
    which is the only reason those checks can be visibility-only."""
    outcome = settle_to_outcome(
        _settle("minor_only", minor=("every bullet is quantified — reads mechanical",)),
        chain_id="cv_terminal_review",
    )
    check = build_terminal_review_check(outcome, previous=None, document="cv")
    assert check.status == "pass"
    assert "mechanical" in (check.details or "")


def test_no_outcome_and_no_previous_check_is_not_applicable_never_absent():
    """An absent check reads as a clean, complete audit of something never
    examined — the #634 failure class ADR-079 clause 4 answers."""
    check = build_terminal_review_check(None, previous=None, document="cv")
    assert check.status == "not_applicable"


def test_a_re_audit_without_a_fresh_outcome_carries_the_previous_check_forward():
    """The section editor re-audits without running a terminal review. Recomputing
    `not_applicable` there would let a later edit launder an exhausted review."""
    first = build_terminal_review_check(
        settle_to_outcome(_settle("exhausted", blocking=("open finding",)), chain_id="cv_terminal_review"),
        previous=None,
        document="cv",
    )
    carried = build_terminal_review_check(None, previous=first.model_dump(), document="cv")
    assert carried.status == "fail"
    assert carried.details == first.details


def test_a_fresh_outcome_always_wins_over_a_carried_one():
    stale = build_terminal_review_check(
        settle_to_outcome(_settle("exhausted", blocking=("old",)), chain_id="cv_terminal_review"),
        previous=None,
        document="cv",
    )
    fresh = build_terminal_review_check(
        settle_to_outcome(_settle("approved", approved=True), chain_id="cv_terminal_review"),
        previous=stale.model_dump(),
        document="cv",
    )
    assert fresh.status == "pass"


def test_the_details_are_bounded_so_a_verbose_reviewer_cannot_flood_the_report():
    outcome = settle_to_outcome(
        _settle("exhausted", blocking=tuple(f"finding number {i} " + "x" * 400 for i in range(20))),
        chain_id="cv_terminal_review",
    )
    check = build_terminal_review_check(outcome, previous=None, document="cv")
    assert len(check.details or "") <= 1200


def test_normalize_issues_still_produces_what_the_settle_reports():
    """The settle's issue texts are the loop's own normalized issues — not a
    second parse (ADR-066)."""
    issues = normalize_issues([{"severity": "minor", "issue": "tone"}, "unlabelled"])
    assert [i.text for i in issues if not i.is_blocking] == ["tone"]
    assert [i.text for i in issues if i.is_blocking] == ["unlabelled"]
