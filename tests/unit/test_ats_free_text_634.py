# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""ADR-039 amendment (2026-08-31, #634) — the audit verifies the candidate's
own prose, and both document kinds use the same predicate.

Before this amendment the two halves of one responsibility had diverged:

* ``_audit_cv_text`` compared **no** free-text field against the extracted PDF.
  On the #634 reproduction it ran eight checks and passed all of them on a CV
  that had silently lost content.
* ``_audit_letter_text`` did check prose — but only ``probe = p[:60]``. The
  boundary was exact and template-independent: a loss at character offset ≤ 59
  failed ``body-0``, a loss at ≥ 60 passed.

Now the summary and every bullet on the CV side, and every full paragraph on
the letter side, are verified verbatim against the extracted text with the one
``_norm``/``_find`` predicate the structured checks already use (ADR-066 — one
predicate, not two).

These are text-level tests: they drive the audit's real entry points with the
extracted-text string, so they need no renderer and run in every CI job.
``tests/ats/test_autoescape_634.py`` covers the same contract through the real
Chromium render, where Chromium is available.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.cv import TailoredCVData  # noqa: E402
from applire.services.ats_audit import _audit_cv_text, _audit_letter_text  # noqa: E402

SUMMARY = "Projektleiter mit Schwerpunkt Digitale Fertigung und Verantwortung für Standardisierung."
ROLE_BULLET = "Koordination mit Projekt Phoenix und R&D-Teams beim Rollout."
NESTED_BULLET = "Aufbau einer statistischen Prozesslenkung für die Serienfertigung."
STANDALONE_BULLET = "Veröffentlichung eines Messdaten-Toolkits unter freier Lizenz."

CV = TailoredCVData.model_validate(
    {
        "contact": {"name": "Jörg Müller", "email": "joerg@example.de"},
        "summary": SUMMARY,
        "work_history": [
            {
                "company": "Süddeutsche Präzisionstechnik GmbH",
                "role": "Teamleiter Qualitätssicherung",
                "start_date": "2018-03",
                "end_date": None,
                "bullets": [ROLE_BULLET],
                "projects": [
                    {"name": "Projekt Nullfehler", "bullets": [NESTED_BULLET]}
                ],
            }
        ],
        "skills": ["Python"],
        "education": [
            {
                "institution": "Technische Universität München",
                "degree": "Dipl.-Ing.",
                "field": "Maschinenbau",
                "start_date": "2006-10",
                "end_date": "2011-03",
            }
        ],
        "projects": [
            {"name": "Messdaten-Toolkit", "bullets": [STANDALONE_BULLET]}
        ],
    }
)

# Everything the audit's pre-existing checks need, so a failure below is always
# about free text and never about a missing structured field.
STRUCTURED = (
    "Jörg Müller joerg@example.de "
    "Teamleiter Qualitätssicherung Süddeutsche Präzisionstechnik GmbH 2018 "
    "Projekt Nullfehler Messdaten-Toolkit "
    "Technische Universität München Dipl.-Ing. Maschinenbau Python "
)

ALL_FREE_TEXT = " ".join([SUMMARY, ROLE_BULLET, NESTED_BULLET, STANDALONE_BULLET])


def _failed(report) -> list[str]:
    return [c.id for c in report.checks if c.status == "fail"]


def _content_checks(report) -> list:
    return [c for c in report.checks if c.id.startswith("content-")]


# ---------------------------------------------------------------------------
# CV side — the half that verified nothing
# ---------------------------------------------------------------------------


def test_intact_cv_prose_produces_no_failure():
    """The floor: a document whose prose survived must not be flagged."""
    report = _audit_cv_text(STRUCTURED + ALL_FREE_TEXT, CV, keywords=[])

    assert _failed(report) == []
    assert len(_content_checks(report)) == 4, "summary + 3 bullets must each be checked"


@pytest.mark.parametrize(
    "dropped,label",
    [
        (ROLE_BULLET, "a work-entry bullet"),
        (SUMMARY, "the summary"),
        (NESTED_BULLET, "a nested project bullet"),
        (STANDALONE_BULLET, "a standalone project bullet"),
    ],
)
def test_missing_free_text_fails_the_audit(dropped, label):
    """#634's class: text present in the data, absent from the delivered document.

    Before the amendment every one of these produced a clean report.
    """
    text = STRUCTURED + ALL_FREE_TEXT.replace(dropped, "")
    report = _audit_cv_text(text, CV, keywords=[])

    failures = _failed(report)
    assert failures, f"{label} vanished and the audit stayed silent"
    assert all(f.startswith("content-") for f in failures), (
        f"{label}: the failure must be attributed to free text, got {failures}"
    )


def test_partial_loss_inside_a_bullet_fails():
    """The reported shape: a phrase disappears and the sentence stays grammatical."""
    mangled = ROLE_BULLET.replace("Projekt Phoenix ", "")
    report = _audit_cv_text(
        STRUCTURED + ALL_FREE_TEXT.replace(ROLE_BULLET, mangled), CV, keywords=[]
    )

    assert _failed(report), "a phrase dropped mid-bullet must not pass"


def test_blank_bullet_is_not_checked():
    """Mirrors the letter side's empty-paragraph guard: nothing to verify, no check."""
    cv = CV.model_copy(deep=True)
    cv.work_history[0].bullets = ["   "]
    # Everything else this CV carries is present; only the blank bullet is gone.
    text = STRUCTURED + " ".join([SUMMARY, NESTED_BULLET, STANDALONE_BULLET])
    report = _audit_cv_text(text, cv, keywords=[])

    assert _failed(report) == []
    assert len(_content_checks(report)) == 3, "the blank bullet must not become a check"


# ---------------------------------------------------------------------------
# Letter side — the 60-character probe, converged onto the same predicate
# ---------------------------------------------------------------------------

LETTER_PARAGRAPH = (
    "In meiner aktuellen Rolle verantworte ich die Standardisierung der "
    "Fertigungsprozesse und darueber hinaus Projekt Phoenix sowie die "
    "Zusammenarbeit mit den R&D-Teams beider Standorte."
)
LETTER = {
    "header": {"name": "Jörg Müller", "email": "joerg@example.de"},
    "recipient": {"company": "Nordwerk Systeme GmbH"},
    "body": {"paragraphs": [LETTER_PARAGRAPH]},
    "signature": {"closing": "Mit freundlichen Grüßen", "name": "Jörg Müller"},
}
LETTER_STRUCTURED = "Jörg Müller joerg@example.de Nordwerk Systeme GmbH "


def test_letter_loss_past_the_old_60_character_probe_now_fails():
    """The convergence, stated as the case the old probe could not see.

    ``Projekt Phoenix`` sits at offset 105 of this paragraph — beyond
    ``p[:60]`` — so before the amendment this exact document passed.
    """
    assert LETTER_PARAGRAPH.index("Projekt Phoenix") > 60

    mangled = LETTER_PARAGRAPH.replace("Projekt Phoenix ", "")
    report = _audit_letter_text(LETTER_STRUCTURED + mangled, LETTER, keywords=[])

    assert "body-0" in _failed(report), (
        "a loss past character 60 must fail now that the probe is the full paragraph"
    )


def test_intact_letter_paragraph_still_passes():
    report = _audit_letter_text(LETTER_STRUCTURED + LETTER_PARAGRAPH, LETTER, keywords=[])

    assert _failed(report) == []
