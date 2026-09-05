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

"""ADR-079 clause 4 — the page-length band is reported ``not_applicable`` WITH
its reason, never silently omitted.

**Why this changed a boundary.** E057 task 1.1 froze ``_audit_cv_text`` /
``_audit_letter_text`` so the audit would stay one implementation (ADR-066).
Building the ``.docx`` export showed the freeze and ADR-079 clause 4 were not
simultaneously satisfiable: the only behaviour reachable without touching them
— omitting ``page_count`` — makes the band *absent*, and an absent check is
invisible to ``passed``/``failed`` alike, so the report reads ``passed=N,
failed=0`` on a band that was never evaluated. That is the #634 shape: an
instrument's silence read as evidence about something it never examined.

The founder chose to amend the boundary rather than open a second construction
site for the check (PO 2026-09-01). One defaulted parameter, one place that
still decides what a page-length row looks like — ADR-066's own property, and
what the freeze existed to protect.

A ``.docx`` has no pages until a word processor lays it out, so the band is
genuinely inapplicable there — not passing, not failing, and emphatically not
absent.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.cv import TailoredCVData  # noqa: E402
from applire.services.ats_audit import _audit_cv_text, _audit_letter_text  # noqa: E402

_CV = TailoredCVData.model_validate(
    {
        "contact": {"name": "Jörg Müller", "email": "joerg@example.de"},
        "summary": "Projektleiter mit Schwerpunkt Digitale Fertigung.",
        "work_history": [
            {
                "company": "Süddeutsche Präzisionstechnik GmbH",
                "role": "Teamleiter Qualitätssicherung",
                "start_date": "2018-03",
                "end_date": None,
                "bullets": ["Koordination mit Projekt Phoenix und R&D-Teams."],
            }
        ],
        "skills": ["Python"],
        "education": [],
    }
)
_CV_TEXT = (
    "Jörg Müller joerg@example.de Projektleiter mit Schwerpunkt Digitale Fertigung. "
    "Teamleiter Qualitätssicherung Süddeutsche Präzisionstechnik GmbH 2018 "
    "Koordination mit Projekt Phoenix und R&D-Teams. Python"
)

_LETTER = {
    "header": {"name": "Jörg Müller", "email": "joerg@example.de"},
    "recipient": {"company": "Nordwerk Systeme GmbH"},
    "body": {"paragraphs": ["In meiner aktuellen Rolle verantworte ich die Standardisierung."]},
    "signature": {"closing": "Mit freundlichen Grüßen", "name": "Jörg Müller"},
}
_LETTER_TEXT = (
    "Jörg Müller joerg@example.de Nordwerk Systeme GmbH "
    "In meiner aktuellen Rolle verantworte ich die Standardisierung. "
    "Mit freundlichen Grüßen Jörg Müller"
)


def _band(report):
    return next((c for c in report.checks if c.id == "page-length"), None)


# ---------------------------------------------------------------------------
# The band is PRESENT and explicitly not applicable — the clause-4 requirement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "audit,text,data",
    [(_audit_cv_text, _CV_TEXT, _CV), (_audit_letter_text, _LETTER_TEXT, _LETTER)],
    ids=["cv", "letter"],
)
def test_band_is_emitted_not_applicable_with_a_reason(audit, text, data):
    report = audit(text, data, keywords=[], page_band_not_applicable=True)

    band = _band(report)
    assert band is not None, "ADR-079 cl.4: the band must never be silently omitted"
    assert band.status == "not_applicable"
    assert band.details_key, "the reason must be machine-readable, not only EN prose"
    assert band.details, "the EN fallback must stay populated for the agent door"


@pytest.mark.parametrize(
    "audit,text,data",
    [(_audit_cv_text, _CV_TEXT, _CV), (_audit_letter_text, _LETTER_TEXT, _LETTER)],
    ids=["cv", "letter"],
)
def test_not_applicable_band_is_counted_in_its_own_bucket(audit, text, data):
    """It must reach neither the pass nor the fail numerator."""
    report = audit(text, data, keywords=[], page_band_not_applicable=True)

    # Scoped to the BAND (2026-09-04). This read `== 1` while the band was the only
    # producer of the status; ADR-039's 2026-09-04 amendment adds two always-present
    # checks that are `not_applicable` on a ledger-less, review-outcome-less fixture, so
    # a bare count would now assert something this test never meant. The invariant it
    # DOES mean — the band lands in its own bucket and in no other — is unchanged, and
    # the totals identity below still proves the buckets are exhaustive.
    assert report.not_applicable == sum(
        1 for c in report.checks if c.status == "not_applicable"
    )
    assert _band(report).status == "not_applicable"
    assert not any(c.status == "pass" and c.id == "page-length" for c in report.checks)
    assert not any(c.status == "fail" and c.id == "page-length" for c in report.checks)
    assert report.passed + report.failed + report.not_applicable == len(report.checks)


# ---------------------------------------------------------------------------
# The default must not move — every existing caller keeps today's behaviour
# ---------------------------------------------------------------------------


def test_default_is_unchanged_for_a_caller_supplying_a_page_count():
    """The parameter is additive: a real page count still produces a real band."""
    report = _audit_cv_text(_CV_TEXT, _CV, keywords=[], page_count=1)

    band = _band(report)
    assert band is not None and band.status == "pass"
    # See the note above: the assertion is about the BAND, not about the report's total.
    assert not any(
        c.status == "not_applicable" and c.id == "page-length" for c in report.checks
    )


def test_default_is_unchanged_for_a_caller_supplying_nothing():
    """Text-only callers (≈90 unit-test sites) keep the historical skip."""
    report = _audit_cv_text(_CV_TEXT, _CV, keywords=[])

    assert _band(report) is None
    assert not any(
        c.status == "not_applicable" and c.id == "page-length" for c in report.checks
    )


def test_not_applicable_wins_over_a_page_count_if_both_are_given():
    """A contradictory call must resolve deterministically, not by accident."""
    report = _audit_cv_text(
        _CV_TEXT, _CV, keywords=[], page_count=3, page_band_not_applicable=True
    )

    band = _band(report)
    assert band is not None and band.status == "not_applicable"
