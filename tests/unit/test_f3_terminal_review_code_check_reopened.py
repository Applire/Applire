# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hazelnut build 3, WP-FR (delivery F3) — a terminal review whose own verdict was
clean must not read as "findings still open".

Daniel's letter (delivery run 2026-10-22, LLM log records 177–180): the terminal
reviewer APPROVED the letter twice with one minor note; each round was reopened only
by a deterministic CHECK finding (E068 ``checks_fn`` — the false `letter.language`
fact, A-9); the corrector reproduced an earlier draft and the loop stopped on
``cycle_detected``. ``cycle_detected`` was mapped to ``fail`` unconditionally, so the
`terminal-review` row said "stopped early … with findings still open … Open findings:
(the verdict named no issue text)" — "not ready" with nothing to fix.

The rule now (ADR-076 / ADR-039, WP-FR): the row's status follows the VERDICT. A fail
path whose last verdict raised no blocking finding is a ``pass`` that says a code check
reopened the draft (the check reports itself — its own ATS row, or the delivered-draft
note); a fail path whose verdict rejected the draft without naming anything is
``not_applicable`` (unknown), never an open finding. A verdict-bearing fail stays a fail.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.review_issues import ReviewSettle  # noqa: E402
from applire.services.reviewer import CheckFinding, review_and_refine  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    build_terminal_review_check,
    settle_to_outcome,
)

NO_ISSUE_TEXT = "(the verdict named no issue text)"


def _settle(path, *, approved=False, blocking=(), minor=(), rounds=2):
    return ReviewSettle(
        path=path, approved=approved, blocking_issues=tuple(blocking),
        minor_issues=tuple(minor), rounds=rounds, settled={},
    )


def _check(settle):
    outcome = settle_to_outcome(settle, chain_id="letter_terminal_review")
    return build_terminal_review_check(outcome, previous=None, document="letter")


@pytest.mark.parametrize("path", ["cycle_detected", "generator_call_failed", "review_malformed"])
def test_an_approving_verdict_reopened_by_a_code_check_is_a_pass(path):
    check = _check(_settle(path, approved=True, minor=("tone note",)))
    assert check.status == "pass", check.details
    assert NO_ISSUE_TEXT not in (check.details or "")
    assert "code check" in (check.details or "")
    assert "tone note" in (check.details or "")  # the observation still has a reader


def test_a_minor_only_verdict_reopened_by_a_code_check_is_a_pass():
    check = _check(_settle("cycle_detected", approved=False, minor=("tone note",)))
    assert check.status == "pass", check.details
    assert NO_ISSUE_TEXT not in (check.details or "")


@pytest.mark.parametrize("path", ["cycle_detected", "exhausted"])
def test_a_rejection_that_named_nothing_is_unknown_not_open(path):
    check = _check(_settle(path, approved=False))
    assert check.status == "not_applicable", check.details
    assert NO_ISSUE_TEXT not in (check.details or "")
    assert "Open findings" not in (check.details or "")


def test_a_verdict_with_a_blocking_finding_still_fails_on_a_cycle():
    check = _check(_settle("cycle_detected", blocking=("the closing paragraph is missing",)))
    assert check.status == "fail"
    assert "closing paragraph" in (check.details or "")


def test_a_blocking_finding_beside_minor_notes_still_fails_on_a_cycle():
    """Minor notes never make a verdict clean while it carries a blocking finding."""
    check = _check(_settle(
        "cycle_detected", blocking=("the closing paragraph is missing",), minor=("tone",),
    ))
    assert check.status == "fail"
    assert "closing paragraph" in (check.details or "")


def test_a_malformed_first_verdict_still_fails_with_its_reason():
    """#688's pin is untouched: no verdict at all is not a clean verdict."""
    check = _check(_settle("review_malformed", approved=False, rounds=1))
    assert check.status == "fail"
    assert "malformed" in (check.details or "").lower()


@pytest.mark.asyncio
async def test_the_daniel_shape_through_the_real_loop_reports_a_pass():
    """The loop, end to end with a stub provider: reviewer approves with a minor note,
    a blocking CHECK finding reopens every round, the corrector hands back the draft it
    was given → `cycle_detected`. The reported row is the verdict's: a pass."""
    provider = AsyncMock()
    verdict = {
        "approved": True,
        "issues": [{"severity": "minor", "issue": "rewrite the mentoring sentence in English"}],
        "feedback": "",
    }
    draft = {"body": {"paragraphs": ["Dear Hiring Manager,", "I also mentor two engineers."]}}
    provider.aparse_json.side_effect = [verdict, dict(draft), verdict, dict(draft)]
    settles: list[ReviewSettle] = []

    await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "REVIEW",
        reviewer_system="rev",
        provider=provider,
        max_retries=2,
        chain_id="letter_terminal_review",
        checks_fn=lambda d: [CheckFinding(check="letter.language", text="1 of 4 sentences …")],
        on_settle=settles.append,
    )

    assert [s.path for s in settles] == ["cycle_detected"]
    check = build_terminal_review_check(
        settle_to_outcome(settles[0], chain_id="letter_terminal_review"),
        previous=None, document="letter",
    )
    assert check.status == "pass", check.details
    assert NO_ISSUE_TEXT not in (check.details or "")
