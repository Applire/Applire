# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#306 (a) — unit tests for the deterministic reviewer-issue sanity check,
pinned against the VERBATIM issues from charter run #7, case 2
(``operations_marcus_de``, chain=cover_letter, reviewer attempt 5 of 5)."""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.review_issue_filter import (
    evaluate_issue,
    filter_reviewer_issues,
)

# The exact strings from backend/logs/llm/2026-07-27.jsonl, reviewer
# attempt 5, chain=cover_letter (case 2).
ISSUE_SELF_CONTRADICTION_1 = (
    "Paragraph 1: Invented employer fact — 'Verbundverpackungen' and "
    "'Lebensmittelkunden' are not in the job_description text (only "
    "'Kunststoff- und Verbundverpackungen für Konsumgüter- und "
    "Lebensmittelkunden' appears; 'Verbundverpackungen' and "
    "'Lebensmittelkunden' are DO NOT CLAIM terms and cannot be used as "
    "employer facts)."
)
ISSUE_SELF_CONTRADICTION_2 = (
    "Paragraph 1: Invented employer fact — 'Konsumgüter- und "
    "Lebensmittelkunden' is not in the job_description text (only "
    "'Konsumgüter- und Lebensmittelkunden' appears as a single phrase; "
    "splitting it into separate claims is ungrounded)."
)
ISSUE_REPEATED_WRONG_COUNT = (
    "Paragraph 2: Repetitive employer naming — 'Bei Weberit Kunststofftechnik "
    "GmbH' is repeated 6 times in a single paragraph, which is stylistically "
    "poor but not a grounding issue. However, the unanchored content issue "
    "(above) is the primary concern."
)
ISSUE_MINTED_BUT_GROUNDED = (
    "Paragraph 2: Minted figure — '38 Mitarbeitenden' is grounded, but the "
    "repetition of the employer name without anchoring each achievement is "
    "the issue."
)
ISSUE_ALLOWED_GAP = (
    "Paragraph 3: DO NOT CLAIM term used as candidate competence — "
    "'Lebensmittelkunden' is a DO NOT CLAIM term and is presented as a "
    "candidate limitation (honest gap), which is allowed. However, the "
    "phrasing 'da ich bisher nicht direkt für Lebensmittelkunden produziert "
    "habe' is acceptable as it names the gap, not a competence claim."
)
# Two issues from the SAME round the run's own account calls genuine — must
# survive every check untouched.
ISSUE_GENUINE_MISSING_POSITIONING = (
    "Paragraph 3: Missing required positioning content — The gap/transfer "
    "argument for 'Digitalisierung' (from unaddressed_hard_requirements) is "
    "not explicitly addressed. The letter mentions MES and Industrie 4.0, "
    "but does not frame it as a transfer argument for the gap."
)
ISSUE_GENUINE_UNSUPPORTED_GENERALIZATION = (
    "Paragraph 3: Unsupported generalization — 'Meine Erfahrung in der "
    "Digitalisierung der Fertigung' is a generic statement that does not "
    "trace to a specific, sourced claim about the candidate. Replace with a "
    "concrete, grounded achievement."
)

# Paragraph 2 of the round-4 draft this reviewer round actually looked at —
# "Bei Weberit Kunststofftechnik GmbH" occurs 4 times, never 6.
DRAFT_TEXT_ROUND_4 = (
    "Bei der Weberit Kunststofftechnik GmbH führe ich seit April 2017 zwei "
    "Fertigungsbereiche. Bei Weberit Kunststofftechnik GmbH senkte ich die "
    "Ausschussquote. Bei Weberit Kunststofftechnik GmbH begleitete ich die "
    "ISO-45001-Zertifizierung. Als Projektleiter führte ich bei Weberit "
    "Kunststofftechnik GmbH ein MES-System ein. Bei Weberit Kunststofftechnik "
    "GmbH moderierte ich Kaizen-Workshops. Bei Weberit Kunststofftechnik GmbH "
    "bin ich Schnittstelle zu Einkauf."
)


def test_self_contradiction_1_is_discarded():
    verdict = evaluate_issue(ISSUE_SELF_CONTRADICTION_1, "")
    assert verdict.discard is True
    assert verdict.reason == "self_refuting"


def test_self_contradiction_2_byte_identical_quotes_is_discarded():
    verdict = evaluate_issue(ISSUE_SELF_CONTRADICTION_2, "")
    assert verdict.discard is True
    assert verdict.reason == "self_refuting"


def test_minted_but_grounded_is_discarded():
    verdict = evaluate_issue(ISSUE_MINTED_BUT_GROUNDED, "")
    assert verdict.discard is True
    assert verdict.reason == "self_refuting"


def test_wrong_repeat_count_is_discarded():
    # 6 is claimed, actual (in DRAFT_TEXT_ROUND_4) is different.
    assert DRAFT_TEXT_ROUND_4.count("Bei Weberit Kunststofftechnik GmbH") != 6
    verdict = evaluate_issue(ISSUE_REPEATED_WRONG_COUNT, DRAFT_TEXT_ROUND_4)
    assert verdict.discard is True
    # Wrong-count fires first in this string (before the non-blocking cue is
    # even reached) since checks run in order — either reason is a correct
    # discard, but pin the actual one so a reordering regression is caught.
    assert verdict.reason == "wrong_count"


def test_correct_repeat_count_is_not_discarded_by_count_check():
    draft = "'X' appears once. " * 1
    issue = "'X' is repeated 1 times in a single paragraph."
    verdict = evaluate_issue(issue, draft)
    assert verdict.discard is False


def test_self_annotated_allowed_gap_is_discarded():
    verdict = evaluate_issue(ISSUE_ALLOWED_GAP, "")
    assert verdict.discard is True
    assert verdict.reason == "self_annotated_non_blocking"


def test_genuine_missing_positioning_content_survives():
    verdict = evaluate_issue(ISSUE_GENUINE_MISSING_POSITIONING, "")
    assert verdict.discard is False


def test_genuine_unsupported_generalization_survives():
    verdict = evaluate_issue(ISSUE_GENUINE_UNSUPPORTED_GENERALIZATION, "")
    assert verdict.discard is False


def test_filter_reviewer_issues_end_to_end_matches_run_7_case_2():
    """The full attempt-5 issue batch (11 issues): the 5 verbatim
    false/non-blocking ones are discarded, the 2 the run calls genuine
    survive, and nothing is invented (survivors is always a SUBSET)."""
    issues = [
        ISSUE_SELF_CONTRADICTION_1,
        ISSUE_SELF_CONTRADICTION_2,
        ISSUE_REPEATED_WRONG_COUNT,
        ISSUE_MINTED_BUT_GROUNDED,
        ISSUE_ALLOWED_GAP,
        ISSUE_GENUINE_MISSING_POSITIONING,
        ISSUE_GENUINE_UNSUPPORTED_GENERALIZATION,
    ]
    survivors, verdicts = filter_reviewer_issues(issues, DRAFT_TEXT_ROUND_4)

    assert set(survivors) <= set(issues)  # never invents an issue
    assert ISSUE_GENUINE_MISSING_POSITIONING in survivors
    assert ISSUE_GENUINE_UNSUPPORTED_GENERALIZATION in survivors
    assert ISSUE_SELF_CONTRADICTION_1 not in survivors
    assert ISSUE_SELF_CONTRADICTION_2 not in survivors
    assert ISSUE_REPEATED_WRONG_COUNT not in survivors
    assert ISSUE_MINTED_BUT_GROUNDED not in survivors
    assert ISSUE_ALLOWED_GAP not in survivors
    assert len(survivors) == 2
    assert len(verdicts) == len(issues)


def test_all_issues_discarded_when_every_one_is_noise():
    issues = [ISSUE_SELF_CONTRADICTION_1, ISSUE_MINTED_BUT_GROUNDED, ISSUE_ALLOWED_GAP]
    survivors, _ = filter_reviewer_issues(issues, "")
    assert survivors == []


def test_empty_issues_list_is_a_noop():
    survivors, verdicts = filter_reviewer_issues([], "any draft text")
    assert survivors == []
    assert verdicts == []


def test_plain_unrelated_issue_text_survives():
    """A short, plain issue with no quotes/counts/non-blocking cues (the
    existing test suite's fixtures, e.g. 'a', 'b') must never be discarded —
    this filter must be a no-op on ordinary issue text."""
    verdict = evaluate_issue("Missing closing paragraph.", "some draft")
    assert verdict.discard is False
