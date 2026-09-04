# Copyright (C) 2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Unit tests for ADR-083 clause 4 (``services/corrector_feedback.py``): the
reviewer's normalized BLOCKING issues, rendered into a corrector-facing block
and folded into the ``feedback`` string handed to ``generator_prompt_fn`` at
the one shared call site in ``services/reviewer.py``.

The seam through ``review_and_refine`` itself — proving all five sharing
chains inherit this, and the back-compat guarantee on the argument actually
passed — is covered in ``test_reviewer.py``, not here; this file tests the
renderer and the fold in isolation.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.corrector_feedback import (
    fold_issues_into_feedback,
    render_blocking_issues,
)
from applire.services.review_issues import ReviewIssue, normalize_issues


def _blocking(text: str) -> ReviewIssue:
    return ReviewIssue(text=text, severity="blocking")


def _minor(text: str) -> ReviewIssue:
    return ReviewIssue(text=text, severity="minor")


# ---------------------------------------------------------------------------
# render_blocking_issues — blocking-only filtering, empty in/out
# ---------------------------------------------------------------------------


def test_empty_issues_render_to_empty_string():
    assert render_blocking_issues([]) == ""


def test_minor_only_issues_render_to_empty_string():
    """Constraint 1: the loop itself never reaches the corrector call site with
    minor-only issues (it settles earlier, see reviewer.py's `if issues and not
    blocking` gate) — but the renderer is defensively correct even if handed an
    unfiltered list directly."""
    issues = [_minor("Repetitive phrasing."), _minor("Could be punchier.")]
    assert render_blocking_issues(issues) == ""


def test_blocking_issues_are_rendered_and_minor_ones_are_not():
    issues = [
        _blocking("Paragraph 2 states a figure the source does not carry."),
        _minor("Slightly repetitive wording in paragraph 1."),
        _blocking("The closing paragraph omits the required call to action."),
    ]
    block = render_blocking_issues(issues)
    assert "Paragraph 2 states a figure the source does not carry." in block
    assert "The closing paragraph omits the required call to action." in block
    assert "Slightly repetitive wording in paragraph 1." not in block


def test_renders_from_real_normalize_issues_output():
    """Integration with the actual parser, not just hand-built ReviewIssue
    objects — the schema shape a real reviewer verdict uses."""
    issues = normalize_issues(
        [
            {"severity": "blocking", "issue": "Invented employer fact."},
            {"severity": "minor", "issue": "Wordy opening sentence."},
        ]
    )
    block = render_blocking_issues(issues)
    assert "Invented employer fact." in block
    assert "Wordy opening sentence." not in block


# ---------------------------------------------------------------------------
# Corrector-audience wording (constraint 2) — not the codebase's usual
# code-computed "ground truth" framing (PINNED FACTS CHECK, VERIFIED COVERAGE
# CHECK and siblings), and not a restatement of the reviewer's audit process.
# ---------------------------------------------------------------------------


def test_block_is_worded_as_an_instruction_not_an_audit():
    block = render_blocking_issues([_blocking("Wrong start date.")])
    low = block.lower()
    assert "fix" in low
    assert "ground truth" not in low
    assert "do not re-derive" not in low
    assert "verified" not in low


# ---------------------------------------------------------------------------
# fold_issues_into_feedback — back-compat (constraint 4) and ordering
# (constraint 3: prose stays, and stays first)
# ---------------------------------------------------------------------------


def test_fold_with_no_issues_leaves_feedback_untouched():
    assert fold_issues_into_feedback("Address the date discrepancy.", []) == (
        "Address the date discrepancy."
    )


def test_fold_with_only_minor_issues_leaves_feedback_untouched():
    issues = [_minor("Could tighten the second sentence.")]
    assert fold_issues_into_feedback("Address the date discrepancy.", issues) == (
        "Address the date discrepancy."
    )


def test_fold_with_no_issues_and_empty_feedback_stays_empty():
    assert fold_issues_into_feedback("", []) == ""


def test_fold_appends_block_after_prose_with_a_blank_line():
    issues = [_blocking("Missing required certification date.")]
    result = fold_issues_into_feedback("Address the date discrepancy.", issues)
    block = render_blocking_issues(issues)
    assert result == f"Address the date discrepancy.\n\n{block}"
    # Prose stays first (constraint 3).
    assert result.startswith("Address the date discrepancy.")


def test_fold_with_empty_feedback_but_a_blocking_issue_returns_block_alone():
    """The coverage gap this clause exists to close: today an empty `feedback`
    with a blocking issue outstanding hands the corrector nothing at all."""
    issues = [_blocking("Missing required certification date.")]
    result = fold_issues_into_feedback("", issues)
    assert result == render_blocking_issues(issues)
    assert not result.startswith("\n")
