# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:47 (founder 2026-10-22, Hazelnut build 4) — employer-name repetition as a
deterministic letter finding in the ATS audit (ADR-082: detect, never repair).

The bar is #565's credit bar: at most ONE sentence-initial employer mention per
body paragraph. Measured 2026-10-24 over every stored letter of build 3 and
ux-audit-2 (8 letters, 0 firings) and the 2026-08-19 ship-gate letter both
blind reviewers called machine-written (fires: 4 + 4 openings)."""
import inspect

from applire.services.ats_audit import (
    EMPLOYER_REPETITION_CHECK_ID,
    _audit_letter_text,
    employer_openings,
    profile_employer_names,
)

SALUTATION = "Sehr geehrte Damen und Herren,"
# The 2026-08-19 shape (synthetic wording): every sentence opens on the employer.
ANCHORED = (
    "Bei der Schwarzwald Präzision GmbH verantworte ich das Monatsreporting. "
    "Bei der Schwarzwald Präzision GmbH habe ich die Planung auf LucaNet umgestellt. "
    "Bei Schwarzwald Präzision betreue ich zwei Werkstudierende."
)
# The shape #565 asks for: one anchor, then back-references.
BACK_REFERENCED = (
    "Bei der Schwarzwald Präzision GmbH verantworte ich das Monatsreporting. "
    "Dort habe ich die Planung auf LucaNet umgestellt. "
    "Außerdem betreue ich zwei Werkstudierende."
)
# The NovaPay opener of build 3 (in the middle, then once sentence-initial): no tell.
OPENER = (
    "I am applying for the Senior Backend Engineer role at NovaPay GmbH. "
    "NovaPay’s checkout and payout rails interest me."
)
EMP = ["Schwarzwald Präzision GmbH"]


def test_the_anchored_shape_fires_and_names_paragraph_and_count():
    hits = employer_openings([SALUTATION, BACK_REFERENCED, ANCHORED], EMP)
    # the salutation is not a body paragraph; the legal-form-less "Schwarzwald
    # Präzision" counts as the same employer
    assert hits == [(2, "Schwarzwald Präzision GmbH", 3)]


def test_one_anchor_per_paragraph_is_the_bar_not_a_finding():
    assert employer_openings([SALUTATION, BACK_REFERENCED, BACK_REFERENCED], EMP) == []


def test_a_mention_inside_the_sentence_is_not_an_opening():
    assert employer_openings([OPENER], ["NovaPay GmbH"]) == []
    late = (
        "In meiner Zeit als Controllerin bei der Schwarzwald Präzision GmbH stellte ich um. "
        "Bei der Schwarzwald Präzision GmbH berichte ich an die Geschäftsführung."
    )
    # five words precede the first mention — only the second sentence opens on it
    assert employer_openings([late], EMP) == []


def test_two_employers_are_counted_apart():
    mixed = (
        "Bei der Alpha GmbH führte ich das Team. Bei der Beta AG baute ich das Reporting auf. "
        "Bei der Alpha GmbH senkte ich die Kosten."
    )
    assert employer_openings([mixed], ["Alpha GmbH", "Beta AG"]) == [(1, "Alpha GmbH", 2)]


def _report(employer_names):
    letter = {
        "header": {}, "recipient": {"company": "Arnold Antriebstechnik GmbH"},
        "body": {"paragraphs": [SALUTATION, ANCHORED]},
    }
    text = " ".join(letter["body"]["paragraphs"])
    return _audit_letter_text(text, letter, [], employer_names=employer_names)


def test_the_audit_emits_an_advisory_row_with_locale_neutral_params():
    check = next(c for c in _report(EMP).checks if c.id == EMPLOYER_REPETITION_CHECK_ID)
    assert check.status == "pass"  # advisory: the person decides, nothing blocks
    assert check.details_key == EMPLOYER_REPETITION_CHECK_ID
    assert check.details_params == {
        "employer": "Schwarzwald Präzision GmbH", "paragraph": 1, "count": 3, "more": 0,
    }


def test_without_employer_names_the_report_is_unchanged():
    assert all(c.id != EMPLOYER_REPETITION_CHECK_ID for c in _report(None).checks)


def test_the_recipient_counts_as_an_employer_too():
    letter = {
        "header": {}, "recipient": {"company": "NovaPay GmbH"},
        "body": {"paragraphs": ["NovaPay baut Zahlungswege. NovaPay sucht Backend-Leute. Ich passe dazu."]},
    }
    report = _audit_letter_text(" ".join(letter["body"]["paragraphs"]), letter, [], employer_names=[])
    check = next(c for c in report.checks if c.id == EMPLOYER_REPETITION_CHECK_ID)
    assert check.details_params["employer"] == "NovaPay GmbH"


def test_profile_employer_names_reads_work_experience_once_each():
    pj = {"work_experience": [{"company": "A GmbH"}, {"company": " A GmbH "}, {"company": None}, {}, "x"]}
    assert profile_employer_names(pj) == ["A GmbH"]
    assert profile_employer_names(None) == []


def test_the_letter_audit_door_passes_the_profile_employers():
    from applire.services import cover_letter

    src = inspect.getsource(cover_letter)
    i = src.index("cl.ats_report = audit_cover_letter(")
    assert "employer_names=profile_employer_names(" in src[i : i + 2500]
