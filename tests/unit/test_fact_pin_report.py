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

"""ADR-077 clauses 3 + 5 — the pin report on the ATS audit.

Presence is measured INSIDE `_audit_cv_text` against the override-applied
tailored data (the single seam all three `_update_ats_report` doors share —
SF-PIN.5), and the page-length check's fail bands gain a structured `driver`
naming the pin count. The bands themselves (incl. #238) stay untouched.
"""

import uuid

from applire.schemas.application import FactPin
from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _audit_cv_text

ACHIEVEMENT = "Cut deployment time by 70% across 12 teams"
ENTRY_ID = "w1"


def _pin(quote=ACHIEVEMENT, stale=False, targets=None) -> FactPin:
    return FactPin(
        pin_id=str(uuid.uuid4()),
        entry_type="work",
        entry_id=ENTRY_ID,
        quote=quote,
        targets=targets or ["cv", "letter"],
        stale=stale,
    )


def _tailored(bullets) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "X"},
        "work_history": [{
            "id": ENTRY_ID, "company": "Acme", "role": "Lead",
            "start_date": "2020-01", "bullets": list(bullets),
        }],
        "skills": [],
    })


def _audit(tailored, *, pins, page_count=2, target=2, exhausted=False):
    return _audit_cv_text(
        "some rendered text",
        tailored,
        [],
        None,
        page_count=page_count,
        target=target,
        region="DACH",
        condensation_exhausted=exhausted,
        pins=pins,
    )


def test_report_carries_presence_per_pin():
    report = _audit(
        _tailored([f"Delivered: {ACHIEVEMENT}"]),
        pins=[_pin(), _pin(quote="Nowhere to be found fact")],
    )
    by_quote = {e.quote: e for e in report.pinned_facts}
    assert by_quote[ACHIEVEMENT].present is True
    assert by_quote["Nowhere to be found fact"].present is False


def test_stale_and_letter_only_pins_are_reported_not_measured_present():
    report = _audit(
        _tailored([f"Delivered: {ACHIEVEMENT}"]),
        pins=[_pin(stale=True), _pin(quote="Letter fact", targets=["letter"])],
    )
    # stale pin: surfaced (never silently dropped), present=False by exclusion
    stale_entry = next(e for e in report.pinned_facts if e.stale)
    assert stale_entry.present is False
    # letter-only pin does not appear on the CV report
    assert all(e.quote != "Letter fact" for e in report.pinned_facts)


def test_no_pins_leaves_the_report_shape_unchanged():
    report = _audit(_tailored(["Bullet"]), pins=[])
    assert report.pinned_facts is None
    page = next(c for c in report.checks if c.id == "page-length")
    assert page.driver is None


def test_failed_page_length_band_carries_the_pin_driver():
    # Over the explicit target (the #238 band) with a present pin → driver
    # names the count; the band itself is unchanged (still a fail).
    report = _audit(
        _tailored([f"Delivered: {ACHIEVEMENT}"]),
        pins=[_pin()],
        page_count=3,
        target=2,
    )
    page = next(c for c in report.checks if c.id == "page-length")
    assert page.status == "fail"
    assert page.driver == {"pinned_facts": 1}


def test_passing_page_length_gets_no_driver():
    report = _audit(
        _tailored([f"Delivered: {ACHIEVEMENT}"]),
        pins=[_pin()],
        page_count=2,
        target=2,
    )
    page = next(c for c in report.checks if c.id == "page-length")
    assert page.status == "pass" and page.driver is None
