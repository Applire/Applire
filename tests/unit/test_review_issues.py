# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the reviewer-issue layer (``services/review_issues.py``):

* severity parsing (ADR-021 amended 2026-07-28) — including every degenerate
  shape a real model produces, all of which must fail SAFE (blocking);
* the deterministic soundness measurement (#306 (a)), pinned against the
  VERBATIM issues from charter run #7, case 2 (``operations_marcus_de``,
  chain=cover_letter, reviewer attempt 5 of 5).
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.review_severity import SEVERITY_BLOCKING, SEVERITY_MINOR
from applire.services.review_issues import (
    ReviewIssue,
    evaluate_issue,
    measure_reviewer_issues,
    normalize_issues,
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
# be measured SOUND by every check.
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


# --------------------------------------------------------------------------
# Severity parsing — ADR-021 amended 2026-07-28
# --------------------------------------------------------------------------


def test_schema_shape_is_parsed():
    issues = normalize_issues(
        [
            {"severity": "blocking", "issue": "Invented employer."},
            {"severity": "minor", "issue": "Repetitive phrasing."},
        ]
    )
    assert issues == [
        ReviewIssue("Invented employer.", SEVERITY_BLOCKING),
        ReviewIssue("Repetitive phrasing.", SEVERITY_MINOR),
    ]


def test_severity_matching_is_case_and_whitespace_insensitive():
    issues = normalize_issues([{"severity": "  Minor ", "issue": "x"}])
    assert issues[0].severity == SEVERITY_MINOR


def test_pre_severity_plain_strings_read_as_blocking():
    """The pre-amendment shape (and any model that ignores the instruction)
    must behave EXACTLY as it did before severity existed — a rewrite."""
    issues = normalize_issues(["Invented employer.", "Wrong date."])
    assert [i.severity for i in issues] == [SEVERITY_BLOCKING, SEVERITY_BLOCKING]
    assert all(i.is_blocking for i in issues)


def test_unreadable_severity_fails_safe_to_blocking():
    """Absent, null, unknown, or non-string severity — every one of these is a
    model getting the field wrong, and every one must still cost a rewrite
    rather than silently skip one."""
    for entry in (
        {"issue": "x"},
        {"severity": None, "issue": "x"},
        {"severity": "critical", "issue": "x"},
        {"severity": "MINOR-ISH", "issue": "x"},
        {"severity": 1, "issue": "x"},
        {"severity": ["minor"], "issue": "x"},
    ):
        assert normalize_issues([entry])[0].severity == SEVERITY_BLOCKING, entry


def test_alternative_prose_keys_are_read():
    for key in ("issue", "text", "description", "detail", "message"):
        issues = normalize_issues([{"severity": "minor", key: "the prose"}])
        assert issues[0].text == "the prose", key


def test_dict_with_no_recognised_prose_key_is_kept_not_dropped():
    issues = normalize_issues([{"severity": "minor", "whatever": "the prose"}])
    assert len(issues) == 1
    assert "the prose" in issues[0].text


def test_mixed_adoption_is_tolerated():
    issues = normalize_issues(["plain string", {"severity": "minor", "issue": "object"}])
    assert [i.severity for i in issues] == [SEVERITY_BLOCKING, SEVERITY_MINOR]


def test_non_list_issues_value_yields_nothing():
    for raw in (None, "a string", {"severity": "minor"}, 7):
        assert normalize_issues(raw) == []


# --------------------------------------------------------------------------
# Soundness measurement — #306 (a), measurement only since 2026-07-28
# --------------------------------------------------------------------------


def test_self_contradiction_1_is_unsound():
    verdict = evaluate_issue(ISSUE_SELF_CONTRADICTION_1, "")
    assert verdict.unsound is True
    assert verdict.reason == "self_refuting"


def test_self_contradiction_2_byte_identical_quotes_is_unsound():
    verdict = evaluate_issue(ISSUE_SELF_CONTRADICTION_2, "")
    assert verdict.unsound is True
    assert verdict.reason == "self_refuting"


def test_minted_but_grounded_is_unsound():
    verdict = evaluate_issue(ISSUE_MINTED_BUT_GROUNDED, "")
    assert verdict.unsound is True
    assert verdict.reason == "self_refuting"


def test_wrong_repeat_count_is_unsound():
    assert DRAFT_TEXT_ROUND_4.count("Bei Weberit Kunststofftechnik GmbH") != 6
    verdict = evaluate_issue(ISSUE_REPEATED_WRONG_COUNT, DRAFT_TEXT_ROUND_4)
    assert verdict.unsound is True
    assert verdict.reason == "wrong_count"


def test_correct_repeat_count_is_sound():
    draft = "'X' appears once. "
    issue = "'X' is repeated 1 times in a single paragraph."
    assert evaluate_issue(issue, draft).unsound is False


def test_self_annotated_non_blocking_is_no_longer_a_soundness_check():
    """Check 3 was removed when severity landed: reading "which is allowed" out
    of English prose was inferring, badly, the field the reviewer now sets. The
    issue is measured SOUND here — the severity field is what makes it
    non-blocking, and that is the reviewer's call to make explicitly."""
    verdict = evaluate_issue(ISSUE_ALLOWED_GAP, "")
    assert verdict.unsound is False
    assert normalize_issues([{"severity": "minor", "issue": ISSUE_ALLOWED_GAP}])[0].severity == (
        SEVERITY_MINOR
    )


def test_genuine_missing_positioning_content_is_sound():
    assert evaluate_issue(ISSUE_GENUINE_MISSING_POSITIONING, "").unsound is False


def test_genuine_unsupported_generalization_is_sound():
    assert evaluate_issue(ISSUE_GENUINE_UNSUPPORTED_GENERALIZATION, "").unsound is False


def test_measurement_over_run_7_case_2_batch():
    """The attempt-5 batch: 4 of the verbatim issues are demonstrably unsound,
    the 2 the run calls genuine are sound, and the self-annotated one is no
    longer judged by prose. Measurement returns a COUNT — it changes nothing."""
    texts = [
        ISSUE_SELF_CONTRADICTION_1,
        ISSUE_SELF_CONTRADICTION_2,
        ISSUE_REPEATED_WRONG_COUNT,
        ISSUE_MINTED_BUT_GROUNDED,
        ISSUE_ALLOWED_GAP,
        ISSUE_GENUINE_MISSING_POSITIONING,
        ISSUE_GENUINE_UNSUPPORTED_GENERALIZATION,
    ]
    issues = normalize_issues(texts)
    unsound, verdicts = measure_reviewer_issues(issues, DRAFT_TEXT_ROUND_4)

    assert len(verdicts) == len(texts)
    assert unsound == 4
    by_text = {v.issue: v for v in verdicts}
    assert by_text[ISSUE_GENUINE_MISSING_POSITIONING].unsound is False
    assert by_text[ISSUE_GENUINE_UNSUPPORTED_GENERALIZATION].unsound is False
    assert by_text[ISSUE_ALLOWED_GAP].unsound is False


def test_empty_issue_list_measures_to_nothing():
    assert measure_reviewer_issues([], "any draft text") == (0, [])


def test_plain_unrelated_issue_text_is_sound():
    """A short, plain issue with no quotes or counts must never be called
    unsound — the measurement must be a no-op on ordinary issue text."""
    assert evaluate_issue("Missing closing paragraph.", "some draft").unsound is False
